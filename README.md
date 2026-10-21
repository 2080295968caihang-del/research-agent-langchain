# Private Project Workspace

这是一个本机优先的个人项目工作 app。FastAPI 后端、React/Vite 前端和项目文件夹可以在没有云服务的情况下运行。app 根目录是 `E:\ResearchAgent`，项目内容默认放在 `E:\ResearchProjects`，每个项目使用独立文件夹。

## 前置条件

- Windows 10/11
- Python 3.10 或更高版本，并能在终端执行 `python`
- Node.js 18 或更高版本，并能执行 `npm`
- PowerShell 7（执行启动、导入和备份脚本）

## 安装

在 PowerShell 中执行：

```powershell
cd E:\ResearchAgent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .\backend
npm --prefix .\frontend install
Copy-Item .env.example .env
```

`.env` 只保存本机路径和本机环境变量，已经被 Git 忽略。API 密钥不要写入项目文件、数据库或日志。

## 启动

```powershell
cd E:\ResearchAgent
.\.venv\Scripts\Activate.ps1
.\scripts\start.ps1
```

启动脚本会启动两个本机进程：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

按 `Ctrl+C` 会停止脚本启动的两个进程。可以通过 `.env` 中的 `PROJECTS_ROOT`，或直接传入 `-ProjectRoot`，指定项目根目录。

## 项目操作

已有项目必须包含 `project.json` 和 `plan.json`。可以在前端注册项目，也可以调用本地 API：

```powershell
$body = @{ root_path = 'E:\ResearchProjects\Example' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/projects `
  -ContentType 'application/json' -Body $body
```

导入首个 Research Agent 项目时，原始 DOCX 只会被复制，不会移动或覆盖：

```powershell
.\scripts\import_research_agent.ps1 `
  -SourcePath 'C:\Users\1000length\Desktop\Research Agent.docx' `
  -ProjectRoot 'E:\ResearchProjects\Research Agent'
```

## 备份

备份脚本会创建带时间戳的 ZIP，包含项目清单、计划、笔记、报告、来源、附件、导出文件，以及 `_app\app.db` 数据库快照。目标目录不能位于项目目录内；`.env`、凭据和密钥文件会被排除。

```powershell
.\scripts\backup.ps1 `
  -ProjectPath 'E:\ResearchProjects\Research Agent' `
  -Destination 'E:\ResearchAgent\data\backups'
```

`DATABASE_URL` 默认从 `.env` 读取；也可以显式传入 `-DatabasePath`。恢复时先解压到新的项目路径，再在 app 中重新注册或执行重新索引。

## 测试与构建

```powershell
python -m pytest backend/tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

## 首版验收清单

- [ ] 注册两个以上独立项目目录。
- [ ] 为项目建立目标、阶段、任务和子任务。
- [ ] 在工作台查看今日任务和阻塞任务。
- [ ] 在计划视图之间切换并检查任务排序。
- [ ] 创建并搜索 Markdown 备忘录。
- [ ] 导入 Research Agent DOCX，并确认桌面原文件哈希不变。
- [ ] 导出一个项目，在另一条本机路径重新注册并索引。
- [ ] 研究执行完成后保存报告并关联真实来源 URL。

基础版暂不包含公网访问、多用户权限、云同步、自由白板和多 Agent 编排。
