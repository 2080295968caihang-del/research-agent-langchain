import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PlanTree from "../components/PlanTree";
import type { PlanNode } from "../types";

const nodes: PlanNode[] = [
  { node_id: "goal-1", parent_id: null, kind: "goal", title: "Ship release", status: "进行中", due_date: null, position: 0 },
  { node_id: "task-1", parent_id: "goal-1", kind: "task", title: "Write docs", status: "待开始", due_date: null, position: 0 },
];

describe("PlanTree", () => {
  let host: HTMLDivElement;
  beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); });
  it("renders nested nodes and exposes status controls", () => {
    act(() => { createRoot(host).render(<PlanTree nodes={nodes} onToggleStatus={vi.fn()} onMove={vi.fn()} />); });
    expect(host.textContent).toContain("Ship release");
    expect(host.textContent).toContain("Write docs");
    expect(host.querySelectorAll("select").length).toBe(2);
  });
});
