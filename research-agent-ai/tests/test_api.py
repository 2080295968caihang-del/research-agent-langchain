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
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agent import AgentLoopError, AgentRun
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

