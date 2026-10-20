"""Persistent file contract for independent project directories."""

import json
from pathlib import Path
from uuid import UUID, uuid4

from .config import Settings
from .domain import PlanNode, ProjectManifest


SCHEMA_VERSION = 1
PROJECT_DIRECTORIES = ("source", "notes", "reports", "attachments", "exports")


class ProjectStore:
    def __init__(self, projects_root: Path | None = None):
        self.projects_root = (projects_root or Settings.from_env().projects_root).expanduser().resolve()

    def _validate_root(self, root: Path) -> Path:
        candidate = Path(root).expanduser().resolve()
        try:
            candidate.relative_to(self.projects_root)
        except ValueError as exc:
            raise ValueError(f"project path must be inside configured projects root: {self.projects_root}") from exc
        return candidate

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def create(self, root: Path, name: str, goal: str) -> ProjectManifest:
        project_root = self._validate_root(root)
        project_root.mkdir(parents=True, exist_ok=True)
        manifest_path = project_root / "project.json"
        if manifest_path.exists():
            raise FileExistsError(f"project already exists: {project_root}")
        for directory in PROJECT_DIRECTORIES:
            (project_root / directory).mkdir(exist_ok=True)
        manifest = ProjectManifest(uuid4(), name, project_root, "active", goal)
        self._write_json(
            manifest_path,
            {
                "schema_version": SCHEMA_VERSION,
                "project_id": str(manifest.project_id),
                "name": manifest.name,
                "root_path": str(manifest.root_path),
                "status": manifest.status,
                "goal": manifest.goal,
            },
        )
        self._write_json(project_root / "plan.json", {"schema_version": SCHEMA_VERSION, "nodes": []})
        return manifest

    def load(self, root: Path) -> ProjectManifest:
        project_root = self._validate_root(root)
        path = project_root / "project.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported project schema version: {data.get('schema_version')}")
        manifest_root = Path(data["root_path"]).expanduser().resolve()
        if manifest_root != project_root:
            raise ValueError("manifest root_path does not match requested project root")
        return ProjectManifest(
            project_id=UUID(str(data["project_id"])),
            name=str(data["name"]),
            root_path=manifest_root,
            status=str(data["status"]),
            goal=str(data["goal"]),
        )

    def save_plan(self, root: Path, nodes: list[PlanNode]) -> None:
        project_root = self._validate_root(root)
        if not project_root.is_dir():
            raise FileNotFoundError(project_root)
        self._write_json(
            project_root / "plan.json",
            {"schema_version": SCHEMA_VERSION, "nodes": [node.to_dict() for node in nodes]},
        )

    def load_plan(self, root: Path) -> list[PlanNode]:
        project_root = self._validate_root(root)
        path = project_root / "plan.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported plan schema version: {data.get('schema_version')}")
        return [PlanNode.from_dict(item) for item in data.get("nodes", [])]

    def write_note(self, root: Path, note_id: UUID, title: str, body: str) -> Path:
        project_root = self._validate_root(root)
        notes_dir = project_root / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_path = notes_dir / f"{note_id}.md"
        note_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        return note_path
