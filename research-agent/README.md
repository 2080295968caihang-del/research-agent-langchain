# Research Agent：基于 LangChain 的智能研究助手

输入一个问题，走「判断是否需要检索 → 调用搜索 → 校验返回 → 决定是否补搜 → 带引用写报告」的循环，
输出结构化报告和可核验的来源清单。CLI、HTTP 接口、网页三个入口共用同一套实现。

技术栈：Python 3.10+（开发环境 3.13）、LangChain、博查搜索 API（另有 Tavily 适配）、OpenAI 兼容接口、
FastAPI、Pydantic、pytest、Streamlit。

> 本目录是本人实现。同级 `../research-agent-ai/` 是同一套代码的对照材料（带块级注释），
> 两者边界见文末「参考实现与本人贡献」。

## 关键数字与复核方式

| 项 | 数字 | 怎么复核 |
|---|---|---|
| 自动化测试 | **122 条**，6 个文件（工具 34 / 报告 25 / 接口 30 / 循环 19 / 日志 6 / 界面 8） | `pytest -q` |
| HTTP 错误语义 | **5 种**状态码：200 / 422 / 502 / 504 / 500 | `tests/test_api.py` |
| token 计量覆盖 | **4 个**模型调用点（含报告生成，最贵的一笔） | `app/agent.py:164`、`app/report.py:359` |
| 单次成本（试算） | 约 **¥0.045** | `estimate_cost()` + `.env` 三个单价 |
| 单次演示（一次真实请求） | 4 步 / 3 次工具调用 / 114.55 秒 | `../notes/day9.md` |

**口径说明**

- 122 条是**离线自动化测试**，跑的是假模型与假搜索，不访问真实 API。真实链路要自己配 key 跑一次。
- 4 步 / 3 次工具是**一次真实请求的观测值**，不是平均值或性能指标。模型有随机性，同题两次的
  步数与耗时都会变（另一次是 78.30 秒 / 3 次调用 / 1 次工具）。
- ¥0.045 是按 `.env` 里的单价**试算**，不是账单：`PRICE_INPUT_PER_M=2`、`PRICE_OUTPUT_PER_M=8`、
  `PRICE_CACHE_READ_PER_M=0.2`（元 / 百万 token）。单价留空时接口返回 `null`，不按 0 算。

## 架构

```text
app/main.py  ──┐
app/api.py   ──┼──→ app/agent.py ──→ app/tools.py     搜索 + 结果清洗
ui/app.py    ──┘         │                （博查 / Tavily 两个 provider）
                         └──→ app/report.py   提示词 + 引用回填 + 链接探测
                              app/schemas.py  报告与请求的数据结构
```

| 文件 | 职责 |
|---|---|
| `app/agent.py` | 多步循环、终止条件、用量累加（`ContextVar`）、成本估算 |
| `app/tools.py` | 搜索调用、URL 归一化、保序去重、空标题过滤、top-k 截断 |
| `app/report.py` | 报告提示词、JSON 解析、引用编号回填与清洗、链接可达性探测 |
| `app/api.py` | `/research` 端点、耗时中间件、按异常类型注册的 handler |
| `app/logging_setup.py` | 控制台 / 轮转文件 handler、级别解析、密钥脱敏 |
| `app/main.py` | CLI 入口 |
| `ui/` | Streamlit 界面：`client.py` 只做 HTTP + 错误翻译，`app.py` 只做渲染 |

## 快速开始

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows；macOS/Linux 用 .venv/bin/python
cp .env.example .env        # 填 OPENAI_API_KEY、SEARCH_API_KEY
```

`.env` 最小配置：

```ini
OPENAI_API_KEY=           # 必填
OPENAI_MODEL=gpt-4o-mini  # 任意 OpenAI 兼容模型
OPENAI_BASE_URL=          # 走中转时填中转地址，官方留空
SEARCH_PROVIDER=bocha     # bocha（默认，实际使用）或 tavily
SEARCH_API_KEY=           # 必填
```

三个入口：

```bash
python -m app.main                                   # CLI，一次问答
python -m app.api                                    # HTTP，http://127.0.0.1:8000/docs
python -m streamlit run ui/app.py                    # 网页（必须用 -m，见「踩过的坑」）
```

一次 HTTP 调用：

```bash
curl --noproxy '*' -m 600 -X POST http://127.0.0.1:8000/research \
  -H 'Content-Type: application/json' \
  -d '{"question":"LangChain 的 Tool Calling 是什么","max_steps":4,"max_tool_calls":3,"verify_links":true}'
```

真实请求要 1–3 分钟（最多 5 次模型调用 + 若干次搜索 + 链接探测），客户端超时别用默认值。

## 简历主张怎么验证

| 主张 | 代码入口 | 测试 |
|---|---|---|
| 工具调用循环 + 双条件终止 | `app/agent.py:282` `run_agent` | `tests/test_agent.py` |
| 搜索异常降级、不中断任务 | `app/tools.py:132` `search` 抛 `SearchError`；循环内捕获 | `tests/test_tools.py`、`tests/test_agent.py` |
| URL 归一化 / 保序去重 / 空标题过滤 / top-k | `app/tools.py:367` `normalize_url`、`:508` `dedupe_sources` | `tests/test_tools.py`（34 条） |
| 引用只用资料清单编号、剔除无效编号并重编号 | `app/report.py:250` `apply_citations` | `tests/test_report.py` |
| 资料不足写入 `limitations`、不让模型编内容 | `app/report.py` 的 `REPORT_SYSTEM_PROMPT` 与 `_push_limits` | `tests/test_report.py` |
| 5 种状态码语义 | `app/api.py:107` 中间件、`:137/146/155` 三个 handler | `tests/test_api.py`（30 条） |
| token 累加与成本估算 | `app/agent.py:143` `_USAGE`、`:164` `record_usage`、`:208` `estimate_cost` | `tests/test_agent.py`、`tests/test_report.py` |
| 日志轮转 + `sk-` / `KGAT_` 脱敏 | `app/logging_setup.py:20` `SECRET_PATTERN`、`:94` `setup_logging` | `tests/test_logging.py`（6 条） |
| 界面把 HTTP 失败翻成中文 | `ui/client.py:21` `_STATUS_HINT`、`:114` 连接失败、`:123` 超时 | `tests/test_ui.py`（8 条） |

### 状态码语义

| 状态码 | 触发条件 | 界面文案 |
|---|---|---|
| 200 | 正常返回；响应头带 `X-Elapsed-Ms` | — |
| 422 | 请求体校验失败（问题为空、步数越界） | 输入有问题：… |
| 502 | 搜索服务报错（key 失效、上游 5xx） | 搜索服务不可用，稍后重试 |
| 504 | 预算内未收敛（步数 / 工具次数用尽） | 研究没在预算内跑完，可以调大 max_steps 再试 |
| 500 | 未捕获异常，兜底（日志里有原始堆栈） | 服务端内部错误（已记日志） |

另有两条不是状态码的失败：连接失败（`except ConnectionError`）与超时（`except Timeout`）。
顺序不能反：`ConnectTimeout` 同时是两个异常类的子类，`ConnectionError` 必须写在前面。

## 测试

```bash
./.venv/Scripts/python.exe -m pytest -q              # 122 passed，约 4–10 秒
./.venv/Scripts/python.exe -m pytest -q tests/test_api.py -k 502   # 单条
```

测试全部离线：假模型返回预置的 `AIMessage`，假搜索返回固定结果，不发网络请求、不花钱。
真实 API 的连通性**没有被测试覆盖**，需要自己配 key 跑一次。

## 局限（明确未做 / 未验证）

- **没有固定评测集**：成功率、引用准确性、p95 这类指标目前没有实测数字。
  计划中的 20–30 题评估集与 3 条 e2e 用例尚未实现。
- **`main.py` CLI 不打印 token 用量**，只有 HTTP 回包和日志里有。
- **502 / 504 的错误响应不带 `usage` 字段**：异常路径返回的是另一套精简结构，用量只进日志。
- **只跑过博查**：Tavily 的 provider 代码在，但没有用它真实跑过。
- **单次耗时不可用于横向比较**：模型非确定性，同一问题的两次运行差 40 秒是常态。
- **引用回填保证的是「编号都能对上来源」，不保证报告里的事实为真**：事实正确性依赖模型与资料来源。

## 踩过的坑（会影响复现，写在这里省一次调试）

- 必须 `python -m streamlit run ui/app.py`。直接 `streamlit run` 只把脚本所在目录塞进
  `sys.path[0]`，`from ui.client import ...` 会失败。
- 端点用 `def` 不用 `async def`。同步端点跑在线程池里，阻塞调用不会堵住事件循环
  （实测两个并发请求 0.32s vs 0.60s）。
- 500 兜底不能写成 `@app.exception_handler(Exception)`：它挂在 `ServerErrorMiddleware` 上，
  发完响应还会 `raise`，`TestClient` 会直接把异常抛给调用方。必须写成 HTTP 中间件。
- Windows 下 `HTTP_PROXY` 会把 `127.0.0.1` 也代理掉，测「连不上」要显式绕过
  （`Session().trust_env = False`，或 `curl --noproxy '*'`）。

## 参考实现与本人贡献

本仓库包含两个目录，职责不同：

| 目录 | 内容 | 说明 |
|---|---|---|
| `research-agent/`（本目录） | 可运行实现 + 122 条测试 | 本人编写。测试文件从第 4 天起整份复制，不再逐字手敲 |
| `../research-agent-ai/` | 同一套代码的**对照材料**，带块级注释（`# [等级] 块 N`） | 用于逐块讲解，不是独立功能，也不参与运行 |

学习过程留痕：`../docs/`（每日撰写步骤与规划）、`../notes/`（实跑记录与行动清单）。
两区的六个测试文件逐字一致，`diff` 无输出，这是刻意维持的约束，用来保证「参考区讲的」
和「手敲区跑的」是同一套断言。
