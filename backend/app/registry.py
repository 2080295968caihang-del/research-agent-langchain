"""Global registry backed by SQLite while project content remains file-backed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select, text

from .config import Settings
from .db import create_session_factory
from .domain import ProjectManifest
from .project_store import ProjectStore


@dataclass(frozen=True)
class ProjectRecord:
    project_id: UUID
    name: str
    root_path: Path
    status: str
    goal: str
    indexed_at: datetime | None = None
    document_count: int = 0


@dataclass(frozen=True)
class ReindexResult:
    project_id: UUID
    document_count: int


@dataclass(frozen=True)
class InboxItem:
    item_id: UUID
    kind: str
    content: str
    project_id: UUID | None
    created_at: datetime


@dataclass(frozen=True)
class SearchResult:
    project_id: UUID
    relative_path: str
    title: str
    excerpt: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProjectRegistry:
    def __init__(self, settings: Settings | None = None, project_store: ProjectStore | None = None):
        self.settings = settings or Settings.from_env()
        self.project_store = project_store or ProjectStore(self.settings.projects_root)
        self.session_factory = create_session_factory(self.settings)

    @staticmethod
    def _record(row) -> ProjectRecord:
        return ProjectRecord(
            project_id=UUID(row.project_id), name=row.name, root_path=Path(row.root_path),
            status=row.status, goal=row.goal,
            indexed_at=datetime.fromisoformat(row.indexed_at) if row.indexed_at else None,
            document_count=row.document_count,
        )

    def register(self, manifest: ProjectManifest) -> ProjectRecord:
        with self.session_factory() as session:
            existing = session.execute(select(ProjectRow).where(ProjectRow.root_path == str(manifest.root_path))).scalar_one_or_none()
            if existing:
                raise ValueError("project root is already registered")
            by_id = session.get(ProjectRow, str(manifest.project_id))
            if by_id:
                if by_id.root_path != str(manifest.root_path):
                    raise ValueError("project id is already registered with another root")
                return self._record(by_id)
            row = ProjectRow(
                project_id=str(manifest.project_id), name=manifest.name, root_path=str(manifest.root_path),
                status=manifest.status, goal=manifest.goal, document_count=0,
            )
            session.add(row)
            session.commit()
            return self._record(row)

    def list(self) -> list[ProjectRecord]:
        with self.session_factory() as session:
            return [self._record(row) for row in session.scalars(select(ProjectRow).order_by(ProjectRow.name))]

    def get(self, project_id: UUID) -> ProjectRecord:
        with self.session_factory() as session:
            row = session.get(ProjectRow, str(project_id))
            if row is None:
                raise KeyError(project_id)
            return self._record(row)

    def reindex(self, project_id: UUID) -> ReindexResult:
        from .services.note_service import read_note_file

        record = self.get(project_id)
        nodes = self.project_store.load_plan(record.root_path)
        now = _now()
        documents = [("project.json", record.name, f"{record.name}\n{record.goal}"),
                     ("plan.json", "Plan", json.dumps([node.to_dict() for node in nodes], ensure_ascii=False))]
        notes_dir = self.project_store._ensure_inside(record.root_path / "notes", record.root_path, "notes directory")
        for path in sorted(notes_dir.glob("*.md")):
            safe_path = self.project_store._ensure_inside(path, notes_dir, "note")
            note = read_note_file(safe_path, record.project_id)
            documents.append((note.relative_path, note.title, note.body))
        with self.session_factory() as session:
            session.execute(delete(SearchDocument).where(SearchDocument.project_id == str(project_id)))
            for source, title, content in documents:
                session.add(SearchDocument(project_id=str(project_id), source=source, title=title, content=content))
            row = session.get(ProjectRow, str(project_id))
            row.indexed_at = now.isoformat()
            row.document_count = len(documents)
            session.commit()
        return ReindexResult(project_id, len(documents))

    def add_inbox(self, kind: str, content: str, project_id: UUID | None) -> InboxItem:
        item = InboxItem(uuid4(), kind, content, project_id, _now())
        with self.session_factory() as session:
            session.add(InboxRow(item_id=str(item.item_id), kind=item.kind, content=item.content,
                                 project_id=str(project_id) if project_id else None, created_at=item.created_at.isoformat()))
            session.commit()
        return item

    def list_inbox(self) -> list[InboxItem]:
        with self.session_factory() as session:
            rows = session.scalars(select(InboxRow).order_by(InboxRow.created_at, InboxRow.item_id)).all()
            return [InboxItem(UUID(row.item_id), row.kind, row.content,
                              UUID(row.project_id) if row.project_id else None,
                              datetime.fromisoformat(row.created_at)) for row in rows]

    def index_document(self, project_id: UUID, source: str, title: str, content: str) -> None:
        with self.session_factory() as session:
            row = session.scalar(select(SearchDocument).where(
                SearchDocument.project_id == str(project_id), SearchDocument.source == source))
            if row is None:
                session.add(SearchDocument(project_id=str(project_id), source=source, title=title, content=content))
            else:
                row.title, row.content = title, content
            session.commit()

    def search(self, query: str, project_id: UUID | None = None) -> list[SearchResult]:
        import re

        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        match_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        sql = """SELECT d.project_id, d.source, d.title,
                 snippet(search_documents_fts, 1, '', '', ' ... ', 18) AS excerpt
                 FROM search_documents_fts JOIN search_documents d ON d.document_id = search_documents_fts.rowid
                 WHERE search_documents_fts MATCH :query"""
        params = {"query": match_query}
        if project_id is not None:
            sql += " AND d.project_id = :project_id"
            params["project_id"] = str(project_id)
        sql += " ORDER BY rank LIMIT 100"
        with self.session_factory() as session:
            rows = session.execute(text(sql), params).all()
        return [SearchResult(UUID(row.project_id), row.source, row.title, row.excerpt) for row in rows]


from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class ProjectRow(Base):
    __tablename__ = "projects"
    project_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    root_path = Column(String, unique=True, nullable=False)
    status = Column(String, nullable=False)
    goal = Column(Text, nullable=False)
    indexed_at = Column(String, nullable=True)
    document_count = Column(Integer, default=0, nullable=False)


class InboxRow(Base):
    __tablename__ = "inbox_items"
    item_id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    project_id = Column(String, nullable=True)
    created_at = Column(String, nullable=False)


class SearchDocument(Base):
    __tablename__ = "search_documents"
    document_id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(String, nullable=False)
    source = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
