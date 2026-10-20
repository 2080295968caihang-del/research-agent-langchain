"""Hierarchical plan operations backed by each project's plan.json."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID, uuid4

from ..domain import PlanNode
from ..registry import ProjectRegistry


KINDS = {"goal", "phase", "milestone", "task", "subtask"}
STATUSES = {"待开始", "进行中", "已完成", "阻塞", "已取消"}


@dataclass(frozen=True)
class PlanNodePatch:
    title: str | None = None
    kind: str | None = None
    status: str | None = None
    due_date: date | None = None


class PlanService:
    def __init__(self, registry: ProjectRegistry):
        self.registry = registry

    def _project(self, project_id: UUID):
        return self.registry.get(UUID(str(project_id)))

    def _nodes(self, project_id: UUID) -> list[PlanNode]:
        record = self._project(project_id)
        return self.registry.project_store.load_plan(record.root_path)

    @staticmethod
    def _ordered(nodes: list[PlanNode]) -> list[PlanNode]:
        children: dict[UUID | None, list[PlanNode]] = {}
        for node in nodes:
            children.setdefault(node.parent_id, []).append(node)
        for siblings in children.values():
            siblings.sort(key=lambda n: n.position)
        result: list[PlanNode] = []

        def visit(parent: UUID | None) -> None:
            for node in children.get(parent, []):
                result.append(node)
                visit(node.node_id)

        visit(None)
        return result

    @staticmethod
    def _normalize_siblings(nodes: list[PlanNode]) -> list[PlanNode]:
        grouped: dict[UUID | None, list[PlanNode]] = {}
        for node in nodes:
            grouped.setdefault(node.parent_id, []).append(node)
        positions = {node.node_id: index for siblings in grouped.values() for index, node in enumerate(sorted(siblings, key=lambda n: n.position))}
        return [PlanNode(n.node_id, n.parent_id, n.kind, n.title, n.status, n.due_date, positions[n.node_id]) for n in nodes]

    def _save(self, project_id: UUID, nodes: list[PlanNode]) -> None:
        record = self._project(project_id)
        self.registry.project_store.save_plan(record.root_path, self._ordered(self._normalize_siblings(nodes)))

    def create_node(self, project_id, parent_id, kind, title, status, due_date) -> PlanNode:
        project_id = UUID(str(project_id))
        if kind not in KINDS:
            raise ValueError("invalid plan node kind")
        if status not in STATUSES:
            raise ValueError("invalid plan node status")
        nodes = self._nodes(project_id)
        parent = UUID(str(parent_id)) if parent_id else None
        if parent is not None and not any(n.node_id == parent for n in nodes):
            raise KeyError(parent)
        position = sum(n.parent_id == parent for n in nodes)
        node = PlanNode(uuid4(), parent, kind, title, status, due_date, position)
        self._save(project_id, nodes + [node])
        return node

    def update_node(self, project_id, node_id, patch: PlanNodePatch) -> PlanNode:
        project_id, node_id = UUID(str(project_id)), UUID(str(node_id))
        nodes = self._nodes(project_id)
        if not any(n.node_id == node_id for n in nodes):
            raise KeyError(node_id)
        if patch.kind is not None and patch.kind not in KINDS:
            raise ValueError("invalid plan node kind")
        if patch.status is not None and patch.status not in STATUSES:
            raise ValueError("invalid plan node status")
        updated = []
        for node in nodes:
            if node.node_id == node_id:
                node = PlanNode(node.node_id, node.parent_id, patch.kind or node.kind, patch.title if patch.title is not None else node.title, patch.status or node.status, patch.due_date if patch.due_date is not None else node.due_date, node.position)
            updated.append(node)
        self._save(project_id, updated)
        return next(n for n in self._nodes(project_id) if n.node_id == node_id)

    def move_node(self, project_id, node_id, new_parent_id, position) -> PlanNode:
        project_id, node_id = UUID(str(project_id)), UUID(str(node_id))
        nodes = self._nodes(project_id)
        by_id = {n.node_id: n for n in nodes}
        if node_id not in by_id:
            raise KeyError(node_id)
        parent = UUID(str(new_parent_id)) if new_parent_id else None
        if parent is not None and parent not in by_id:
            raise KeyError(parent)
        descendants = set()
        frontier = [node_id]
        while frontier:
            current = frontier.pop()
            descendants.add(current)
            frontier.extend(n.node_id for n in nodes if n.parent_id == current)
        if parent in descendants:
            raise ValueError("node cannot become its own descendant")
        siblings = [n for n in nodes if n.parent_id == parent and n.node_id != node_id]
        if position < 0 or position > len(siblings):
            raise ValueError("invalid sibling position")
        reordered = siblings[:position] + [by_id[node_id]] + siblings[position:]
        positions = {n.node_id: i for i, n in enumerate(reordered)}
        moved = []
        for node in nodes:
            if node.node_id == node_id:
                moved.append(PlanNode(node.node_id, parent, node.kind, node.title, node.status, node.due_date, positions[node.node_id]))
            elif node.parent_id == parent:
                moved.append(PlanNode(node.node_id, node.parent_id, node.kind, node.title, node.status, node.due_date, positions[node.node_id]))
            else:
                moved.append(node)
        self._save(project_id, moved)
        return next(n for n in self._nodes(project_id) if n.node_id == node_id)

    def list_tree(self, project_id) -> list[PlanNode]:
        return self._ordered(self._nodes(UUID(str(project_id))))
