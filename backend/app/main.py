from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import Settings
from .registry import ProjectRegistry


settings = Settings.from_env()
app = FastAPI(title="Private Project Workspace")
registry = ProjectRegistry(settings=settings)


class ProjectResponse(BaseModel):
    project_id: UUID
    name: str
    root_path: str
    status: str
    goal: str
    indexed_at: str | None = None
    document_count: int = 0


class RegisterProjectRequest(BaseModel):
    root_path: str


class ReindexResponse(BaseModel):
    project_id: UUID
    document_count: int


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _project_response(record) -> ProjectResponse:
    return ProjectResponse(
        project_id=record.project_id, name=record.name, root_path=str(record.root_path),
        status=record.status, goal=record.goal,
        indexed_at=record.indexed_at.isoformat() if record.indexed_at else None,
        document_count=record.document_count,
    )


@app.get("/api/projects", response_model=list[ProjectResponse])
def list_projects() -> list[ProjectResponse]:
    return [_project_response(record) for record in registry.list()]


@app.post("/api/projects", response_model=ProjectResponse, status_code=201)
def register_project(request: RegisterProjectRequest) -> ProjectResponse:
    from pathlib import Path

    try:
        manifest = registry.project_store.load(Path(request.root_path))
        record = registry.register(manifest)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project manifest not found") from exc
    except ValueError as exc:
        status_code = 409 if "already registered" in str(exc) else 422
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return _project_response(record)


@app.post("/api/projects/{project_id}/reindex", response_model=ReindexResponse)
def reindex_project(project_id: UUID) -> ReindexResponse:
    try:
        result = registry.reindex(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ReindexResponse(project_id=result.project_id, document_count=result.document_count)
