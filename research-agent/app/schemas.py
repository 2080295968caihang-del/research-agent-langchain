"""请求和响应的数据结构。

第 2 天开始在这里增加研究问题和报告的数据模型。
"""
import re

from pydantic import BaseModel,Field,field_validator,model_validator

CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")

def collect_citation_indexes(texts:list[str]) ->list[int]:
    found: set[int] = set()
    for text in texts:
        for raw in CITATION_PATTERN.findall(text or ""):
            found.add(int(raw))
    return sorted(found)    


class ResearchQuestion(BaseModel):
    question:str = Field(description="用户希望研究的问题")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls,value:str)->str: #检查问题是否为空   ->str表示返回值为字符串
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("问题不能为空")
        return cleaned     #判断空问题并且返回去掉空格的非空问题

class SourceRef(BaseModel):
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
    #ResearchReport 是后端的数据模型，用来规定报告数据的结构，
    #并由 Pydantic 检查模型返回的内容是否符合要求。
    title:str = Field(min_length=1,description="报告标题")
    summary:str = Field(min_length=1,description="报告摘要")
    key_findings:list[str] = Field(
        min_length=1,
        description="报告中的关键发现，至少包含一项",
    )
    conclusion:str = Field(min_length=1,description="报告结论")
    limitations:list[str] = Field(
        default_factory=list,
        description="研究局限性，可以为空",
    )

    sources:list[SourceRef] = Field(
        default_factory=list,
        description="正文实际引用到的来源，编号与 [n] 对应；由程序回填",
    )


    @field_validator("title","summary","conclusion")          #判断为空的文本段
    @classmethod
    def tex_fields_must_not_be_blank(cls,value:str)->str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("报告必填文本不能为空")
        return cleaned


    @field_validator("key_findings","limitations")
    @classmethod
    def list_items_must_be_not_blank(cls,values:list[str])->list[str]:
            #清理列表项目，并拒绝空的发现或局限性描述。
        cleaned_value = [item.strip() for item in values]
        if any (not item for item in cleaned_value):
            raise ValueError("报告列表项目不能为空")
        return cleaned_value

    @model_validator(mode="after")
    def citation_must_match_sources(self) ->"ResearchReport":
        texts = [self.summary,self.conclusion, *self.key_findings, *self.limitations]
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
    
