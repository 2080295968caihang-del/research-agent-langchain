from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.note_service import InboxService

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


class InboxRequest(BaseModel):
    kind: str
    content: str = Field(min_length=1)
    project_id: UUID | None = None


class InboxResponse(BaseModel):
    item_id: UUID
    kind: str
    content: str
    project_id: UUID | None
    created_at: str


@router.post("", response_model=InboxResponse, status_code=201)
def capture(request: InboxRequest):
    from ..main import registry
    try:
        item = InboxService(registry).capture(request.kind, request.content, request.project_id)
    except KeyError as exc:
        raise HTTPException(404, "project not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return InboxResponse(item_id=item.item_id, kind=item.kind, content=item.content,
                         project_id=item.project_id, created_at=item.created_at.isoformat())
