import hashlib
import json
import stat
from pathlib import Path

from docx import Document

from app.config import Settings
from app.project_store import ProjectStore
from app.registry import ProjectRegistry
from app.services.import_service import ImportService


def _registry(tmp_path: Path, projects_root: Path) -> ProjectRegistry:
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    return ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root))


def _write_research_doc(path: Path) -> None:
    document = Document()
    document.add_heading("Research Agent", level=1)
    document.add_paragraph("项目目标：建立一个本机优先的研究工作流。")
    document.add_paragraph("技术栈：Python、FastAPI、React")
    document.add_heading("14 天开发计划", level=2)
    document.add_paragraph("Day 1 - 完成项目骨架")
    document.add_paragraph("验收清单", style="List Bullet")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "☐ 健康检查接口返回 200"
    document.save(path)


def test_import_copies_docx_records_provenance_and_seeds_explicit_nodes(tmp_path: Path):
    source = tmp_path / "Research Agent.docx"
    _write_research_doc(source)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    projects_root = tmp_path / "projects"
    store = ProjectStore(projects_root)
    registry = _registry(tmp_path, projects_root)

    result = ImportService(store, registry).import_docx(source, projects_root / "Research Agent")

    assert result.source_copy == projects_root / "Research Agent" / "source" / "Research Agent.docx"
    assert result.source_copy.read_bytes() == source.read_bytes()
    assert not (result.source_copy.stat().st_mode & stat.S_IWRITE)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    assert result.created_node_count >= 3
    nodes = store.load_plan(result.source_copy.parents[1])
    titles = {node.title for node in nodes}
    assert "建立一个本机优先的研究工作流。" in titles
    assert "14 天开发计划" in titles
    assert "Day 1 - 完成项目骨架" in titles
    assert "☐ 健康检查接口返回 200" in titles

    manifest = json.loads((result.source_copy.parents[1] / "project.json").read_text(encoding="utf-8"))
    assert manifest["original_source_path"] == str(source.resolve())
    assert manifest["source_sha256"] == original_hash
    assert registry.get(result.project_id).root_path == result.source_copy.parents[1]


def test_empty_docx_returns_warning_without_inventing_tasks(tmp_path: Path):
    source = tmp_path / "empty.docx"
    Document().save(source)
    projects_root = tmp_path / "projects"
    store = ProjectStore(projects_root)

    result = ImportService(store).import_docx(source, projects_root / "empty")

    assert result.created_node_count == 0
    assert result.warnings
    assert store.load_plan(projects_root / "empty") == []


def test_malformed_docx_returns_warning_without_inventing_tasks(tmp_path: Path):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not a docx")
    projects_root = tmp_path / "projects"
    store = ProjectStore(projects_root)

    result = ImportService(store).import_docx(source, projects_root / "broken")

    assert result.created_node_count == 0
    assert any("读取" in warning or "docx" in warning.lower() for warning in result.warnings)
    assert store.load_plan(projects_root / "broken") == []
