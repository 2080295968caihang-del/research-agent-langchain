"""Safe import of a Research Agent DOCX into an independent project folder."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from docx import Document

from ..domain import PlanNode
from ..project_store import ProjectStore
from ..registry import ProjectRegistry


@dataclass(frozen=True)
class ImportResult:
    project_id: UUID
    source_copy: Path
    created_node_count: int
    warnings: list[str]


class ImportService:
    """Import a DOCX without mutating the source file or inventing plan items."""

    def __init__(self, project_store: ProjectStore, registry: ProjectRegistry | None = None):
        self.project_store = project_store
        self.registry = registry

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _doc_text(document: Document) -> tuple[list[str], list[str]]:
        paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        table_cells = []
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text:
                        table_cells.append(text)
        return paragraphs, table_cells

    @staticmethod
    def _goal(paragraphs: list[str]) -> str | None:
        # Require an explicit project-goal label so a later "目标：" line from
        # a daily task cannot become the project's top-level goal.
        pattern = re.compile(
            r"^[\s«»\"“”‘’【】\[\](））(（]*"
            r"(?:项目目标|项目目的|project\s+goal)\s*[:：]\s*(.+)$",
            re.IGNORECASE,
        )
        for text in paragraphs:
            match = pattern.match(text)
            if match:
                return match.group(1).strip()
        return None

    @staticmethod
    def _is_checklist(text: str) -> bool:
        return bool(re.match(r"^(?:[-*•]\s*)?(?:☐|☑|□|\[\s*[xX]?\s*\])\s*.+", text))

    @staticmethod
    def _is_day_task(text: str) -> bool:
        return bool(re.match(r"^(?:第\s*\d+\s*天|day\s*\d+)\b\s*[:：-]?\s*.+", text, re.IGNORECASE))

    @staticmethod
    def _is_plan_heading(text: str) -> bool:
        return bool(re.search(r"(?:\d+\s*天|\d+\s*日).*计划|development plan", text, re.IGNORECASE))

    @staticmethod
    def _is_checklist_heading(text: str) -> bool:
        return text in {"验收清单", "检查清单", "checklist"}

    def _extract_nodes(self, paragraphs: list[str], table_cells: list[str]) -> tuple[list[PlanNode], list[str]]:
        warnings: list[str] = []
        nodes: list[PlanNode] = []
        goal_text = self._goal(paragraphs)
        goal_id = None
        if goal_text:
            goal_id = uuid4()
            nodes.append(PlanNode(goal_id, None, "goal", goal_text, "待开始", position=0))
        else:
            warnings.append("文档未明确提供项目目标，未创建目标节点")

        current_parent = goal_id
        sibling_positions: dict[UUID | None, int] = {goal_id: 0}
        for text in paragraphs:
            if self._is_plan_heading(text) or self._is_checklist_heading(text):
                node_id = uuid4()
                position = sibling_positions.get(current_parent, 0)
                nodes.append(PlanNode(node_id, current_parent, "phase", text, "待开始", position=position))
                sibling_positions[current_parent] = position + 1
                current_parent = node_id
            elif self._is_day_task(text):
                node_id = uuid4()
                position = sibling_positions.get(current_parent, 0)
                nodes.append(PlanNode(node_id, current_parent, "task", text, "待开始", position=position))
                sibling_positions[current_parent] = position + 1
            elif self._is_checklist(text):
                node_id = uuid4()
                position = sibling_positions.get(current_parent, 0)
                nodes.append(PlanNode(node_id, current_parent, "task", text, "待开始", position=position))
                sibling_positions[current_parent] = position + 1

        for text in table_cells:
            if self._is_checklist(text):
                node_id = uuid4()
                position = sibling_positions.get(current_parent, 0)
                nodes.append(PlanNode(node_id, current_parent, "task", text, "待开始", position=position))
                sibling_positions[current_parent] = position + 1

        if not nodes:
            warnings.append("文档没有可提取的目标、计划或清单节点")
        return nodes, warnings

    @staticmethod
    def _write_provenance(project_root: Path, source: Path, source_copy: Path, digest: str) -> None:
        manifest_path = project_root / "project.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        provenance = {
            "original_path": str(source),
            "sha256": digest,
            "copy_path": str(source_copy.relative_to(project_root)).replace("\\", "/"),
        }
        manifest.update({
            "original_source_path": str(source),
            "source_sha256": digest,
            "source_copy": provenance["copy_path"],
        })
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (project_root / "source" / "import.json").write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def import_docx(self, source: Path, project_root: Path) -> ImportResult:
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if source.suffix.lower() != ".docx":
            raise ValueError("source must be a .docx file")
        project_root = self.project_store._validate_root(Path(project_root))
        source_copy = project_root / "source" / "Research Agent.docx"
        if source_copy.resolve() == source:
            raise ValueError("source file cannot be the destination copy")
        # Validate the destination before creating the project. This rejects
        # external symlinks and collisions without leaving a partial project.
        self.project_store._ensure_inside(project_root / "source", project_root, "source directory")
        self.project_store._ensure_inside(source_copy, project_root / "source", "source copy")
        if os.path.lexists(source_copy):
            raise FileExistsError(f"source copy already exists: {source_copy}")
        source_digest = self._sha256(source)
        project_name = "Research Agent"
        warnings: list[str] = []

        paragraphs: list[str] = []
        table_cells: list[str] = []
        try:
            paragraphs, table_cells = self._doc_text(Document(source))
        except Exception as exc:  # malformed DOCX should be reported, not synthesized
            warnings.append(f"读取 DOCX 失败：{exc}")

        manifest = self.project_store.create(project_root, project_name, self._goal(paragraphs) or "")
        shutil.copy2(source, source_copy)
        self._write_provenance(project_root, source, source_copy, source_digest)
        # The imported artifact is evidence of the original input, so app writes never target it.
        os.chmod(source_copy, stat.S_IREAD)

        nodes: list[PlanNode] = []
        if not warnings and not paragraphs and not table_cells:
            warnings.append("DOCX 文档为空，未创建计划节点")
        elif not warnings:
            nodes, extraction_warnings = self._extract_nodes(paragraphs, table_cells)
            warnings.extend(extraction_warnings)

        if nodes:
            self.project_store.save_plan(project_root, nodes)
        if self.registry is not None:
            self.registry.register(manifest)
        return ImportResult(manifest.project_id, source_copy, len(nodes), warnings)
