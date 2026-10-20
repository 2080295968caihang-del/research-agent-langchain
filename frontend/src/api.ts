import type { InboxItem, PlanNode, ProjectRecord } from "./types";

export const API_BASE = "/api";
export type HealthResponse = { status: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { headers: { "Content-Type": "application/json" }, ...init });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json() as Promise<T>;
}

export const api = {
  async health(): Promise<HealthResponse> {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) {
      throw new Error(`Health check failed: ${response.status}`);
    }
    return response.json() as Promise<HealthResponse>;
  },
  listProjects: () => request<ProjectRecord[]>("/projects"),
  getPlan: (projectId: string) => request<PlanNode[]>(`/projects/${projectId}/plan`),
  updatePlanNode: (projectId: string, nodeId: string, status: string) => request<PlanNode>(`/projects/${projectId}/plan/${nodeId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  movePlanNode: (projectId: string, nodeId: string, newParentId: string | null, position: number) => request<PlanNode>(`/projects/${projectId}/plan/${nodeId}/move`, { method: "POST", body: JSON.stringify({ new_parent_id: newParentId, position }) }),
  createInboxItem: (input: { kind: string; content: string; project_id?: string | null }) => request<InboxItem>("/inbox", { method: "POST", body: JSON.stringify(input) }),
};
