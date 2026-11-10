# Research Agent：参考实现（对照材料）

这是完整参考目录。你的代码请手动输入到同级的 `../research-agent/`，
不要直接复制粘贴，以便理解每个文件的职责。

**定位**：本目录是**对照材料**，带块级注释（`# [等级] 块 N`），用于逐块讲解；
可运行的主项目、对外文档和测试入口都在 [`../research-agent/`](../research-agent/README.md)。
两区的六个测试文件逐字一致，`diff` 无输出。

## 项目结构和文件作用

```text
research-agent-ai/
├── app/
│   ├── __init__.py       # Python 包标记
│   ├── main.py           # 命令行入口：读取问题并显示报告
│   ├── agent.py          # 第 1 天模型客户端 + 第 4 天 Tool Calling 循环
│   ├── tools.py          # 第 3 天：搜索工具（统一格式 + 超时 + 重试 + 清洗）
│   ├── schemas.py        # 第 2 天：定义问题和报告的 Pydantic 模型
│   └── report.py         # 第 2 天：Prompt、JSON 解析和报告校验
├── tests/
│   ├── test_agent.py     # 第 1 天 2 条 + 第 4 天 11 条，共 13 条离线测试
│   ├── test_tools.py     # 第 3 天：搜索的 18 个离线测试
│   └── test_report.py    # 第 2 天结构化报告测试
├── .env.example          # 环境变量模板，不放真实 Key
├── .gitignore            # 忽略 .env、虚拟环境和缓存
├── requirements.txt      # LangChain、Pydantic、requests 和测试依赖
└── README.md             # 安装、运行和文件职责说明
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
```

在 `.env` 中填写有效的模型服务配置，然后运行：

```powershell
python -m app.main
```

## 第 2 天运行

第 2 天的入口会要求模型只返回 JSON，并把结果校验成 `ResearchReport`：

```powershell
python -m app.main
```

输入示例：

```text
什么是 LangChain？它适合解决什么问题？
```

程序会输出包含 `title`、`summary`、`key_findings`、`conclusion` 和
`limitations` 的结构化 JSON。第二天还没有搜索工具，因此报告不会包含外部来源。

## 第 3 天运行

第 3 天新增搜索能力，但还没有接进报告流程（Tool Calling 是第 4 天的事）。
先在 `.env` 里补上搜索配置：

```text
SEARCH_PROVIDER=tavily
SEARCH_API_KEY=tvly-你的key
```

然后直接测搜索：

```powershell
python -m app.tools "LangChain 和 LangGraph 的区别"
```

预期输出：每条结果的标题、URL 和摘要（摘要超过 100 字会被截断显示）。
搜不到结果时打印「请求成功，但没有搜到结果」，这不算错误。

## 第 4 天运行

```powershell
python -m app.agent "2026 年诺贝尔物理学奖颁给了谁"
```

流程：问题发给模型 → 模型返回 `tool_calls` → 执行 `web_search` → 结果以 `ToolMessage`
按 `tool_call_id` 回给模型 → 模型作答。末尾打印「统计：N 步 / M 次工具调用」。

### 真实运行记录（2026-10-24）

搜索正常时：

```text
问题：2026 年诺贝尔物理学奖颁给了谁？
steps=2  calls=1  budget=False
工具返回 5 条（标题含「2026年诺贝尔物理学奖揭晓」「Francis Halzen wins 2026 Nobel Prize」）
回答：2026 年诺贝尔物理学奖授予了 Francis Halzen。
      来源：https://english.news.cn/europe/20261006/...
```

搜索 Key 缺失时（证明工具失败不会炸掉循环）：

```text
steps=2  calls=1  budget=False
回答：目前不能确认「2026 年诺贝尔物理学奖颁给了谁」。我尝试检索……但搜索工具报错，
      未获得可核验的资料，因此不能编造获奖人或依据。
```

## 离线测试

测试使用假模型，也不会真的请求搜索接口，不消耗任何额度：

```powershell
python -m pytest -q
python -m py_compile app\agent.py app\main.py app\schemas.py app\report.py app\tools.py
```

## 第二天代码流程

```text
用户问题
  -> ResearchQuestion 校验
  -> 构造结构化 Prompt
  -> ChatOpenAI 返回文本
  -> 去掉可选 Markdown JSON 包裹
  -> json.loads
  -> ResearchReport.model_validate
  -> 输出结构化报告
```

## 第三天代码流程

```text
搜索词
  -> search() 校验非空、读配置（SEARCH_PROVIDER / SEARCH_API_KEY / TOP_K / TIMEOUT / RETRIES）
  -> 从 _PROVIDER_FUNCTIONS 里挑服务商函数（tavily / bocha）
  -> _http_post_json 发 POST（Bearer Key + timeout）
  -> _raise_for_status：2xx 通过；429/5xx 抛「可重试」；401/403 立刻失败
  -> 失败则指数退避重试，最多 SEARCH_MAX_RETRIES 次
  -> _normalize_results：丢掉非法 URL、压空白、截断、截到 top_k
  -> 返回 [{"title", "url", "snippet"}]
  -> format_results_for_prompt 包上 <search_results> 边界，再交给模型（第 4 天）
```

去重（同一页面多个链接）留到第 5 天，引用编号 `[1]` `[2]` 留到第 6 天。

## 第四天代码流程

```text
问题
  -> run_agent 校验非空
  -> base_model.bind_tools([web_search])      得到带工具的新模型（原模型不变）
  -> messages = [SystemMessage(五条规则), HumanMessage(问题)]
  -> for step in 1..max_steps
       ai = tool_model.invoke(messages)
       messages.append(ai)                    漏了这行，模型会重复要工具
       无 tool_calls  -> 返回 AgentRun(answer)              【出口 A：正常收敛】
       有 tool_calls  -> 逐个执行：
            预算够 -> _execute_tool(名字, 参数) -> ToolMessage(tool_call_id)
            预算尽 -> ToolMessage(「预算已用尽」)，不执行
       这一轮把预算花完 -> base_model.invoke(messages) 强制作答 【出口 B：软刹车】
  -> 步数用尽 -> raise AgentLoopError                        【出口 C：硬报错】
```

三个出口缺一不可。C 绝对不能改成「返回半成品」——线上表现为答非所问，比报错更难查。

当前测试总数：**37 passed**（18 tools + 6 report + 13 agent）。
