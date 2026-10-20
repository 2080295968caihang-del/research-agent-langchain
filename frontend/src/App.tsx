import { useEffect, useState } from "react";

import { api } from "./api";

export default function App() {
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    api.health().then((result) => setStatus(result.status)).catch(() => setStatus("unavailable"));
  }, []);

  return (
    <main>
      <h1>私人项目工作台</h1>
      <p>本地项目管理基础版</p>
      <p aria-live="polite">服务状态：{status}</p>
    </main>
  );
}
