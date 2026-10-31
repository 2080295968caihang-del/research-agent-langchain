"""第 7 天 + 第 7b 天：HTTP 接口层。

════════════════════════════════════════════════════════════════════
一、这份文件在做什么
════════════════════════════════════════════════════════════════════
它不实现任何业务。业务全在已有的三个函数里：

    run_agent(question)                    → AgentRun（检索 + 回答 + sources）
    generate_report(question, sources=...) → ResearchReport（结构化报告）
    report_to_markdown(report)             → str（可点击引用）

这一层只做三件事：
    1. 把 HTTP 请求体解析成 Pydantic 模型（参数校验交给 Pydantic）
    2. 按顺序调用上面三个函数
    3. 把内部异常翻译成 HTTP 状态码

所以「接口层只做编排，不做逻辑」——这条守住了，接口就几乎没有 bug 空间。

════════════════════════════════════════════════════════════════════
二、逻辑链
════════════════════════════════════════════════════════════════════
    POST /research  {question, max_steps, max_tool_calls, verify_links}
         ↓ Pydantic 校验：type / 长度 / 范围，不合规 → 422（FastAPI 自动，不用写代码）
    run_agent(question, max_steps, max_tool_calls)
         ↓ 抛 SearchError      → 502 上游搜索挂了
         ↓ 抛 AgentLoopError   → 504 步数耗尽仍未收敛
    generate_report(question, sources=run.sources, verify_links=verify_links)
         ↓ 抛 ValueError       → 422 模型输出不是合法 JSON / 字段不合规
    report_to_markdown(report)              纯函数，不抛业务异常
         ↓
    ResearchResponse {report, markdown, steps, tool_calls, elapsed_seconds}

「问题为空」这类错误由谁抛分两层：
    · 请求体里 question 是空串 → Pydantic 的 min_length 先拦住 → 422
    · 问题只有空白（" "）→ 长度够，Pydantic 放行，run_agent 里再抛 ValueError → 422
两层都落到同一个状态码，客户端不用区分。

════════════════════════════════════════════════════════════════════
三、异常 → 状态码 对照（手敲标注里点名的 400/422/500/504）
════════════════════════════════════════════════════════════════════
    422  参数不合法（Pydantic 长度/范围/类型）
    422  ValueError：问题全空白、模型输出不是 JSON、报告校验失败
    502  SearchError：博查搜索服务不可用
    504  AgentLoopError：用完 max_steps 模型还在要工具
    500  其他一切未预料异常

为什么 504 而不是 500：语义是「上游（模型/搜索）在限定预算内没给出结果」，
不是我们代码写错了。分开的意义是告警策略不同——504 涨说明要调预算或换模型，
500 涨说明有 bug。

为什么没有 400：FastAPI 把「请求体 JSON 语法错」也归到 422，
自己再补一个 400 会让客户端要处理两种「你参数写错了」，没有收益。
（如果将来要 400，用 @app.exception_handler 覆盖 RequestValidationError 即可。）

════════════════════════════════════════════════════════════════════
四、给只学过基础语法的你（6 个点）
════════════════════════════════════════════════════════════════════
1. @app.post("/research") 是装饰器注册路由：被装饰的函数不会立刻执行，
   它只是被登记成「路径 + 方法 → 函数」的一张表，请求来了才调用。
   函数名（research）随便取，URL 由装饰器参数决定。

2. 函数参数的注解决定参数「从哪来」：
       request: ResearchRequest  → 整个请求体（JSON）按这个模型解析
       question: str             → 单独的 str 会被当成 query 参数 ?question=...
       question: str = Query(...) → 显式声明为 query 参数
   这次的入参都是模型里的字段，所以只写一个 ResearchRequest，别写成三个 str。

3. response_model=ResearchResponse 不只写文档，它会**真的**再校验一次返回值：
   返回的 dict 多出的键会被丢掉，缺的必填键会抛错。
   好处是内部实现改了字段名，接口不会静默变成另一个结构。

4. HTTPException(status_code=..., detail=...) 是 FastAPI 的「受控错误」：
   框架捕获它，渲染成 {"detail": ...} 并带上指定状态码。
   直接 raise ValueError 是没用的——那会变成 500（或直接被 TestClient 抛出）。
   （7b 之后端点里不再 raise HTTPException，改用注册 handler 接管，
     所以这个 import 已经删掉。见第六节。）

5. raise ... from error 保留异常链。7b 把异常搬进 handler 之后，
   `raise HTTPException(...) from error` 的写法跟着消失，
   但异常链没丢——handler 里 logger.error(...) 打的就是原始异常对象。

6. def 还是 async def：这里必须是 def。
   def 的端点由 FastAPI 丢到线程池执行，不会卡住事件循环；
   async def 里做阻塞调用（requests、同步模型调用）会把整个服务卡死，
   一个请求就能让所有其他请求排队。
   我们的 run_agent 是纯同步阻塞的（一次真实运行 1-3 分钟），所以只能用 def。

════════════════════════════════════════════════════════════════════
五、怎么跑起来（三条命令，从 research-agent 目录执行）
════════════════════════════════════════════════════════════════════
    # 1. 启动（会读 .env，监听 127.0.0.1:8000）
    ./.venv/Scripts/python.exe -m app.api

    # 2. 看文档：浏览器打开 http://127.0.0.1:8000/docs
    #    FastAPI 自带 Swagger UI，可以直接点「Try it out」发请求

    # 3. 发一个真实请求（-m 600 是关键，默认 5 秒必超时）
    curl -m 600 -X POST http://127.0.0.1:8000/research \\
         -H "Content-Type: application/json" \\
         -d '{"question":"LangChain 和 LangGraph 的区别是什么"}'

    # 健康检查（不消耗额度、不需要 key）
    curl http://127.0.0.1:8000/health

════════════════════════════════════════════════════════════════════
六、第 7b 天加了什么（日志 + 异常搬家）
════════════════════════════════════════════════════════════════════
改动只有两件事，但都踩过坑：

1. 一个 HTTP 中间件 observe：给每个响应加 X-Elapsed-Ms 头，
   打一行访问日志，并且**兜住一切未处理异常**变成 500。

2. 端点里原来的 4 段 except 全部删掉，改成 3 个 @app.exception_handler
   注册的全局 handler（502 / 504 / 422）。

为什么 500 兜底必须写成中间件，不能用 @app.exception_handler(Exception)：
   那个 handler 挂在 Starlette 的 ServerErrorMiddleware 上，它发完响应
   **仍然会 raise**，TestClient 会直接把异常抛给调用方，
   表现为「测试里根本看不到 500，只看到 traceback」。
   只有自己写中间件，才能把异常吃掉并返回一个干净的 JSON 响应。

为什么 handler 的匹配规则和 except 相反：
   except 按**书写顺序**匹配，所以「具体的写前面」是硬要求。
   exception_handler 按**异常的 MRO** 匹配，注册顺序无关，
   SearchError 和 AgentLoopError 谁先注册都不影响结果（它俩没有继承关系）。
   但 ValueError 范围大，任何 ValueError 都会被它接住，
   所以不要指望「写在后面就优先级低」——这里没有「后面」这个概念。
"""

import logging
import os
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent import MAX_STEPS, MAX_TOOL_CALLS, AgentLoopError, run_agent
from app.report import generate_report, report_to_markdown
from app.schemas import ResearchReport
from app.tools import SearchError

app = FastAPI(
    title="Research Agent API",
    version="0.7.0",
    description="把第 6 天的「检索 → 报告 → 引用回填」流程暴露成 HTTP 接口。",
)


# ====================================================================
# [手敲] 第 7 天块 1：请求模型
# --------------------------------------------------------------------
# 逻辑链：
#   客户端的 JSON → Pydantic 字段 → 每个 Field 的约束在解析时立刻生效
#        ↓ 不合规
#   422 + 一条指明「哪个字段、错在哪」的错误（FastAPI 自动生成，不用写代码）
#
# 为什么校验放在模型上而不是函数里 if：
#   Pydantic 的约束会同时进 OpenAPI 文档，客户端能直接看到「2-200 字」，
#   而 if 判断只存在于代码里，调用方要读源码才知道边界。
# ====================================================================
class ResearchRequest(BaseModel):
    question: str = Field(
        min_length=2,
        max_length=200,
        description="要研究的问题，2-200 字",
        examples=["2026 年诺贝尔物理学奖颁给了谁"],
    )
    max_steps: int = Field(
        default=MAX_STEPS,
        ge=1,
        le=8,
        description="最多几轮模型调用；上限 8 是因为再多基本是模型在兜圈子",
    )
    # 7b 新增：把「工具调用次数上限」也暴露出去。
    # 为什么不让它写死：调试时想在 1 次和 4 次之间对比，改 env 要重启，
    # 改请求参数不用。上限同样是 8，理由与 max_steps 一致。
    max_tool_calls: int = Field(
        default=MAX_TOOL_CALLS,
        ge=1,
        le=8,
        description="最多执行几次工具（每次搜索都花钱，这是成本闸门）",
    )
    verify_links: bool = Field(
        default=True,
        description="是否探测来源链接是否可访问；关掉能快十几秒",
    )


# ====================================================================
# [手敲] 第 7 天块 2：响应模型
# --------------------------------------------------------------------
# 逻辑链：
#   ResearchReport 直接复用（第 6 天的模型），外面再包一层「本次运行的运行信息」。
#   为什么不把 steps / elapsed 塞进 ResearchReport：
#     那是「报告内容」，这是「这次调用的元数据」。混在一起，
#     第 12 天做评估集时导出报告就会多出一堆没用的字段。
# ====================================================================
class ResearchResponse(BaseModel):
    question: str = Field(description="回显请求里的问题，便于日志对账")
    report: ResearchReport = Field(description="结构化报告，含 sources")
    markdown: str = Field(description="渲染好的 Markdown，[n] 已变成可点链接")
    steps: int = Field(description="本次用了几轮模型调用")
    tool_calls: int = Field(description="本次执行了几次工具")
    elapsed_seconds: float = Field(description="整个请求的服务端耗时")


# ====================================================================
# [复制粘贴] 第 7 天块 3：健康检查
# --------------------------------------------------------------------
# 逻辑链：
#   部署之后要能回答「进程活着吗」，这是唯一不需要任何依赖的探针。
#   为什么单独一个接口而不复用 /research：健康检查会被监控每秒打一次，
#   不能让它触发一次真实研究（那要 1-3 分钟且花钱）。
# ====================================================================
@app.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    """只证明进程能响应，不检查模型和搜索服务。"""

    return {"status": "ok"}


# ====================================================================
# [手敲] 第 7b 天块 6：请求观测中间件（计时 + 访问日志 + 500 兜底）
# --------------------------------------------------------------------
# 逻辑链：
#   请求进来 → 记开始时间 → await call_next(request) 交给内层
#        ↓ 内层正常返回（含被 handler 接住的 502/504/422）
#   补 X-Elapsed-Ms 头 + logger.info 一行 → 返回
#        ↓ 内层抛出没人接的异常
#   logger.exception 记录堆栈 → 自己造 500 JSON → 返回
#
# 为什么这里必须 async def：
#   中间件的签名是 ASGI 的，call_next 返回的是 awaitable。
#   写成 def 会把协程对象当成响应返回，直接 500。
#   注意这和端点相反——端点是 def（业务阻塞），中间件是 async def（只是包装）。
#
# 为什么异常分支里也要手写 X-Elapsed-Ms：
#   500 响应是我们自己 new 出来的 JSONResponse，不经过下面那行
#   response.headers[...] = ...，不手写这个头就没有。测试里专门盯这一点。
#
# 为什么用 logger.exception 而不是 logger.error：
#   exception() 会自动带上当前异常的 traceback。500 的排障全靠它，
#   否则日志里只有一句「内部错误」，等于没写。
# ====================================================================
logger = logging.getLogger("app.api")


@app.middleware("http")
async def observe(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """给每个请求计时、记一行访问日志，并兜住未处理异常。"""

    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception as error:  # noqa: BLE001 - 兜底分支就是要把一切都接住
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        logger.exception(
            "未处理异常 %s %s 耗时 %.1fms", request.method, request.url.path, elapsed_ms
        )
        return JSONResponse(
            status_code=500,
            content={"detail": f"内部错误：{error}"},
            headers={"X-Elapsed-Ms": str(elapsed_ms)},
        )

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    logger.info(
        "%s %s -> %d %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    response.headers["X-Elapsed-Ms"] = str(elapsed_ms)
    return response


# ====================================================================
# [手敲] 第 7b 天块 7：三个 typed handler（异常从端点搬到这里）
# --------------------------------------------------------------------
# 逻辑链：
#   端点只管「顺利时怎么走」，异常一律冒出去 → 由这几个函数翻译成响应。
#
# 和原来端点内 except 的逐条对应（文案必须逐字一致，否则测试对不上）：
#   except SearchError     → 502「搜索服务不可用：...」
#   except AgentLoopError  → 504「研究未在限定预算内完成：...」
#   except ValueError      → 422「内容不合规：...」
#   except Exception       → 500「内部错误：...」  ← 这个搬到了中间件，见块 6
#
# 为什么要搬（三个理由，按重要性排）：
#   1. 端点函数只剩「正常路径」，读代码时不用在业务和错误处理之间来回跳。
#   2. 状态码映射集中在一处，改文案不用进业务函数。
#   3. 新增接口时自动继承同一套映射，不会出现「新接口忘了 try/except 就变 500」。
#
# handler 的返回值必须是 Response：这里返回 JSONResponse，
# 而不是 raise HTTPException——已经走到 handler 说明异常被接住了，
# 再 raise 会进下一个 handler，绕圈。
#
# 三个 handler 都要声明 request 参数吗：要。
# Starlette 按签名注入 Request，少写会 TypeError。
# 用不上它也留着，因为以后加「按路径区分文案」时直接就有。
# ====================================================================
@app.exception_handler(SearchError)
async def handle_search_error(request: Request, error: SearchError) -> JSONResponse:
    """上游搜索服务（博查）不可用 → 502。"""

    logger.error("搜索服务失败 %s: %s", request.url.path, error)
    return JSONResponse(status_code=502, content={"detail": f"搜索服务不可用：{error}"})


@app.exception_handler(AgentLoopError)
async def handle_loop_error(request: Request, error: AgentLoopError) -> JSONResponse:
    """预算内没收敛 → 504（不是 500：这不是 bug，是上游太慢/太啰嗦）。"""

    logger.error("预算内未收敛 %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=504,
        content={"detail": f"研究未在限定预算内完成：{error}"},
    )


@app.exception_handler(ValueError)
async def handle_value_error(request: Request, error: ValueError) -> JSONResponse:
    """输入/输出不合规 → 422（问题全空白、模型输出不是 JSON、报告校验失败）。"""

    logger.warning("内容不合规 %s: %s", request.url.path, error)
    return JSONResponse(status_code=422, content={"detail": f"内容不合规：{error}"})


# ====================================================================
# [手敲] 第 7 天块 4：研究接口 —— 7b 之后这里只剩正常路径
# --------------------------------------------------------------------
# 逻辑链：
#   收到请求 → 调 run_agent 拿 sources → 调 generate_report 回填引用
#        → report_to_markdown 渲染 → 打包成 ResearchResponse
#
# 为什么一个 try 都不留：
#   异常已经由块 6 / 块 7 接管。端点里再写 except 会把异常吃掉，
#   上层的 handler 就永远不触发（表现为「handler 是死代码」）。
#   这一步是 7b 真正的「切换点」：删掉 except 的瞬间，
#   原本 96 条测试全部走新路径，数字必须仍是 96。
#
# 为什么 max_tool_calls 要显式透传：
#   run_agent 的默认值来自模块常量，接口层不传就会用常量，
#   请求里那个字段等于白写。Pydantic 收了就要往下传。
# ====================================================================
@app.post("/research", response_model=ResearchResponse, summary="跑一次完整研究")
def research(request: ResearchRequest) -> ResearchResponse:
    """同步执行：检索 → 报告 → 引用回填，一次返回全部结果。

    真实运行要 1-3 分钟（4 轮模型调用 + 3 次搜索 + 链接探测），
    所以调用方的超时不要设成默认的 5 秒：
        curl -m 600 -X POST ...
    """

    started_at = time.perf_counter()

    run = run_agent(
        request.question,
        max_steps=request.max_steps,
        max_tool_calls=request.max_tool_calls,
    )
    report = generate_report(
        request.question,
        sources=run.sources,
        verify_links=request.verify_links,
    )
    markdown = report_to_markdown(report)

    return ResearchResponse(
        question=request.question,
        report=report,
        markdown=markdown,
        steps=run.steps,
        tool_calls=run.tool_call_count,
        elapsed_seconds=round(time.perf_counter() - started_at, 2),
    )


# ====================================================================
# [复制粘贴] 第 7 天块 5：本地启动入口（7b 加了一行日志初始化）
# --------------------------------------------------------------------
# 逻辑链：
#   python -m app.api → 读 .env → setup_logging → uvicorn.run(import string)
#        ↓
#   监听 8000，自动生成 /docs
#
# 为什么 load_dotenv() 写在守卫里而不是模块顶层（和 main.py 同一理由）：
#   顶层调用会让「import app.api」也有副作用（读文件、改 os.environ），
#   测试里 import 一次就把真实 key 读进来了。
#
# 为什么 setup_logging 放在 load_dotenv **之后**：
#   它要读 LOG_LEVEL / LOG_FILE 两个环境变量。放在前面，
#   .env 里配的级别和路径全部读不到，静默退回默认值。
#
# 为什么 reload=False：
#   reload 模式会把 app 放到子进程里跑，子进程不继承这里 load 的环境变量，
#   表现为「明明有 key 却报缺少 key」。
#   要热重载就自己在命令行跑 uvicorn，并先把环境变量导出到 shell。
# ====================================================================
if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    from app.logging_setup import setup_logging

    load_dotenv()
    setup_logging(log_file=os.getenv("LOG_FILE"))
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)
