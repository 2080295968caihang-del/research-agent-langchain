"""Persistent file contract for independent project directories."""

import json
import re
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
        if candidate == self.projects_root:
            raise ValueError("project root cannot be the configured projects root")
        try:
            candidate.relative_to(self.projects_root)
        except ValueError as exc:
            raise ValueError(f"project path must be inside configured projects root: {self.projects_root}") from exc
        return candidate

    @staticmethod
    def _ensure_inside(path: Path, parent: Path, label: str = "path") -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(parent.resolve())
        except ValueError as exc:
            raise ValueError(f"{label} must remain inside project root") from exc
        return resolved

    def _require_project(self, root: Path) -> Path:
        project_root = self._validate_root(root)
        manifest_path = project_root / "project.json"
        if not project_root.is_dir() or not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        self.load(project_root)
        return project_root

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
            directory_path = self._ensure_inside(project_root / directory, project_root, "project directory")
            directory_path.mkdir(exist_ok=True)
        manifest = ProjectManifest(uuid4(), name, project_root, "active", goal)
        self._write_json(
            self._ensure_inside(manifest_path, project_root, "manifest"),
            {
                "schema_version": SCHEMA_VERSION,
                "project_id": str(manifest.project_id),
                "name": manifest.name,
                "root_path": str(manifest.root_path),
                "status": manifest.status,
                "goal": manifest.goal,
            },
        )
        self._write_json(
            self._ensure_inside(project_root / "plan.json", project_root, "plan"),
            {"schema_version": SCHEMA_VERSION, "nodes": []},
        )
        return manifest

    def load(self, root: Path) -> ProjectManifest:
        project_root = self._validate_root(root)
        path = self._ensure_inside(project_root / "project.json", project_root, "manifest")
        if not path.is_file():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported project schema version: {data.get('schema_version')}")
        manifest_root_raw = Path(data["root_path"]).expanduser()
        if not manifest_root_raw.is_absolute():
            raise ValueError("manifest root_path must be absolute")
        manifest_root = manifest_root_raw.resolve()
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
        project_root = self._require_project(root)
        self._write_json(
            self._ensure_inside(project_root / "plan.json", project_root, "plan"),
            {"schema_version": SCHEMA_VERSION, "nodes": [node.to_dict() for node in nodes]},
        )

    def load_plan(self, root: Path) -> list[PlanNode]:
        project_root = self._require_project(root)
        path = self._ensure_inside(project_root / "plan.json", project_root, "plan")
        if not path.is_file():
            raise FileNotFoundError(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"unsupported plan schema version: {data.get('schema_version')}")
        return [PlanNode.from_dict(item) for item in data.get("nodes", [])]

    def write_note(self, root: Path, note_id: UUID | str, title: str, body: str) -> Path:
        project_root = self._require_project(root)
        try:
            normalized_id = UUID(str(note_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError("note_id must be a valid UUID") from exc
        notes_dir = self._ensure_inside(project_root / "notes", project_root, "notes directory")
        notes_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^\w\s-]", "", str(title), flags=re.UNICODE).lower()
        slug = re.sub(r"[-\s]+", "-", slug).strip("-._")[:80] or "note"
        note_path = self._ensure_inside(notes_dir / f"{normalized_id}-{slug}.md", notes_dir, "note")
        note_path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
        return note_path
