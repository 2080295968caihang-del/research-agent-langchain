# Research Agent：第 1 天参考代码

这是完整参考目录。你的代码请手动输入到同级的 `../research-agent/`，
不要直接复制粘贴，以便理解每个文件的职责。

## 项目结构

```text
research-agent-ai/
├── app/
│   ├── __init__.py       # Python 包标记
│   ├── main.py           # 命令行入口
│   ├── agent.py          # 模型和 Agent 逻辑
│   ├── tools.py          # 搜索和网页工具
│   ├── schemas.py        # 请求、响应和报告数据结构
│   └── report.py         # 报告生成和引用格式化
├── tests/
│   ├── test_agent.py     # 第 1 天离线测试
│   ├── test_tools.py     # 工具测试位置
│   └── test_report.py    # 报告测试位置
├── .env.example          # 环境变量模板
├── .gitignore            # 忽略密钥和缓存
├── requirements.txt      # Python 依赖
└── README.md             # 运行说明
```

## 第 1 天运行

```powershell
cd E:\ResearchAgent\research-agent-ai
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python -m app.main
```

在 `.env` 中配置：

```dotenv
OPENAI_API_KEY=你的真实API_KEY
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

如果使用 OpenAI 兼容服务，填写服务商提供的 `OPENAI_BASE_URL`，并使用其支持的模型名。

## 代码职责

- `app/agent.py`：读取环境变量、创建 `ChatOpenAI`、调用模型。
- `app/main.py`：加载 `.env`，接收用户输入并显示结果。
- `app/tools.py`：后续接入搜索工具。
- `app/schemas.py`：后续定义研究问题和报告结构。
- `app/report.py`：后续生成带来源的研究报告。

## 离线检查

不填写 API Key 也可以运行基础测试：

```powershell
python -m pytest -q
python -m py_compile app\agent.py app\main.py
```

真正调用模型需要有效 API Key、网络连接和正确的模型名。
