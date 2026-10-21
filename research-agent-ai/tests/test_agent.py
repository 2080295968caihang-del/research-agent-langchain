"""第 1 天模型调用的离线测试。"""

import pytest

from app.agent import ask_model, create_model


def test_ask_model_rejects_empty_question() -> None:
    """空问题应在发起网络请求前被拒绝。"""

    with pytest.raises(ValueError, match="问题不能为空"):
        ask_model("   ")


def test_create_model_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 Key 时应给出明确提示，而不是发出不完整请求。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_model()
