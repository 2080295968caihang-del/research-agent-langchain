"""day7:http接口层   

    1. 把 HTTP 请求体解析成 Pydantic 模型（参数校验交给 Pydantic）
    2. 按顺序调用上面三个函数
    3. 把内部异常翻译成 HTTP 状态码

"""

import time
import logging
import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agent import MAX_STEPS,MAX_TOOL_CALLS, AgentLoopError, run_agent
from app.report import generate_report, report_to_markdown
from app.schemas import ResearchReport
from app.tools import SearchError

app = FastAPI(
    title="Research Agent API",
    version="0.7.0",
    description="把第 6 天的「检索 → 报告 → 引用回填」流程暴露成 HTTP 接口。",
)

#请求模型
class ResearchRequest(BaseModel):
    question:str = Field(
        min_length=2,
        max_length=200,
        description="要研究的问题，2-200 字",
        examples=["2026 年诺贝尔物理学奖颁给了谁"],
    )
    max_steps:int = Field(
        default= MAX_STEPS,
        ge = 1,
        le = 8,
        description="最多几轮模型调用；上限 8 是因为再多基本是模型再兜圈子",
    )
    max_tool_calls: int = Field(
        default=MAX_TOOL_CALLS,
        ge=1,
        le=8,
        description="最多执行几次工具（每次搜索都花钱，这是成本闸门）",
    )
    verify_links:bool = Field(
        default=True,
        description="是否探测来源链接是否可访问；关掉能快十几秒"
    )

#响应模型
class ResearchResponse(BaseModel):
    question: str = Field(description="回显请求里的问题，便于日志对账")
    report: ResearchReport = Field(description="结构化报告，含 sources")
    markdown: str = Field(description="渲染好的 Markdown，[n] 已变成可点链接")
    steps: int = Field(description="本次用了几轮模型调用")
    tool_calls: int = Field(description="本次执行了几次工具")
    elapsed_seconds: float = Field(description="整个请求的服务端耗时")

#健康检查
@app.get("/health", summary="健康检查")
def health() -> dict[str, str]:
    """只证明进程能响应，不检查模型和搜索服务。"""

    return {"status": "ok"}

logger = logging.getLogger("app.api")

def _question_of(request: Request) -> str:
    """从 request.state 取问题原文；没设过（比如 422 在校验阶段就失败）返回 "-"。"""
    return getattr(request.state, "question", "-")



@app.middleware("http")
async def observe(
    request:Request,
    call_next:Callable[[Request],Awaitable[Response]]
) -> Response:

    started =time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as error:
        elapsed_ms = round((time.perf_counter() - started)*1000,1)
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

@app.exception_handler(SearchError)
async def handle_search_error(request: Request, error: SearchError) -> JSONResponse:
    logger.error("搜索服务失败 question=%r: %s", _question_of, error)
    return JSONResponse(
        status_code=502, 
        content={"detail": f"搜索服务不可用：{error}"}
        )


@app.exception_handler(AgentLoopError)
async def handle_loop_error(request: Request, error: AgentLoopError) -> JSONResponse:
    logger.error("预算内未收敛 question=%r: %s", _question_of, error)
    return JSONResponse(
        status_code=504,              
        content={"detail": f"研究未在限定预算内完成：{error}"}
        )


@app.exception_handler(ValueError)
async def handle_value_error(request: Request, error: ValueError) -> JSONResponse:
    logger.warning("内容不合规 question=%r: %s", _question_of, error)
    return JSONResponse(
        status_code=422, 
        content={"detail": f"内容不合规：{error}"}
        )

#研究接口
@app.post("/research",response_model=ResearchResponse,summary="跑一次完整研究")
def research(payload: ResearchRequest) -> ResearchResponse:
    started_at = time.perf_counter()

    run = run_agent(
        payload.question,
        max_steps=payload.max_steps,
        max_tool_calls=payload.max_tool_calls,
        )
    report = generate_report(
        payload.question,
        sources=run.sources,
        verify_links=payload.verify_links,
    )
    markdown = report_to_markdown(report)
    

    return ResearchResponse(
        question=payload.question,
        report=report,
        markdown=markdown,
        steps=run.steps,
        tool_calls=run.tool_call_count,
        elapsed_seconds=round(time.perf_counter() - started_at, 2),
    )


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    from app.logging_setup import setup_logging

    load_dotenv()
    setup_logging(log_file=os.getenv("LOG_FILE"))
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)
