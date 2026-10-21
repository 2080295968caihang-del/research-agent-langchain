"""第 1 天：模型客户端和最小调用逻辑。"""

import os

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI


def create_model() -> ChatOpenAI:
    """根据环境变量创建聊天模型客户端。

    API Key 由 ChatOpenAI 从 OPENAI_API_KEY 环境变量读取，
    所以密钥不会出现在源码中。
    """

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "没有找到 OPENAI_API_KEY。请复制 .env.example 为 .env，"
            "然后填写真实 API Key。"
        )

    model_name = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()

    model_options = {
        "model": model_name,
        "temperature": 0,
        "timeout": 60,
        "max_retries": 2,
    }
    if base_url:
        model_options["base_url"] = base_url

    return ChatOpenAI(**model_options)


def ask_model(question: str) -> str:
    """发送一条用户问题，并返回模型生成的文本。"""

    question = question.strip()
    if not question:
        raise ValueError("问题不能为空，请输入一句问题。")

    model = create_model()
    response = model.invoke([HumanMessage(content=question)])
    return str(response.content)
