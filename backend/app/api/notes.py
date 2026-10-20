from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..services.note_service import NoteService

router = APIRouter(prefix="/api/projects/{project_id}/notes", tags=["notes"])


class NoteRequest(BaseModel):
    title: str = Field(min_length=1)
    body: str
    note_type: str = "note"
    tags: list[str] = []


class NoteResponse(BaseModel):
    note_id: UUID
    project_id: UUID
    relative_path: str
    title: str
    body: str
    note_type: str
    tags: list[str]
    created_at: str
    updated_at: str


def _response(note):
    return NoteResponse(note_id=note.note_id, project_id=note.project_id, relative_path=note.relative_path,
                        title=note.title, body=note.body, note_type=note.note_type, tags=note.tags,
                        created_at=note.created_at.isoformat(), updated_at=note.updated_at.isoformat())


def _service():
    from ..main import registry
    return NoteService(registry)


@router.post("", response_model=NoteResponse, status_code=201)
def create_note(project_id: UUID, request: NoteRequest):
    try:
        return _response(_service().create(project_id, request.title, request.body, request.note_type, request.tags))
    except KeyError as exc:
        raise HTTPException(404, "project not found") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("", response_model=list[NoteResponse])
def list_notes(project_id: UUID):
    try:
        return [_response(note) for note in _service().list(project_id)]
    except KeyError as exc:
        raise HTTPException(404, "project not found") from exc


@router.get("/{note_id}", response_model=NoteResponse)
def read_note(project_id: UUID, note_id: UUID):
    try:
        return _response(_service().read(project_id, note_id))
    except KeyError as exc:
        raise HTTPException(404, "note not found") from exc


search_router = APIRouter(prefix="/api", tags=["search"])


class SearchResponse(BaseModel):
    project_id: UUID
    relative_path: str
    title: str
    excerpt: str


@search_router.get("/search", response_model=list[SearchResponse])
def search(q: str = Query(min_length=1), project_id: UUID | None = None):
    from ..main import registry
    return [SearchResponse(project_id=item.project_id, relative_path=item.relative_path, title=item.title, excerpt=item.excerpt)
            for item in NoteService(registry).search(q, project_id)]
