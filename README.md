# research-agent

基于 LangChain 的智能研究 Agent：把「检索 → 校验 → 带引用写报告」做成可运行的服务，
带 122 条离线测试、5 类 HTTP 错误语义和 token 成本计量。

本仓库同时放着学习过程的对照实现，以及一个与本项目无关的本机项目工作台（见文末）。

## 仓库结构

| 目录 | 内容 | 关系 |
|---|---|---|
| **`research-agent/`** | 主项目：可运行实现 + 122 条测试（`app/` 7 个模块、`ui/` 2 个模块） | **本人编写**，简历中的项目 |
| `research-agent-ai/` | 同一套代码的对照材料，带块级注释（`# [等级] 块 N`） | 逐块讲解用，不是新功能，不参与运行 |
| `backend/` + `frontend/` | 本机项目工作台 app（FastAPI + React/Vite） | 独立的小工具，与本项目无关 |
| `docs/` + `notes/` | 每日撰写步骤、规划、实跑记录 | 学习留痕，不是交付文档 |

## 主项目速览

```bash
cd research-agent
python -m venv .venv && ./.venv/Scripts/python.exe -m pip install -r requirements.txt
cp .env.example .env      # 填 OPENAI_API_KEY、SEARCH_API_KEY
python -m app.api                                    # http://127.0.0.1:8000/docs
python -m streamlit run ui/app.py                     # 网页入口
python -m pytest -q                                   # 122 passed
```

| 项 | 数字 | 证据位置 |
|---|---|---|
| 自动化测试 | 122 条 / 6 个文件 | `research-agent/tests/` |
| HTTP 状态码语义 | 200 / 422 / 502 / 504 / 500 | `research-agent/app/api.py` |
| token 计量 | 覆盖 4 个模型调用点 | `app/agent.py:143`、`app/report.py:359` |
| 单次成本（试算） | 约 ¥0.045 | `estimate_cost()` + `.env` 单价 |
| 单次真实请求 | 4 步 / 3 次工具 / 114.55s | `notes/day9.md` |

**口径**：122 条是离线测试（假模型、假搜索，不发网络请求）；4 步/3 次工具是一次真实请求的
观测值，不是平均值；¥0.045 是按 `.env` 单价试算，不是账单。

**明确没做的**：固定评测集（成功率、引用准确性、p95 目前没有实测数字）、e2e 用例、
Docker 化、Tavily 真实调用（代码有适配，只跑过博查）。

详细说明、状态码触发条件、复现命令与踩坑记录见 [`research-agent/README.md`](research-agent/README.md)。

## 附：本机项目工作台（`backend/` + `frontend/`）

这是一个本机优先的个人项目工作 app。FastAPI 后端、React/Vite 前端和项目文件夹可以在没有云服务的情况下运行。app 根目录是 `E:\ResearchAgent`，项目内容默认放在 `E:\ResearchProjects`，每个项目使用独立文件夹。

### 前置条件

- Windows 10/11
- Python 3.10 或更高版本，并能在终端执行 `python`
- Node.js 18 或更高版本，并能执行 `npm`
- PowerShell 7（执行启动、导入和备份脚本）

### 安装

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

### 启动

```powershell
cd E:\ResearchAgent
.\.venv\Scripts\Activate.ps1
.\scripts\start.ps1
```

启动脚本会启动两个本机进程：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

按 `Ctrl+C` 会停止脚本启动的两个进程。可以通过 `.env` 中的 `PROJECTS_ROOT`，或直接传入 `-ProjectRoot`，指定项目根目录。

### 项目操作

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

### 备份

备份脚本会创建带时间戳的 ZIP，包含项目清单、计划、笔记、报告、来源、附件、导出文件，以及 `_app\app.db` 数据库快照。目标目录不能位于项目目录内；`.env`、凭据和密钥文件会被排除。

```powershell
.\scripts\backup.ps1 `
  -ProjectPath 'E:\ResearchProjects\Research Agent' `
  -Destination 'E:\ResearchAgent\data\backups'
```

`DATABASE_URL` 默认从 `.env` 读取；也可以显式传入 `-DatabasePath`。恢复时先解压到新的项目路径，再在 app 中重新注册或执行重新索引。

### 测试与构建

```powershell
python -m pytest backend/tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run build
git diff --check
```

### 首版验收清单

- [ ] 注册两个以上独立项目目录。
- [ ] 为项目建立目标、阶段、任务和子任务。
- [ ] 在工作台查看今日任务和阻塞任务。
- [ ] 在计划视图之间切换并检查任务排序。
- [ ] 创建并搜索 Markdown 备忘录。
- [ ] 导入 Research Agent DOCX，并确认桌面原文件哈希不变。
- [ ] 导出一个项目，在另一条本机路径重新注册并索引。
- [ ] 研究执行完成后保存报告并关联真实来源 URL。

基础版暂不包含公网访问、多用户权限、云同步、自由白板和多 Agent 编排。
