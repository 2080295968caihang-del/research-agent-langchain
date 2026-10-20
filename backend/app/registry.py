"""Global registry backed by SQLite while project content remains file-backed."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select

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
        record = self.get(project_id)
        nodes = self.project_store.load_plan(record.root_path)
        now = _now()
        documents = [("project.json", record.name, f"{record.name}\n{record.goal}"),
                     ("plan.json", "Plan", json.dumps([node.to_dict() for node in nodes], ensure_ascii=False))]
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
