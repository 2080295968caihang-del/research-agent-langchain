"""Markdown note persistence and inbox capture services."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from ..registry import InboxItem, ProjectRegistry


@dataclass(frozen=True)
class NoteRecord:
    note_id: UUID
    project_id: UUID
    relative_path: str
    title: str
    body: str
    note_type: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(title: str) -> str:
    value = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).lower()
    return re.sub(r"[-\s]+", "-", value).strip("-._")[:80] or "note"


def _frontmatter(note: NoteRecord) -> str:
    metadata = {
        "note_id": str(note.note_id), "project_id": str(note.project_id),
        "title": note.title, "type": note.note_type, "tags": note.tags,
        "created_at": note.created_at.isoformat(), "updated_at": note.updated_at.isoformat(),
    }
    return "---\n" + json.dumps(metadata, ensure_ascii=False, indent=2) + "\n---\n\n" + note.body.rstrip() + "\n"


def read_note_file(path: Path, project_id: UUID) -> NoteRecord:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"note lacks front matter: {path}")
    end = raw.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"note front matter is incomplete: {path}")
    metadata = json.loads(raw[4:end])
    note_id = UUID(str(metadata["note_id"]))
    if UUID(str(metadata["project_id"])) != project_id:
        raise ValueError(f"note project mismatch: {path}")
    body = raw[end + 6:]
    if body.endswith("\n"):
        body = body[:-1]
    return NoteRecord(note_id, project_id, path.relative_to(path.parents[1]).as_posix(),
                      str(metadata["title"]), body, str(metadata.get("type", "note")),
                      [str(tag) for tag in metadata.get("tags", [])],
                      datetime.fromisoformat(metadata["created_at"]), datetime.fromisoformat(metadata["updated_at"]))


class NoteService:
    def __init__(self, registry: ProjectRegistry):
        self.registry = registry

    def _record(self, project_id: UUID, note_id: UUID) -> tuple[NoteRecord, Path]:
        project = self.registry.get(project_id)
        notes_dir = self.registry.project_store._ensure_inside(project.root_path / "notes", project.root_path, "notes directory")
        for path in notes_dir.glob(f"{note_id}-*.md"):
            path = self.registry.project_store._ensure_inside(path, notes_dir, "note")
            return read_note_file(path, project_id), path
        raise KeyError(note_id)

    def create(self, project_id: UUID, title: str, body: str, note_type: str = "note", tags: list[str] | None = None) -> NoteRecord:
        project = self.registry.get(project_id)
        now = _now()
        note_id = uuid4()
        note = NoteRecord(note_id, project_id, f"notes/{note_id}-{_slug(title)}.md", title, body, note_type, tags or [], now, now)
        path = self.registry.project_store.write_note(project.root_path, note.note_id, title, body)
        path.write_text(_frontmatter(note), encoding="utf-8")
        self.registry.index_document(project_id, note.relative_path, title, body)
        return note

    def read(self, project_id: UUID, note_id: UUID) -> NoteRecord:
        return self._record(project_id, note_id)[0]

    def list(self, project_id: UUID) -> list[NoteRecord]:
        project = self.registry.get(project_id)
        notes_dir = self.registry.project_store._ensure_inside(project.root_path / "notes", project.root_path, "notes directory")
        paths = [self.registry.project_store._ensure_inside(path, notes_dir, "note") for path in sorted(notes_dir.glob("*.md"))]
        return [read_note_file(path, project_id) for path in paths]

    def search(self, query: str, project_id: UUID | None = None):
        return self.registry.search(query, project_id)


class InboxService:
    ALLOWED = {"task", "idea", "question", "note", "research"}

    def __init__(self, registry: ProjectRegistry):
        self.registry = registry

    def capture(self, kind: str, content: str, project_id: UUID | None) -> InboxItem:
        if kind not in self.ALLOWED:
            raise ValueError(f"unsupported inbox kind: {kind}")
        if project_id is not None:
            self.registry.get(project_id)
        return self.registry.add_inbox(kind, content, project_id)
