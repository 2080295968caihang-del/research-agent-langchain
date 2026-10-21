# Research Agent：你的练习目录

这是你照着 `../research-agent-ai/` 参考实现亲手输入的项目目录。
目录结构先一次搭好，代码按照 14 天计划逐步填充。

## 项目结构

```text
research-agent/
├── app/
│   ├── __init__.py       # Python 包标记
│   ├── main.py           # 程序入口
│   ├── agent.py          # 模型和 Agent 逻辑
│   ├── tools.py          # 搜索和网页工具
│   ├── schemas.py        # 请求、响应和报告数据结构
│   └── report.py         # 报告生成和引用格式化
├── tests/
│   ├── test_agent.py     # 模型调用测试
│   ├── test_tools.py     # 工具测试
│   └── test_report.py    # 报告测试
├── .env.example          # 环境变量模板
├── .gitignore            # 忽略密钥和缓存
├── requirements.txt      # Python 依赖
└── README.md             # 项目说明
```

## 第 1 天操作顺序

在 PowerShell 中执行：

```powershell
cd E:\ResearchAgent\research-agent
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

在 `.env` 中填写真实 API Key。不要把 Key 写进 Python 文件，也不要提交 `.env`。

然后照着 `../research-agent-ai/app/agent.py` 和 `../research-agent-ai/app/main.py`
手动输入代码，最后运行：

```powershell
python -m app.main
```

## 每天的填充范围

- 第 1 天：填写 `app/agent.py`、`app/main.py`，完成一次模型调用。
- 第 2 天：填写 `app/schemas.py`、`app/report.py`，生成结构化报告。
- 第 3 天：填写 `app/tools.py`，接入搜索工具。
- 后续：逐步补充 Agent、API、界面和测试。
