import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { PlanNode, PlanStatus, ProjectRecord } from "../types";
import PlanTree from "../components/PlanTree";

export default function ProjectPage({ project, onBack }: { project: ProjectRecord; onBack: () => void }) {
  const [nodes, setNodes] = useState<PlanNode[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(""); const [mutationError, setMutationError] = useState("");
  const refresh = useCallback(async () => { setLoading(true); setError(""); try { setNodes(await api.getPlan(project.project_id)); } catch (cause) { setError(String(cause)); } finally { setLoading(false); } }, [project.project_id]);
  useEffect(() => { void refresh(); }, [refresh]);
  const mutate = async (operation: Promise<unknown>) => { setMutationError(""); try { await operation; await refresh(); } catch (cause) { setMutationError(String(cause)); } };
  const toggle = (node: PlanNode, status: PlanStatus) => void mutate(api.updatePlanNode(project.project_id, node.node_id, status));
  const move = (node: PlanNode, direction: -1 | 1) => { const siblings = nodes.filter((candidate) => candidate.parent_id === node.parent_id).sort((a, b) => a.position - b.position); const index = siblings.findIndex((candidate) => candidate.node_id === node.node_id); const target = siblings[index + direction]; if (target) void mutate(api.movePlanNode(project.project_id, node.node_id, node.parent_id, target.position)); };
  return <div className="project-page"><button type="button" onClick={onBack}>← 工作台</button><header><h1>{project.name}</h1><p>{project.goal}</p><small>{project.root_path}</small></header><nav><button type="button" aria-current="page">计划</button><a href="#notes">备忘录</a></nav>{loading && <p>正在加载项目计划...</p>}{error && <p role="alert">项目计划加载失败：{error} <button type="button" onClick={() => void refresh()}>重试</button></p>}{mutationError && <p role="alert">保存计划失败：{mutationError} <button type="button" onClick={() => void refresh()}>重试</button></p>}{!loading && !error && !nodes.length ? <p>暂无计划任务</p> : !loading && !error && <PlanTree nodes={nodes} onToggleStatus={toggle} onMove={move} />}<p id="notes"><a href="#notes">打开项目备忘录</a></p></div>;
}
