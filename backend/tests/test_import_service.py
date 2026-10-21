import hashlib
import json
import stat
from pathlib import Path

import pytest
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


def test_import_handles_project_goal_prefix_and_bulleted_checklists(tmp_path: Path):
    source = tmp_path / "Research Agent.docx"
    document = Document()
    document.add_paragraph("«项目目标：构建可运行的研究助手。")
    document.add_paragraph("14 天开发计划")
    document.add_paragraph("第 9 天：开发 Streamlit 界面")
    document.add_paragraph("- [ ] 创建研究问题输入框。")
    document.add_paragraph("目标：能在本地成功调用大模型。")
    document.save(source)

    projects_root = tmp_path / "projects"
    store = ProjectStore(projects_root)
    result = ImportService(store).import_docx(source, projects_root / "Research Agent")
    nodes = store.load_plan(projects_root / "Research Agent")
    by_title = {node.title: node for node in nodes}

    assert by_title["构建可运行的研究助手。"].kind == "goal"
    assert by_title["14 天开发计划"].kind == "phase"
    assert by_title["第 9 天：开发 Streamlit 界面"].kind == "task"
    assert by_title["- [ ] 创建研究问题输入框。"].kind == "task"
    assert "目标：能在本地成功调用大模型。" not in by_title
    assert result.created_node_count == 4


def test_import_rejects_existing_source_copy_before_creating_manifest(tmp_path: Path):
    source = tmp_path / "input.docx"
    _write_research_doc(source)
    project_root = tmp_path / "projects" / "Research Agent"
    (project_root / "source").mkdir(parents=True)
    destination = project_root / "source" / "Research Agent.docx"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        ImportService(ProjectStore(tmp_path / "projects")).import_docx(source, project_root)

    assert not (project_root / "project.json").exists()
    assert destination.read_bytes() == b"existing"
