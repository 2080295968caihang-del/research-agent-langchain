"""第 2 天的数据结构，第 6 天增加来源引用。

把输入和输出定义成明确的 Pydantic 模型，可以让程序在模型返回内容后
立刻发现字段缺失、类型错误和空文本，而不是把不可靠的字典继续传下去。

第 6 天的关键约定：
    来源（sources）只能由程序从真实搜索结果回填，
    模型只负责在正文里写 [n]。这样「模型编造 URL」这条风险从结构上就被堵死了。
"""

import re

from pydantic import BaseModel, Field, field_validator, model_validator


# 正文引用编号的正则：只认方括号里的 1-3 位数字。
# 用 1-3 位而不是 \d+，是为了让「[12345]」这种明显不是编号的内容不被当成引用。
# [手敲] 第 6 天块 2：这条正则是「引用可校验」的全部基础，自己写一遍。
CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")


# [手敲] 第 6 天块 3：collect_citation_indexes —— 抽号、去重、升序
# --------------------------------------------------------------------
# 逻辑链：
#   正文若干段文本 → 正则找出所有 [n] → 字符串转 int → 塞进 set 去重
#        → sorted() 变成升序列表
#   为什么用 set 而不是 list：同一来源被引用 5 次只应算 1 条，
#   用 list 还要写 `if x not in result`，set 天然去重。
#   为什么是纯函数：不碰模型、不碰网络，所以能单独写测试钉住行为。
def collect_citation_indexes(texts: list[str]) -> list[int]:
    """从若干段文本里抽出 [n] 引用编号，去重后升序返回。

    放在 schemas 而不是 report 里，是因为下面的模型校验器要用到它，
    而 report 又反过来 import schemas —— 放 report 会形成循环导入。
    """

    found: set[int] = set()
    for text in texts:
        for raw in CITATION_PATTERN.findall(text or ""):
            found.add(int(raw))
    return sorted(found)


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


# [手敲] 第 6 天块 1：SourceRef —— 一条来源长什么样
# --------------------------------------------------------------------
# 逻辑链：
#   真实搜索结果 dict  → SourceRef(index, title, url)  → 进 ResearchReport.sources
#   为什么要单独建模型，不直接用 dict：
#     dict 里的 url 没有任何约束，搜索服务商返回一个 javascript: 链接也能混进来。
#   为什么 index 只校验 ge=1、不校验连续性：
#     连续性由 report 层重排时保证；schema 只负责「别是 0 或负数」。
class SourceRef(BaseModel):
    """报告里引用的一条外部来源。

    为什么不给 url 设默认值、也不允许模型提供：见模块开头的约定。
    """

    index: int = Field(ge=1, description="正文 [n] 里的 n，从 1 开始")
    title: str = Field(min_length=1, description="来源标题")
    url: str = Field(min_length=1, description="来源网址")

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        """标题必须真的有字，空白标题在报告里等于没有出处。"""

        cleaned = value.strip()
        if not cleaned:
            raise ValueError("来源标题不能为空")
        return cleaned

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        """只接受 http/https。

        javascript:、data:、file: 这类协议一律拒绝——它们可能来自被污染的结果，
        放进报告后如果被渲染成链接会变成可执行内容。
        """

        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("来源网址必须以 http:// 或 https:// 开头")
        return cleaned


class ResearchReport(BaseModel):
    """结构化研究报告。第 6 天起带 sources。"""

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
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="正文实际引用到的来源，编号与 [n] 对应；由程序回填",
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

    # [手敲] 第 6 天块 4：引用一致性校验
    # ------------------------------------------------------------------
    # 逻辑链（三条规则各自防一种错）：
    #   正文有 [n] 但来源为空   → 模型编了出处          → 拒绝
    #   来源有一堆但正文没 [n]  → 模型根本没用这些资料  → 拒绝
    #   正文的 [n] 超出来源数量 → 越界引用              → 拒绝
    #   mode="after" 表示「其余字段都校验完了再跑」，这时能拿到完整的 self。
    #   这一步只拒绝不修复：修复需要原始搜索结果，那是 report 层的事。
    @model_validator(mode="after")
    def citations_must_match_sources(self) -> "ResearchReport":
        """正文引用和来源清单必须一一对得上。"""

        texts = [self.summary, self.conclusion, *self.key_findings]
        cited = set(collect_citation_indexes(texts))
        allowed = {source.index for source in self.sources}

        if not self.sources:
            if cited:
                raise ValueError(f"正文引用了 {sorted(cited)}，但来源清单是空的")
            return self

        if not cited:
            raise ValueError("报告列了来源，但正文没有任何 [n] 引用")

        unknown = sorted(cited - allowed)
        if unknown:
            raise ValueError(f"正文引用了不存在的来源编号：{unknown}")

        return self
