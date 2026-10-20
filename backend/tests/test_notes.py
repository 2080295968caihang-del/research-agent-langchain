from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.project_store import ProjectStore
from app.registry import ProjectRegistry
from app.services.note_service import InboxService, NoteService


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch):
    root = tmp_path / "projects"
    settings = Settings(projects_root=root, database_url=f"sqlite:///{tmp_path / 'app.db'}")
    registry = ProjectRegistry(settings, ProjectStore(root))
    monkeypatch.setattr("app.main.registry", registry)
    manifest = registry.project_store.create(root / "one", "One", "Goal")
    registry.register(manifest)
    return registry, manifest, TestClient(app)


def test_note_markdown_round_trip_and_reindex(workspace):
    registry, manifest, _ = workspace
    service = NoteService(registry)
    note = service.create(manifest.project_id, "Research brief", "The orchid result is useful.", "research", ["botany", "draft"])
    path = manifest.root_path / note.relative_path
    assert path.is_file()
    markdown = path.read_text(encoding="utf-8")
    assert markdown.startswith("---\n")
    assert "orchid result" in markdown
    assert service.read(manifest.project_id, note.note_id) == note

    registry.reindex(manifest.project_id)
    restarted = NoteService(ProjectRegistry(registry.settings, ProjectStore(registry.settings.projects_root)))
    assert restarted.read(manifest.project_id, note.note_id) == note
    hit = next(result for result in restarted.search("orchid") if result.relative_path == note.relative_path)
    assert hit.project_id == manifest.project_id
    assert hit.title == "Research brief"
    assert "orchid" in hit.excerpt.lower()
    assert any(result.relative_path == note.relative_path for result in restarted.search("Research brief", manifest.project_id))
    assert restarted.search("orchid", uuid4()) == []


def test_inbox_unassigned_and_api_routes(workspace):
    registry, manifest, client = workspace
    item = InboxService(registry).capture("idea", "Compare methods", None)
    assert item.project_id is None
    assert registry.list_inbox()[0] == item

    base = f"/api/projects/{manifest.project_id}/notes"
    created = client.post(base, json={"title": "Field report", "body": "Found copper deposits", "note_type": "research", "tags": ["field"]})
    assert created.status_code == 201
    note_id = created.json()["note_id"]
    assert client.get(base).status_code == 200
    assert client.get(f"{base}/{note_id}").json()["body"] == "Found copper deposits"
    hit = client.get("/api/search", params={"q": "copper"}).json()[0]
    assert hit["project_id"] == str(manifest.project_id)
    assert hit["relative_path"].startswith("notes/")
    assert "copper" in hit["excerpt"].lower()
    capture = client.post("/api/inbox", json={"kind": "task", "content": "Review result"})
    assert capture.status_code == 201
    assert capture.json()["project_id"] is None
    assert client.post("/api/inbox", json={"kind": "invalid", "content": "Bad"}).status_code == 422
    assert client.post("/api/inbox", json={"kind": "idea", "content": "Bad", "project_id": str(uuid4())}).status_code == 404
