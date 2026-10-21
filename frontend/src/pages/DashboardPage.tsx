import { useEffect, useState } from "react";
import { api } from "../api";
import type { PlanNode, ProjectRecord } from "../types";
import InboxCapture from "../components/InboxCapture";

type Props = { onOpenProject: (project: ProjectRecord) => void };
const localDate = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };

export default function DashboardPage({ onOpenProject }: Props) {
  const [projects, setProjects] = useState<ProjectRecord[]>([]); const [plans, setPlans] = useState<PlanNode[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState("");
  const load = () => { setLoading(true); setError(""); return api.listProjects().then(async (items) => { setProjects(items); const all = await Promise.all(items.map((item) => api.getPlan(item.project_id))); setPlans(all.flat()); }).catch((cause) => setError(String(cause))).finally(() => setLoading(false)); };
  useEffect(() => { void load(); }, []);
  const today = plans.filter((node) => node.due_date === localDate()); const blocked = plans.filter((node) => node.status === "阻塞");
  return <div className="dashboard"><header><h1>私人项目工作台</h1><p>本机项目计划与快速记录</p></header>{loading && <p>正在加载工作台...</p>}{error && <p role="alert">工作台加载失败：{error} <button type="button" onClick={() => void load()}>重试</button></p>}<section><h2>活跃项目</h2>{!loading && !error && projects.length === 0 ? <p>暂无项目</p> : <div className="project-grid">{projects.map((project) => <button className="project-card" key={project.project_id} onClick={() => onOpenProject(project)}><strong>{project.name}</strong><span>{project.goal}</span><small>{project.status}</small></button>)}</div>}</section><section><h2>今日任务</h2>{!loading && !error && (today.length ? <ul>{today.map((node) => <li key={node.node_id}>{node.title} <small>{node.status}</small></li>)}</ul> : <p>今天没有安排任务</p>)}</section><section><h2>阻塞任务</h2>{!loading && !error && (blocked.length ? <ul>{blocked.map((node) => <li key={node.node_id}>{node.title}</li>)}</ul> : <p>暂无阻塞任务</p>)}</section><InboxCapture onSubmit={(input) => api.createInboxItem(input).then(() => undefined)} /></div>;
}
