from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain import PlanNode
from ..services.plan_service import PlanNodePatch, PlanService

router = APIRouter(prefix="/api/projects/{project_id}/plan", tags=["plan"])
Kind = Literal["goal", "phase", "milestone", "task", "subtask"]
Status = Literal["待开始", "进行中", "已完成", "阻塞", "已取消"]


class NodeRequest(BaseModel):
    parent_id: UUID | None = None
    kind: Kind
    title: str = Field(min_length=1)
    status: Status
    due_date: date | None = None


class NodePatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    kind: Kind | None = None
    status: Status | None = None
    due_date: date | None = None


class MoveRequest(BaseModel):
    new_parent_id: UUID | None = None
    position: int = Field(ge=0)


class NodeResponse(BaseModel):
    node_id: UUID
    parent_id: UUID | None
    kind: str
    title: str
    status: str
    due_date: date | None
    position: int


def _service() -> PlanService:
    from ..main import registry
    return PlanService(registry)


def _response(node: PlanNode) -> NodeResponse:
    return NodeResponse(**node.to_dict())


@router.get("", response_model=list[NodeResponse])
def list_plan(project_id: UUID):
    try:
        return [_response(node) for node in _service().list_tree(project_id)]
    except KeyError as exc:
        raise HTTPException(404, "project or node not found") from exc


@router.post("", response_model=NodeResponse, status_code=201)
def create_plan_node(project_id: UUID, request: NodeRequest):
    try:
        return _response(_service().create_node(project_id, request.parent_id, request.kind, request.title, request.status, request.due_date))
    except KeyError as exc:
        raise HTTPException(404, "project or parent node not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{node_id}", response_model=NodeResponse)
def update_plan_node(project_id: UUID, node_id: UUID, request: NodePatchRequest):
    try:
        return _response(_service().update_node(project_id, node_id, PlanNodePatch(**request.model_dump())))
    except KeyError as exc:
        raise HTTPException(404, "project or node not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{node_id}/move", response_model=NodeResponse)
def move_plan_node(project_id: UUID, node_id: UUID, request: MoveRequest):
    try:
        return _response(_service().move_node(project_id, node_id, request.new_parent_id, request.position))
    except KeyError as exc:
        raise HTTPException(404, "project or node not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
