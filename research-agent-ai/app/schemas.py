"""第 2 天的数据结构。

把输入和输出定义成明确的 Pydantic 模型，可以让程序在模型返回内容后
立刻发现字段缺失、类型错误和空文本，而不是把不可靠的字典继续传下去。
"""

from pydantic import BaseModel, Field, field_validator


class ResearchQuestion(BaseModel):
    """用户提交的研究问题。"""

    question: str = Field(description="用户希望研究的问题")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        """去掉首尾空格，并拒绝空问题。"""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("研究问题不能为空")
        return cleaned


class ResearchReport(BaseModel):
    """第 2 天的结构化研究报告。"""

    title: str = Field(min_length=1, description="报告标题")
    summary: str = Field(min_length=1, description="报告摘要")
    key_findings: list[str] = Field(
        min_length=1,
        description="报告中的关键发现，至少包含一项",
    )
    conclusion: str = Field(min_length=1, description="报告结论")
    limitations: list[str] = Field(
        default_factory=list,
        description="研究局限性，可以为空",
    )

    @field_validator("title", "summary", "conclusion")
    @classmethod
    def text_fields_must_not_be_blank(cls, value: str) -> str:
        """拒绝只有空格的必填文本字段。"""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("报告必填文本不能为空")
        return cleaned

    @field_validator("key_findings", "limitations")
    @classmethod
    def list_items_must_be_non_blank(cls, values: list[str]) -> list[str]:
        """清理列表项目，并拒绝空的发现或局限性描述。"""

        cleaned_values = [item.strip() for item in values]
        if any(not item for item in cleaned_values):
            raise ValueError("报告列表项目不能为空")
        return cleaned_values
