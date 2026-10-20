import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.domain import PlanNode
from app.project_store import ProjectStore


def test_create_builds_independent_project_tree(tmp_path: Path):
    projects_root = tmp_path / "projects"
    project_root = projects_root / "Research Agent"
    store = ProjectStore(projects_root=projects_root)

    manifest = store.create(project_root, "Research Agent", "Study a topic")

    assert manifest.root_path == project_root.resolve()
    assert manifest.name == "Research Agent"
    assert manifest.goal == "Study a topic"
    for relative in (
        "project.json",
        "plan.json",
        "source",
        "notes",
        "reports",
        "attachments",
        "exports",
    ):
        assert (project_root / relative).exists()

    project_json = (project_root / "project.json").read_text(encoding="utf-8")
    plan_json = (project_root / "plan.json").read_text(encoding="utf-8")
    assert json.loads(project_json)["schema_version"] == 1
    assert json.loads(plan_json)["schema_version"] == 1


def test_manifest_and_plan_round_trip(tmp_path: Path):
    store = ProjectStore(projects_root=tmp_path)
    root = tmp_path / "project"
    created = store.create(root, "Project", "Goal")
    loaded = store.load(root)

    assert loaded.project_id == created.project_id
    assert loaded.root_path == root.resolve()

    node = PlanNode(
        node_id=uuid4(),
        parent_id=None,
        kind="goal",
        title="First goal",
        status="待开始",
    )
    store.save_plan(root, [node])
    assert store.load_plan(root) == [node]


def test_write_note_uses_utf8_markdown_and_does_not_touch_source(tmp_path: Path):
    store = ProjectStore(projects_root=tmp_path)
    root = tmp_path / "project"
    store.create(root, "Project", "Goal")
    source = root / "source" / "original.txt"
    source.write_text("keep this file", encoding="utf-8")

    note_id = UUID("11111111-1111-1111-1111-111111111111")
    note_path = store.write_note(root, note_id, "My note", "正文内容")

    assert note_path == root / "notes" / f"{note_id}.md"
    assert note_path.read_text(encoding="utf-8") == "# My note\n\n正文内容\n"
    assert source.read_text(encoding="utf-8") == "keep this file"


def test_paths_outside_configured_projects_root_are_rejected(tmp_path: Path):
    store = ProjectStore(projects_root=tmp_path / "projects")

    with pytest.raises(ValueError, match="projects root"):
        store.create(tmp_path / "outside", "Outside", "No")

