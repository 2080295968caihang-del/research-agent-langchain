export type ProjectRecord = { project_id: string; name: string; root_path: string; status: string; goal: string; indexed_at?: string | null; document_count?: number };
export type PlanStatus = "待开始" | "进行中" | "已完成" | "阻塞" | "已取消";
export type PlanNode = { node_id: string; parent_id: string | null; kind: string; title: string; status: PlanStatus; due_date: string | null; position: number };
export type InboxItem = { item_id: string; kind: string; content: string; project_id: string | null; created_at: string };
