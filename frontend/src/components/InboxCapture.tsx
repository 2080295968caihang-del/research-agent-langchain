import { FormEvent, useState } from "react";
type Props = { onSubmit: (input: { kind: string; content: string }) => Promise<void> };
export default function InboxCapture({ onSubmit }: Props) {
  const [kind, setKind] = useState("task"); const [content, setContent] = useState(""); const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent) { event.preventDefault(); if (!content.trim()) return; setSaving(true); try { await onSubmit({ kind, content: content.trim() }); setContent(""); } finally { setSaving(false); } }
  return <form onSubmit={submit} className="inbox-capture"><h2>快速记录</h2><select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="记录类型"><option value="task">任务</option><option value="idea">想法</option><option value="question">问题</option><option value="note">备忘录</option><option value="research">待研究</option></select><textarea value={content} onInput={(event) => setContent(event.currentTarget.value)} placeholder="记录一个想法、任务或问题" /><button type="submit" disabled={saving || !content.trim()}>保存</button></form>;
}
