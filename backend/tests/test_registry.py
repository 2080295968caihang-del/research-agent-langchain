import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import Settings
from app.domain import PlanNode
from app.project_store import ProjectStore
from app.registry import ProjectRegistry


def make_registry(tmp_path: Path) -> tuple[ProjectRegistry, ProjectStore, Path]:
    projects_root = tmp_path / "projects"
    db_path = tmp_path / "app.db"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{db_path}")
    store = ProjectStore(projects_root=projects_root)
    return ProjectRegistry(settings=settings, project_store=store), store, projects_root


def test_register_list_and_duplicate_root_rejection(tmp_path: Path):
    registry, store, projects_root = make_registry(tmp_path)
    manifest = store.create(projects_root / "one", "One", "Goal")

    record = registry.register(manifest)
    assert record.project_id == manifest.project_id
    assert registry.get(manifest.project_id).root_path == manifest.root_path
    assert [item.project_id for item in registry.list()] == [manifest.project_id]

    with pytest.raises(ValueError, match="root"):
        registry.register(manifest)


def test_listing_survives_registry_restart(tmp_path: Path):
    registry, store, projects_root = make_registry(tmp_path)
    manifest = store.create(projects_root / "one", "One", "Goal")
    registry.register(manifest)

    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    restarted = ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root))
    assert restarted.get(manifest.project_id).name == "One"


def test_reindex_reads_changed_plan_json(tmp_path: Path):
    registry, store, projects_root = make_registry(tmp_path)
    manifest = store.create(projects_root / "one", "One", "Goal")
    registry.register(manifest)
    node = PlanNode(uuid4(), None, "task", "Changed", "待开始")
    store.save_plan(manifest.root_path, [node])

    result = registry.reindex(manifest.project_id)
    assert result.project_id == manifest.project_id
    assert result.document_count >= 1
    assert registry.get(manifest.project_id).indexed_at is not None


def test_unassigned_inbox_item_is_stored(tmp_path: Path):
    registry, _, _ = make_registry(tmp_path)
    item = registry.add_inbox("note", "capture this", None)
    assert item.kind == "note"
    assert item.content == "capture this"
    assert item.project_id is None
