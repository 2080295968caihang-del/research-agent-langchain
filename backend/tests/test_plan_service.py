import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.project_store import ProjectStore
from app.registry import ProjectRegistry
from app.services.plan_service import PlanNodePatch, PlanService


def make_service(tmp_path: Path):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    store = ProjectStore(projects_root)
    registry = ProjectRegistry(settings=settings, project_store=store)
    manifest = store.create(projects_root / "one", "One", "Goal")
    registry.register(manifest)
    return PlanService(registry), store, manifest


def test_creates_ordered_nested_plan_and_persists_it(tmp_path: Path):
    service, store, manifest = make_service(tmp_path)
    goal = service.create_node(manifest.project_id, None, "goal", "Ship", "待开始", None)
    milestone = service.create_node(manifest.project_id, goal.node_id, "milestone", "Beta", "进行中", date(2026, 11, 1))
    task = service.create_node(manifest.project_id, milestone.node_id, "task", "Build", "已完成", None)

    tree = service.list_tree(manifest.project_id)
    assert [node.node_id for node in tree] == [goal.node_id, milestone.node_id, task.node_id]
    assert [node.position for node in tree] == [0, 0, 0]
    saved = json.loads((manifest.root_path / "plan.json").read_text(encoding="utf-8"))
    assert [item["node_id"] for item in saved["nodes"]] == [str(goal.node_id), str(milestone.node_id), str(task.node_id)]
    assert saved["nodes"][2]["status"] == "已完成"
    assert store.load_plan(manifest.root_path)[1].due_date == date(2026, 11, 1)


def test_sibling_positions_and_patch_are_persisted(tmp_path: Path):
    service, _, manifest = make_service(tmp_path)
    root = service.create_node(manifest.project_id, None, "goal", "Goal", "待开始", None)
    first = service.create_node(manifest.project_id, root.node_id, "task", "First", "待开始", None)
    second = service.create_node(manifest.project_id, root.node_id, "task", "Second", "待开始", None)
    service.move_node(manifest.project_id, second.node_id, root.node_id, 0)
    updated = service.update_node(manifest.project_id, first.node_id, PlanNodePatch(title="Done", status="已完成"))
    assert updated.title == "Done"
    assert [node.title for node in service.list_tree(manifest.project_id)] == ["Goal", "Second", "Done"]
    assert [node.position for node in service.list_tree(manifest.project_id)[1:]] == [0, 1]


def test_rejects_cycles_unknown_projects_and_invalid_nodes(tmp_path: Path):
    service, _, manifest = make_service(tmp_path)
    parent = service.create_node(manifest.project_id, None, "goal", "Parent", "待开始", None)
    child = service.create_node(manifest.project_id, parent.node_id, "task", "Child", "待开始", None)
    with pytest.raises(ValueError, match="descendant"):
        service.move_node(manifest.project_id, parent.node_id, child.node_id, 0)
    with pytest.raises(KeyError):
        service.list_tree(uuid4())
    with pytest.raises(KeyError):
        service.update_node(manifest.project_id, uuid4(), PlanNodePatch(title="missing"))
    with pytest.raises(ValueError):
        service.create_node(manifest.project_id, None, "invalid", "Bad", "待开始", None)
    with pytest.raises(ValueError):
        service.create_node(manifest.project_id, None, "task", "Bad", "未知", None)
