"""File-backed domain objects used by the project workspace."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ProjectManifest:
    project_id: UUID
    name: str
    root_path: Path
    status: str
    goal: str


@dataclass(frozen=True)
class PlanNode:
    node_id: UUID
    parent_id: UUID | None
    kind: str
    title: str
    status: str
    due_date: date | None = None
    position: int = 0

    @property
    def id(self) -> UUID:
        """Compatibility alias for consumers that call the identifier ``id``."""
        return self.node_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": str(self.node_id),
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "due_date": self.due_date.isoformat() if self.due_date else None,
            "position": self.position,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanNode":
        raw_id = data.get("node_id", data.get("id"))
        if raw_id is None:
            raise ValueError("plan node is missing node_id")
        parent_id = data.get("parent_id")
        due_date = data.get("due_date")
        return cls(
            node_id=raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id)),
            parent_id=(parent_id if isinstance(parent_id, UUID) else UUID(str(parent_id)))
            if parent_id
            else None,
            kind=str(data["kind"]),
            title=str(data["title"]),
            status=str(data["status"]),
            due_date=date.fromisoformat(str(due_date)) if due_date else None,
            position=int(data.get("position", 0)),
        )
