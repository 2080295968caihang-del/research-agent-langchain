import { useState } from "react";
import type { ProjectRecord } from "./types";
import DashboardPage from "./pages/DashboardPage";
import ProjectPage from "./pages/ProjectPage";

export default function App() {
  const [project, setProject] = useState<ProjectRecord | null>(null);
  return <main>{project ? <ProjectPage project={project} onBack={() => setProject(null)} /> : <DashboardPage onOpenProject={setProject} />}</main>;
}
