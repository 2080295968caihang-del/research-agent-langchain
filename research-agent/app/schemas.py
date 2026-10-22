"""请求和响应的数据结构。

第 2 天开始在这里增加研究问题和报告的数据模型。
"""
from pydantic import BaseModel,Field,field_validator

class ResearchQuestion(BaseModel):
    question:str = Field(description="用户希望研究的问题")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls,value:str)->str: #检查问题是否为空   ->str表示返回值为字符串
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("问题不能为空")
        return cleaned     #判断空问题并且返回去掉空格的非空问题

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
