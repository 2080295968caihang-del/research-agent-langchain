"""day8: `ui/client.py` 的八条测试。

全部离线：monkeypatch `requests.post`，不发真实请求、不花模型钱。

**不 import streamlit**：只测「请求 + 错误翻译」这一层。
界面（`ui/app.py`）没有可断言的逻辑，它只是把 client 的结果摆出来，
交给真机验收；真在这儿 import streamlit，会给整个套件多摊一份 import 时间。
"""

from typing import Any

import pytest
import requests

from ui.client import ResearchAPIError, post_research


class _FakeResponse:
    """只实现 client 用到的两个成员：`status_code` 和 `json()`。"""

    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        # payload 为 None 时模拟「响应不是 JSON」——代理或网关拦截的典型样子。
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


@pytest.fixture
def fake_post(monkeypatch):
    """把 `requests.post` 换成可编程的假函数。

    返回 `(calls, outcome)`：
      · `calls` 记录每次调用的 url 与 kwargs
      · `outcome["value"]` 放 `_FakeResponse`（正常返回）或异常实例（要抛出）
    """

    calls: list[dict[str, Any]] = []
    outcome: dict[str, Any] = {}

    def _post(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})

        value = outcome["value"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(requests, "post", _post)
    return calls, outcome


def test_200_returns_payload_and_uses_default_timeout(fake_post) -> None:
    """200 就把 JSON 原样交出去，同时确认默认超时真的传到了 requests。"""

    calls, outcome = fake_post
    payload = {
        "question": "问题",
        "report": {"title": "标题", "sources": []},
        "markdown": "# 标题",
        "steps": 2,
        "tool_calls": 1,
        "elapsed_seconds": 36.5,
    }
    outcome["value"] = _FakeResponse(200, payload)

    assert post_research("问题") == payload
    assert calls[0]["timeout"] == 180


def test_url_and_body_are_built_from_arguments(fake_post) -> None:
    """base_url 末尾多一条斜杠也要拼出正确的 /research，请求体只带 question。"""

    calls, outcome = fake_post
    outcome["value"] = _FakeResponse(200, {})

    post_research("问题", base_url="http://127.0.0.1:8000/", timeout=5)

    assert calls[0]["url"] == "http://127.0.0.1:8000/research"
    assert calls[0]["json"] == {"question": "问题"}
    assert calls[0]["timeout"] == 5


def test_connect_error_has_no_status_and_tells_how_to_start(fake_post) -> None:
    """连不上时 status 必须是 None，文案要告诉人怎么把后端起起来。"""

    _, outcome = fake_post
    outcome["value"] = requests.ConnectionError("Connection refused")

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("问题")

    assert excinfo.value.status is None
    assert "连不上" in excinfo.value.detail
    assert "app.api" in excinfo.value.detail


def test_read_timeout_mentions_seconds_and_still_running(fake_post) -> None:
    """读超时要说清「等了多少秒」和「服务可能还在跑」。"""

    _, outcome = fake_post
    outcome["value"] = requests.ReadTimeout("Read timed out")

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("问题", timeout=7)

    assert excinfo.value.status is None
    assert "7 秒" in excinfo.value.detail
    assert "还在跑" in excinfo.value.detail


def test_connect_timeout_is_reported_as_unreachable(fake_post) -> None:
    """连接阶段超时要报「连不上」，不能说「服务还在跑」。

    `ConnectTimeout` 的 MRO 是 ConnectTimeout → ConnectionError → Timeout
    （实测），同时属于两个父类，**谁在前谁接走**。这条就是用来锁顺序的：
    把 `except ConnectionError` 挪到 `Timeout` 后面，它会变红。
    """

    _, outcome = fake_post
    outcome["value"] = requests.ConnectTimeout("connect timed out")

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("问题", timeout=7)

    assert excinfo.value.status is None
    assert "连不上" in excinfo.value.detail
    assert "还在跑" not in excinfo.value.detail


def test_422_list_detail_is_flattened_to_chinese(fake_post) -> None:
    """pydantic 的 detail 是列表，必须拍平成一句中文，不能把英文原文糊给用户。"""

    _, outcome = fake_post
    outcome["value"] = _FakeResponse(
        422,
        {
            "detail": [
                {
                    "type": "string_too_short",
                    "loc": ["body", "question"],
                    "msg": "String should have at least 2 characters",
                    "input": "短",
                }
            ]
        },
    )

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("短")

    detail = excinfo.value.detail
    assert excinfo.value.status == 422
    assert isinstance(detail, str)
    assert "输入有问题" in detail
    assert "研究问题" in detail
    assert "太短了" in detail


def test_500_maps_to_internal_error_and_keeps_backend_detail(fake_post) -> None:
    """500 用固定文案，但后端那句 detail 要留着——排障时它是唯一的线索。"""

    _, outcome = fake_post
    outcome["value"] = _FakeResponse(500, {"detail": "内部错误：boom"})

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("问题")

    assert excinfo.value.status == 500
    assert "内部错误" in excinfo.value.detail
    assert "boom" in excinfo.value.detail


def test_non_json_error_body_does_not_crash(fake_post) -> None:
    """502 的 body 常常是 HTML（网关页），不能因为 json() 抛错就漏出英文堆栈。"""

    _, outcome = fake_post
    outcome["value"] = _FakeResponse(502, None)

    with pytest.raises(ResearchAPIError) as excinfo:
        post_research("问题")

    assert excinfo.value.status == 502
    assert "搜索服务不可用" in excinfo.value.detail
    assert "没有更多说明" in excinfo.value.detail
