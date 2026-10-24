"""第 2 天的结构化报告生成。

这里暂时不搜索网页，也不使用 Tool Calling；流程只有：
验证问题 -> 构造 Prompt -> 调用模型 -> 解析 JSON -> Pydantic 校验。
"""

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.agent import create_model
from app.schemas import ResearchQuestion, ResearchReport


REPORT_SYSTEM_PROMPT = """你是一个严谨的研究报告助手。
请根据用户的问题生成一份简洁、结构化的报告。
只返回一个合法 JSON 对象，不要使用 Markdown 代码块，不要添加 JSON 以外的解释。
JSON 必须包含以下字段：
- title: 字符串，报告标题
- summary: 字符串，摘要
- key_findings: 字符串数组，至少一项关键发现
- conclusion: 字符串，结论
- limitations: 字符串数组，列出局限性，没有时返回空数组
不要虚构来源 URL；第二天暂时不接入搜索和引用。
"""


def build_report_prompt(question: str) -> str:
    """验证研究问题，并构造发送给模型的用户 Prompt。"""

    validated_question = ResearchQuestion(question=question)
    return (
        "请研究下面的问题，并严格按照系统消息规定的 JSON 字段返回报告。\n\n"
        f"研究问题：{validated_question.question}"
    )


def _remove_json_fence(raw_text: str) -> str:
    """去掉模型偶尔添加的 ```json 代码块标记。"""

    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        # 第一行是 ``` 或 ```json，最后一行是结束标记。
        return "\n".join(lines[1:-1]).strip()
    return text


def parse_report(raw_text: str) -> ResearchReport:
    """将模型文本解析为经过 Pydantic 校验的 ResearchReport。"""

    try:
        payload: Any = json.loads(_remove_json_fence(raw_text))
    except json.JSONDecodeError as error:
        raise ValueError("模型没有返回有效 JSON 报告") from error

    try:
        return ResearchReport.model_validate(payload)
    except ValidationError as error:
        raise ValueError("模型返回的报告字段不符合要求") from error


def generate_report(question: str, model: Any | None = None) -> ResearchReport:
    """调用模型生成并校验一份结构化研究报告。

    ``model`` 是可选的依赖注入参数。正式运行时不传它，函数会创建真实
    ChatOpenAI；测试时传入假模型，就可以在不消耗 API 额度的情况下验证逻辑。
    """

    prompt = build_report_prompt(question)
    model = model or create_model()
    response = model.invoke(
        [
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
    )
    return parse_report(str(response.content))
