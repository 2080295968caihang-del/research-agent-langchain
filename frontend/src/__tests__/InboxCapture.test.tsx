import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import InboxCapture from "../components/InboxCapture";

describe("InboxCapture", () => {
  it("submits a typed item and clears after success", async () => {
    const host = document.createElement("div"); document.body.appendChild(host);
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    act(() => { createRoot(host).render(<InboxCapture onSubmit={onSubmit} />); });
    const input = host.querySelector("textarea") as HTMLTextAreaElement;
    const select = host.querySelector("select") as HTMLSelectElement;
    await act(async () => {
      input.value = "Review proposal"; input.dispatchEvent(new Event("input", { bubbles: true }));
      select.value = "task"; select.dispatchEvent(new Event("change", { bubbles: true }));
      (host.querySelector("button") as HTMLButtonElement).click();
    });
    expect(onSubmit).toHaveBeenCalledWith({ kind: "task", content: "Review proposal" });
    expect(input.value).toBe("");
  });
});
