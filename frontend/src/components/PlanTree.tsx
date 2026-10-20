import { useState } from "react";
import type { PlanNode, PlanStatus } from "../types";

const statuses: PlanStatus[] = ["待开始", "进行中", "已完成", "阻塞", "已取消"];
type Props = { nodes: PlanNode[]; onToggleStatus: (node: PlanNode, status: PlanStatus) => void; onMove: (node: PlanNode, direction: -1 | 1) => void };

export default function PlanTree({ nodes, onToggleStatus, onMove }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const children = (parentId: string | null) => nodes.filter((node) => node.parent_id === parentId).sort((a, b) => a.position - b.position);
  const render = (parentId: string | null, depth = 0): React.ReactNode => children(parentId).map((node) => {
    const hasChildren = children(node.node_id).length > 0;
    return <div key={node.node_id} className="plan-node" style={{ marginLeft: depth * 20 }}>
      <div className="plan-row">
        {hasChildren && <button type="button" aria-label={`toggle ${node.title}`} onClick={() => setCollapsed((current) => { const next = new Set(current); next.has(node.node_id) ? next.delete(node.node_id) : next.add(node.node_id); return next; })}>{collapsed.has(node.node_id) ? "+" : "−"}</button>}
        {!hasChildren && <span className="tree-spacer" />}
        <strong>{node.title}</strong><span className="kind">{node.kind}</span>
        <select aria-label={`status ${node.title}`} value={node.status} onChange={(event) => onToggleStatus(node, event.target.value as PlanStatus)}>{statuses.map((status) => <option key={status}>{status}</option>)}</select>
        <button type="button" onClick={() => onMove(node, -1)} aria-label={`move ${node.title} up`}>↑</button><button type="button" onClick={() => onMove(node, 1)} aria-label={`move ${node.title} down`}>↓</button>
      </div>{!collapsed.has(node.node_id) && render(node.node_id, depth + 1)}
    </div>;
  });
  return <section aria-label="项目计划">{render(null)}</section>;
}
