"""day7:http接口层   

    1. 把 HTTP 请求体解析成 Pydantic 模型（参数校验交给 Pydantic）
    2. 按顺序调用上面三个函数
    3. 把内部异常翻译成 HTTP 状态码

"""

import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import MAX_STEPS, AgentLoopError, run_agent
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

#研究接口
@app.post("/research",response_model=ResearchResponse,summary="跑一次完整研究")
def research(request: ResearchRequest) -> ResearchResponse:
    started_at = time.perf_counter()

    try:
        run = run_agent(request.question,max_steps=request.max_steps)
        report = generate_report(
            request.question,
            sources=run.sources,
            verify_links=request.verify_links,
        )
        markdown = report_to_markdown(report)
    except SearchError as error:
        # 上游搜索服务（博查）不可用：不是我们的错，也不是客户端的错。
        raise HTTPException(status_code=502, detail=f"搜索服务不可用：{error}") from error
    except AgentLoopError as error:
        # 预算内没收敛：语义是「上游太慢/太啰嗦」，给 504 而不是 500。
        raise HTTPException(status_code=504, detail=f"研究未在限定预算内完成：{error}") from error
    except ValueError as error:
        # 问题全空白、模型输出不是 JSON、报告校验失败 —— 都算输入/输出不合规。
        raise HTTPException(status_code=422, detail=f"内容不合规：{error}") from error
    except Exception as error:  # noqa: BLE001 - 兜底分支就是要把一切都接住
        # 配置缺失（没有 API key）、网络异常等未预料问题。
        raise HTTPException(status_code=500, detail=f"内部错误：{error}") from error

    return ResearchResponse(
        question=request.question,
        report=report,
        markdown=markdown,
        steps=run.steps,
        tool_calls=run.tool_call_count,
        elapsed_seconds=round(time.perf_counter() - started_at, 2),
    )


if __name__ == "__main__":
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)
