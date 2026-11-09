"""第 7 天：HTTP 接口层测试。

全部用 fastapi.testclient.TestClient —— 它不起真服务器、不占端口，
直接把请求喂给 ASGI app，所以跑得快、可以在 CI 里随便跑。

这一层要钉住的不是业务逻辑（那是 71 条旧测试的事），而是三件事：
    1. 请求体能被正确解析成模型，非法输入拿到 422
    2. 内部三个函数的调用顺序与参数传递没接错
    3. 每种异常映射到约定的状态码，一个都不能混
"""

from typing import Any

import asyncio
import logging
import time
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.agent import AgentLoopError, AgentRun, record_usage, reset_usage
from app.api import app
from app.schemas import ResearchReport, SourceRef
from app.tools import SearchError

client = TestClient(app)


# ====================================================================
# 两个构造器：把「一次正常的运行结果」固定下来
# --------------------------------------------------------------------
# 为什么造 ResearchReport 要带 sources：第 6 天的校验器规定
# 「正文引用了 [1]，就必须有 index=1 的来源」，否则构造直接失败。
# ====================================================================
def _fake_report() -> ResearchReport:
    return ResearchReport(
        title="测试标题",
        summary="摘要 [1]",
        key_findings=["发现 [1]"],
        conclusion="结论 [1]",
        limitations=["资料有限"],
        sources=[SourceRef(index=1, title="来源甲", url="https://a.example/1")],
    )


def _fake_run(sources: list[dict[str, str]] | None = None) -> AgentRun:
    return AgentRun(
        answer="模型回答",
        steps=3,
        tool_call_count=2,
        tool_outputs=["<search_results/>"],
        sources=sources
        if sources is not None
        else [{"title": "来源甲", "url": "https://a.example/1", "snippet": ""}],
    )


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run: Any = None,
    report: Any = None,
) -> dict[str, Any]:
    """把 run_agent / generate_report 换成假函数，并记录调用参数。"""

    calls: dict[str, Any] = {}
    run_impl = run if run is not None else (lambda *a, **k: _fake_run())
    report_impl = report if report is not None else (lambda *a, **k: _fake_report())

    def fake_run(question: str, **kwargs: Any) -> Any:
        calls["question"] = question
        calls["run_kwargs"] = kwargs
        return run_impl(question, **kwargs)

    def fake_generate(question: str, **kwargs: Any) -> Any:
        calls["generate_question"] = question
        calls["generate_kwargs"] = kwargs
        return report_impl(question, **kwargs)

    monkeypatch.setattr("app.api.run_agent", fake_run)
    monkeypatch.setattr("app.api.generate_report", fake_generate)
    return calls


# ====================================================================
# 一、服务能起来
# ====================================================================
def test_health_returns_ok() -> None:
    """健康检查必须不依赖模型和搜索服务，否则监控一起挂。"""

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_lists_both_routes() -> None:
    """文档能生成，说明两个路由的签名与模型都是合法的。

    改错 response_model 或漏写返回注解，这里会先红。
    """

    schema = client.get("/openapi.json").json()

    assert set(schema["paths"]) == {"/health", "/research"}


# ====================================================================
# 二、正常路径：返回结构化 JSON
# ====================================================================
def test_research_returns_structured_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """一次成功请求要同时给出 report（结构化）与 markdown（可渲染）。"""

    _patch_pipeline(monkeypatch)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 200
    body = response.json()
    assert body["question"] == "量子计算有什么用"
    assert body["report"]["summary"] == "摘要 [1]"
    assert body["report"]["sources"] == [
        {"index": 1, "title": "来源甲", "url": "https://a.example/1"}
    ]
    assert body["markdown"].startswith("# 测试标题")
    assert body["steps"] == 3
    assert body["tool_calls"] == 2
    assert isinstance(body["elapsed_seconds"], float)


def test_research_forwards_sources_to_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_agent 收集到的 sources 必须交给 generate_report。

    这一条是整条链路最容易断的地方：少传 sources，报告会变成
    「正文有 [1] 但来源清单为空」，第 6 天的校验器直接报错。
    """

    sources = [{"title": "来源乙", "url": "https://b.example/2", "snippet": "s"}]
    calls = _patch_pipeline(monkeypatch, run=lambda *a, **k: _fake_run(sources))

    client.post("/research", json={"question": "量子计算有什么用"})

    assert calls["generate_kwargs"]["sources"] == sources


def test_verify_links_defaults_to_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 verify_links 时默认校验链接，避免报告里出现过期来源。"""

    calls = _patch_pipeline(monkeypatch)

    client.post("/research", json={"question": "量子计算有什么用"})

    assert calls["generate_kwargs"]["verify_links"] is True


def test_verify_links_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式传 false 要能透传到报告层（调试时省十几秒）。"""

    calls = _patch_pipeline(monkeypatch)

    client.post(
        "/research",
        json={"question": "量子计算有什么用", "verify_links": False},
    )

    assert calls["generate_kwargs"]["verify_links"] is False


def test_max_steps_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_steps 要透传，否则客户端调小预算不生效。"""

    calls = _patch_pipeline(monkeypatch)

    client.post("/research", json={"question": "量子计算有什么用", "max_steps": 2})

    assert calls["run_kwargs"]["max_steps"] == 2


# ====================================================================
# 三、参数校验：422 全部由 Pydantic 产生，不写一行代码
# --------------------------------------------------------------------
# 这里测的是「约束真的挂上了」，不是测 Pydantic 本身。
# 改 Field 的 min_length / ge / le，对应测试会红。
# ====================================================================
@pytest.mark.parametrize(
    "payload",
    [
        {"question": "a"},                       # 低于 min_length=2
        {"question": "问" * 201},                # 超过 max_length=200
        {},                                       # 缺字段
        {"question": "量子计算有什么用", "max_steps": 0},   # 低于 ge=1
        {"question": "量子计算有什么用", "max_steps": 9},   # 超过 le=8
        {"question": "量子计算有什么用", "max_steps": "两次"},  # 类型错
    ],
    ids=["too_short", "too_long", "missing", "steps_low", "steps_high", "wrong_type"],
)
def test_invalid_payload_gets_422(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """六种非法输入都必须被拦在进入业务逻辑之前。"""

    calls = _patch_pipeline(monkeypatch)

    response = client.post("/research", json=payload)

    assert response.status_code == 422
    assert calls == {}, "参数非法时不应该调用任何业务函数"


def test_blank_question_falls_through_to_value_error() -> None:
    """"  "（两个空格）长度够，Pydantic 放行，由 run_agent 抛 ValueError。

    这条不 monkeypatch run_agent：它在建模型之前就校验，不会发网络请求。
    """

    response = client.post("/research", json={"question": "  "})

    assert response.status_code == 422
    assert "不能为空" in response.json()["detail"]


# ====================================================================
# 四、异常映射：四个状态码各一条，混了就红
# ====================================================================
def test_agent_loop_error_maps_to_504(monkeypatch: pytest.MonkeyPatch) -> None:
    """步数耗尽 = 上游没在预算内收敛 → 504，不是 500。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AgentLoopError("超过最大步数 5 仍未给出回答")

    _patch_pipeline(monkeypatch, run=boom)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 504
    assert "预算" in response.json()["detail"]


def test_search_error_maps_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    """上游搜索服务挂了 → 502，客户端应重试而不是改请求。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise SearchError("博查返回 401")

    _patch_pipeline(monkeypatch, run=boom)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 502
    assert "搜索服务" in response.json()["detail"]


def test_model_output_value_error_maps_to_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型输出不是合法 JSON → ValueError → 422（内容不合规，不是服务器崩了）。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("模型输出不是合法 JSON")

    _patch_pipeline(monkeypatch, report=boom)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 422
    assert "不合规" in response.json()["detail"]


def test_unexpected_error_maps_to_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """没预料的异常必须变成 500，而不是把栈直接吐给客户端。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("缺少 API key")

    _patch_pipeline(monkeypatch, run=boom)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 500
    assert "内部错误" in response.json()["detail"]
    assert "Traceback" not in response.text


# ====================================================================
# 五、合法边界：探针要落在区间里面，不能只测两端
# --------------------------------------------------------------------
# 上一块（三、参数校验）全是「非法要拦」。这一块测反方向：
# 值落在合法区间里时必须放行。
#
# 为什么必须补：只测 `"a"`（太短）和 `"问"*201`（太长）的话，
# 把 max_length 从 200 改成 100、把 ge 从 1 改成 2，
# 90 条测试一条都不会红 —— 非法用例在两个阈值下都仍然非法。
# 边界值必须有一条「刚好等于上下界且合法」的用例，缩放才有人管。
# ====================================================================
@pytest.mark.parametrize(
    "payload",
    [
        {"question": "问" * 2},                              # 长度 = min_length 下界
        {"question": "问" * 200},                            # 长度 = max_length 上界
        {"question": "量子计算有什么用", "max_steps": 1},      # max_steps = ge 下界
        {"question": "量子计算有什么用", "max_steps": 8},      # max_steps = le 上界
    ],
    ids=["question_min", "question_max", "steps_min", "steps_max"],
)
def test_valid_boundary_is_accepted(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """四个边界值都必须 200，且真的进了业务层。"""

    calls = _patch_pipeline(monkeypatch)

    response = client.post("/research", json=payload)

    assert response.status_code == 200
    assert calls != {}, "合法输入必须调用业务函数"


# ====================================================================
# 六、端点必须是 def，不能是 async def
# --------------------------------------------------------------------
# 这是 90 条测试里唯一一条「测执行模型」而不是「测返回值」的。
#
# 为什么需要它：TestClient 是同步的，它把请求喂给 app，就算端点是
# async def，也只会在一个事件循环里跑一次 —— 阻塞事件循环这件事
# 在单请求下完全看不出来。实测：把 def 改成 async def，18 条全绿。
#
# 怎么测：用 httpx.ASGITransport 在同一个事件循环里发两个并发请求，
# 假 run_agent 里 sleep 0.3s。
#   def 端点      → Starlette 用 run_in_threadpool 丢线程 → 两个请求并行 → 约 0.3s
#   async def 端点 → time.sleep 直接阻塞事件循环 → 两个请求串行 → 约 0.6s
# 阈值取 0.3 * 1.6 = 0.48s，两种情况分得很开。
# ====================================================================
def test_endpoint_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """两个并发请求的总耗时必须接近单次，而不是两倍。"""

    delay = 0.3

    def slow_run(*args: Any, **kwargs: Any) -> Any:
        time.sleep(delay)
        return _fake_run()

    _patch_pipeline(monkeypatch, run=slow_run)

    async def two_parallel_requests() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as async_client:
            started_at = time.perf_counter()
            await asyncio.gather(
                async_client.post("/research", json={"question": "量子计算有什么用"}),
                async_client.post("/research", json={"question": "量子计算有什么用"}),
            )
            return time.perf_counter() - started_at

    elapsed = asyncio.run(two_parallel_requests())

    assert elapsed < delay * 1.6, (
        f"两个并发请求耗时 {elapsed:.2f}s（单次 {delay}s）——"
        "端点可能已经改成 async def，阻塞调用把事件循环占满了"
    )


# ====================================================================
# 七、7b 新增：耗时头 / handler 接管 / 成本闸门透传
# --------------------------------------------------------------------
# 这一块钉的是「7b 把异常处理搬出端点」之后能观察到的后果：
#   · 每个响应都带 X-Elapsed-Ms（客户端能看服务端耗时）
#   · 502 是注册的 handler 产出的，不是端点里的 except
#   · max_tool_calls 真的传到了 run_agent（成本闸门不是装饰品）
# ====================================================================
def test_health_response_carries_elapsed_header() -> None:
    """200 也要带头：监控可以只读响应头画耗时曲线，不用解析 body。"""

    response = client.get("/health")

    assert response.status_code == 200
    assert float(response.headers["X-Elapsed-Ms"]) >= 0


def test_error_response_carries_elapsed_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """出错时更想看耗时，所以 502 也必须带 X-Elapsed-Ms。

    实现细节值得记住：头是**观察中间件**补的，不是 handler 补的。
    handler 在 ExceptionMiddleware 内层，中间件在外层，
    call_next 拿到的是 handler 造好的响应，所以外层还能往上加头。
    把中间件里那行 `response.headers[...]` 删掉，这条会红。
    """

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise SearchError("博查返回 401")

    _patch_pipeline(monkeypatch, run=boom)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 502
    assert float(response.headers["X-Elapsed-Ms"]) >= 0


def test_search_error_is_logged_by_the_handler(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """502 必须由 @app.exception_handler 产出，而不是端点里的 try/except。

    怎么区分两者：handler 里有专属的一行
    `logger.error("搜索服务失败 ...")`，端点内的 except 没有这行。
    caplog 抓到它就说明走的是 handler。

    这是一条「改回去就红」的测试：把三个 handler 删掉、
    在端点里恢复 raise HTTPException(502)，状态码仍然是 502，
    但这条会因为日志里没有「搜索服务失败」而红。
    """

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise SearchError("博查返回 401")

    _patch_pipeline(monkeypatch, run=boom)

    with caplog.at_level(logging.ERROR, logger="app.api"):
        response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 502
    assert "搜索服务失败" in caplog.text


def test_max_tool_calls_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """成本闸门要能收紧：客户端传 1，就必须真的只允许 1 次工具调用。

    漏透传会怎样：请求模型收了字段、端点忘了传给 run_agent，
    校验全绿、返回 200，但花掉的钱是默认值 —— 这条能抓住。
    """

    calls = _patch_pipeline(monkeypatch)

    client.post(
        "/research", json={"question": "量子计算有什么用", "max_tool_calls": 1}
    )

    assert calls["run_kwargs"]["max_tool_calls"] == 1


def test_max_tool_calls_out_of_range_gets_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9 越界（le=8），必须在进业务层之前被 Pydantic 拦下。"""

    calls = _patch_pipeline(monkeypatch)

    response = client.post(
        "/research", json={"question": "量子计算有什么用", "max_tool_calls": 9}
    )

    assert response.status_code == 422
    assert calls == {}, "参数越界时不应该调用任何业务函数"



# ====================================================================
# 第 9 天第 0 步（B5）：502 的日志里必须有 question
# --------------------------------------------------------------------
# 为什么这条最要紧：docx 的验收标准写的是「502 时日志里有 query 和原始异常」，
# 但在补这条之前，109 条测试里**没有一条读过日志内容**。
# 于是 api.py 里 `_question_of` 少写一对括号（把函数对象打进日志）、
# 端点忘了写 request.state.question，全都能一路全绿通过。
# ====================================================================
def test_502_log_contains_question(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """搜索失败时，ERROR 行里要同时出现问题原文和原始异常。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise SearchError("博查返回 401")

    _patch_pipeline(monkeypatch, run=boom)

    with caplog.at_level(logging.ERROR, logger="app.api"):
        response = client.post("/research", json={"question": "2026诺贝尔物理学奖"})

    assert response.status_code == 502
    assert "question='2026诺贝尔物理学奖'" in caplog.text, (
        f"日志里没有 question 原文，实际是：{caplog.text}"
    )
    assert "博查返回 401" in caplog.text


# ====================================================================
# 第 9 天：回包要带上用量、分段耗时与绝对时间
# --------------------------------------------------------------------
# 这条盯的是「接没接上」，不是「算得准不准」（那是 test_agent 的事）：
#   ① 累加器里的数字有没有真的流到响应里
#   ② 时间戳是不是带时区的 ISO 8601（naive 会被 fromisoformat 吃掉但不带时区）
#   ③ 没配单价时必须是 null，不能是 0
# ====================================================================
def test_response_carries_usage_and_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /research 的响应要带 usage / timings / 两个绝对时间戳。"""

    def run_with_usage(*args: Any, **kwargs: Any) -> AgentRun:
        # 真跑时 run_agent 自己会 reset；这里换成假函数，就得自己兜住，
        # 否则 ContextVar 会把上一条测试的累计带进来（同线程、同上下文）。
        reset_usage()
        record_usage(
            AIMessage(
                content="答",
                usage_metadata={
                    "input_tokens": 500,
                    "output_tokens": 50,
                    "total_tokens": 550,
                    "input_token_details": {"cache_read": 100},
                },
            )
        )
        return _fake_run()

    _patch_pipeline(monkeypatch, run=run_with_usage)
    for name in ("PRICE_INPUT_PER_M", "PRICE_OUTPUT_PER_M", "PRICE_CACHE_READ_PER_M"):
        monkeypatch.delenv(name, raising=False)

    response = client.post("/research", json={"question": "量子计算有什么用"})

    assert response.status_code == 200
    body = response.json()

    assert body["usage"]["input_tokens"] == 500
    assert body["usage"]["output_tokens"] == 50
    assert body["usage"]["cache_read"] == 100
    assert body["usage"]["model_calls"] == 1
    assert body["usage"]["estimated_cost_cny"] is None, "没配单价必须是 null，不是 0"

    assert body["timings"]["model_ms"] >= 0
    assert body["timings"]["tool_ms"] >= 0
    assert body["timings"]["report_ms"] >= 0

    started = datetime.fromisoformat(body["started_at"])
    finished = datetime.fromisoformat(body["finished_at"])
    assert started.tzinfo is not None, "时间戳必须带时区，naive datetime 读的人会算错"
    assert started <= finished
