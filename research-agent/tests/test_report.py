"""第 2 天：结构化研究报告测试。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.report import build_report_prompt, generate_report, parse_report
from app.schemas import ResearchQuestion, ResearchReport


class FakeModel:
    """不访问网络的假模型，用固定文本模拟 LangChain 返回值。"""

    def __init__(self, content: str) -> None:
        self.content = content
        self.messages = None

    def invoke(self, messages: list[object]) -> SimpleNamespace:
        """记录收到的消息，并返回一个类似 AIMessage 的对象。"""

        self.messages = messages
        return SimpleNamespace(content=self.content)


def test_research_question_rejects_blank_text() -> None:
    """研究问题不能是空字符串或只有空格。"""

    with pytest.raises(ValidationError):
        ResearchQuestion(question="   ")


def test_parse_report_returns_validated_pydantic_model() -> None:
    """有效 JSON 应转换成 ResearchReport，而不是普通字典。"""

    raw = (
        '{"title":"LangChain 简介","summary":"一个应用框架",'
        '"key_findings":["支持模型调用"],"conclusion":"适合构建 LLM 应用",'
        '"limitations":["本报告未引用外部资料"]}'
    )

    report = parse_report(raw)

    assert isinstance(report, ResearchReport)
    assert report.title == "LangChain 简介"
    assert report.key_findings == ["支持模型调用"]


def test_parse_report_accepts_markdown_json_fence() -> None:
    """模型偶尔会包裹 ```json 代码块，解析器应兼容这种输出。"""

    raw = '''```json
{"title":"标题","summary":"摘要","key_findings":["发现"],"conclusion":"结论","limitations":[]}
```'''

    report = parse_report(raw)

    assert report.conclusion == "结论"


def test_parse_report_rejects_invalid_output() -> None:
    """无法解析或缺少必需字段时，应抛出清晰的 ValueError。"""

    with pytest.raises(ValueError, match="有效 JSON|字段"):
        parse_report("这不是 JSON 报告")


def test_generate_report_uses_model_and_validates_response() -> None:
    """报告生成函数应发送问题给模型，并返回校验后的报告。"""

    model = FakeModel(
        '{"title":"测试报告","summary":"测试摘要",'
        '"key_findings":["测试发现"],"conclusion":"测试结论",'
        '"limitations":[]}'
    )

    report = generate_report("什么是 LangChain？", model=model)

    assert report.title == "测试报告"
    assert model.messages is not None
    assert "什么是 LangChain？" in str(model.messages[-1].content)


def test_build_report_prompt_requires_non_blank_question() -> None:
    """Prompt 构造也应在网络请求前拒绝空问题。"""

    with pytest.raises(ValidationError):
        build_report_prompt("  ")
