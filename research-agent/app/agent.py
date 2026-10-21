"""模型和 Agent 逻辑。

第 1 天请在这里照着参考目录的 app/agent.py 手动输入模型调用代码。
"""
import os

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()
def create_model() ->ChatOpenAI:
    api_key = os.getenv("OPENAI_API_KEY","").strip()  #gitenv识别系统环境变量    strip去掉末尾空格
    if not api_key:
        raise RuntimeError(
            "没有找到 OPENAI_API_KEY。请复制 .env.example 为 .env，并且填写真实 API Key。"
        )

    model_name = os.getenv("OPENAI_MODEL","gpt-6-luna").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()

    model_options = {
        "model":model_name,
        "temperature": 0,             #temperature:0->回答尽量稳定；0.7->自然，有变化；1->随机性更高
        "timeout": 60,                #timeout单词请求最多等待秒数
        "max_retries": 2,             #max_retries失败后的重试次数
    }
    if base_url:
        model_options["base_url"] = base_url    #base_url不为空则传入字典，会自动创建键

    return ChatOpenAI(**model_options)

def ask_model(question: str) ->str:
    question = question.strip()
    if not question:
        raise ValueError("问题为空")

    model = create_model()
    response = model.invoke([HumanMessage(content=question)])
    return str(response.content)
