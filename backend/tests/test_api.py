import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4

from app.main import app
from app.registry import ProjectRegistry
from app.project_store import ProjectStore
from app.config import Settings


@pytest.fixture()
def client():
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_project_registration_uses_file_manifest_and_rejects_duplicate_root(client, tmp_path: Path, monkeypatch):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    local_registry = ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root))
    monkeypatch.setattr("app.main.registry", local_registry)
    root = projects_root / "project"
    manifest = local_registry.project_store.create(root, "Project", "Goal")
    first = client.post("/api/projects", json={"root_path": str(root)})
    assert first.status_code == 201
    assert first.json()["project_id"] == str(manifest.project_id)
    assert {"project_id", "name", "root_path", "status", "goal", "indexed_at", "document_count"} <= set(first.json())

    duplicate = client.post("/api/projects", json={"root_path": str(root)})
    assert duplicate.status_code == 409


def test_project_registration_rejects_missing_or_outside_root(client, tmp_path: Path, monkeypatch):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setattr("app.main.registry", ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root)))
    missing = client.post("/api/projects", json={"root_path": str(projects_root / "missing")})
    assert missing.status_code == 404
    outside = client.post("/api/projects", json={"root_path": str(tmp_path.parent / "outside")})
    assert outside.status_code in (404, 422)


def test_reindex_unknown_project_returns_404(client):
    response = client.post(f"/api/projects/{uuid4()}/reindex")
    assert response.status_code == 404


def test_project_registration_rejects_corrupt_or_incomplete_manifest(client, tmp_path: Path, monkeypatch):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setattr("app.main.registry", ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root)))
    corrupt = projects_root / "corrupt"
    corrupt.mkdir(parents=True)
    (corrupt / "project.json").write_text("{broken", encoding="utf-8")
    assert client.post("/api/projects", json={"root_path": str(corrupt)}).status_code == 422

    incomplete = projects_root / "incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "project.json").write_text('{"schema_version": 1}', encoding="utf-8")
    assert client.post("/api/projects", json={"root_path": str(incomplete)}).status_code == 422


def test_plan_api_supports_nested_nodes_and_maps_errors(client, tmp_path: Path, monkeypatch):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    local_registry = ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root))
    monkeypatch.setattr("app.main.registry", local_registry)
    manifest = local_registry.project_store.create(projects_root / "one", "One", "Goal")
    local_registry.register(manifest)
    base = f"/api/projects/{manifest.project_id}/plan"

    goal = client.post(base, json={"kind": "goal", "title": "Ship", "status": "待开始"})
    assert goal.status_code == 201
    goal_id = goal.json()["node_id"]
    task = client.post(base, json={"parent_id": goal_id, "kind": "task", "title": "Build", "status": "进行中", "due_date": "2026-11-01"})
    assert task.status_code == 201
    assert client.get(base).json()[1]["title"] == "Build"

    assert client.patch(f"{base}/{task.json()['node_id']}", json={"status": "已完成"}).json()["status"] == "已完成"
    assert client.post(f"{base}/{goal_id}/move", json={"new_parent_id": task.json()["node_id"], "position": 0}).status_code == 409
    assert client.post(base, json={"kind": "wrong", "title": "Bad", "status": "待开始"}).status_code == 422
    assert client.post(base, json={"kind": "task", "title": "Bad", "status": "待开始", "due_date": "bad-date"}).status_code == 422
    assert client.patch(f"{base}/{task.json()['node_id']}", json={"due_date": None}).json()["due_date"] is None
    assert client.get(f"/api/projects/{uuid4()}/plan").status_code == 404


def test_plan_api_maps_corrupt_ordering_to_conflict(client, tmp_path: Path, monkeypatch):
    projects_root = tmp_path / "projects"
    settings = Settings(projects_root=projects_root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    local_registry = ProjectRegistry(settings=settings, project_store=ProjectStore(projects_root))
    monkeypatch.setattr("app.main.registry", local_registry)
    manifest = local_registry.project_store.create(projects_root / "one", "One", "Goal")
    local_registry.register(manifest)
    path = manifest.root_path / "plan.json"
    path.write_text('{"schema_version": 1, "nodes": [{"node_id": "00000000-0000-0000-0000-000000000001", "parent_id": null, "kind": "goal", "title": "a", "status": "待开始", "position": 0}, {"node_id": "00000000-0000-0000-0000-000000000002", "parent_id": null, "kind": "goal", "title": "b", "status": "待开始", "position": 0}]}', encoding="utf-8")
    assert client.get(f"/api/projects/{manifest.project_id}/plan").status_code == 409
