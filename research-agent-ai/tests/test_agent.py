"""第 1 天模型调用 + 第 4 天 Tool Calling 的离线测试。

════════════════════════════════════════════════════════════════════
这份测试在测什么
════════════════════════════════════════════════════════════════════
第 1 天（2 条，原样保留）：
    · 空问题要在发请求之前就被拒绝
    · 没有 API Key 要明确报错，而不是发出不完整请求

第 4 天（11 条）：
    正常路径 3 条
        · 模型判断不用工具，直接回答
        · 模型调一次工具 → 结果回给模型 → 模型作答
        · 一轮里同时调两个工具
    失败路径 6 条（这才是「Agent 不会失控」的证据）
        · 搜索抛异常 → 不崩，错误变成文本交给模型
        · 模型幻觉出不存在的工具名
        · 模型传空参数 → 在调用搜索前就拒绝
        · 工具预算用尽 → 停手，强制收尾
        · 步数用尽 → 抛 AgentLoopError
        · 模型既不给文字也不要工具 → 抛 AgentLoopError
    工具元信息 1 条
        · @tool 的 docstring 不能是空话

════════════════════════════════════════════════════════════════════
为什么一条测试都不联网
════════════════════════════════════════════════════════════════════
两个替换点：
    ① 模型 —— 传 FakeToolModel，它按剧本返回 AIMessage，不发请求
    ② 搜索 —— 用 monkeypatch 把 agent 模块里的 search 换成假函数
              （web_search 内部调的是模块级名字 search，换掉它就够了）

这样跑 13 条测试只要 2 秒左右，而且不消耗一分钱额度。
第 12 天做评估集时才需要真实调用。
"""

# ====================================================================
# [复制粘贴] 块 1：导入
# --------------------------------------------------------------------
# 逻辑链：
#   pytest                    → 用 pytest.raises 断言「这里应该抛异常」
#   monkeypatch（fixture 参数）→ 临时替换模块里的函数，测试结束自动还原
#   AIMessage / ToolMessage    → 造「模型回复」和「工具结果」两种消息
#   agent / run_agent / ...    → 被测对象
#   SearchError                → 模拟搜索失败时用
# ====================================================================

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app import agent
from app.agent import AgentLoopError, ask_model, create_model, run_agent, web_search
from app.tools import SearchError


# ====================================================================
# [半手敲] 块 2：FakeToolModel —— 不联网的假模型
# --------------------------------------------------------------------
# 逻辑链：
#   run_agent 里只有两个地方碰模型：
#       tool_model.invoke(messages)   ← 正常循环
#       base_model.invoke(messages)   ← 预算用尽后强制收尾
#   所以只要造一个「有 invoke 和 bind_tools 的同款对象」，就能完全替代真模型。
#
# 三个必须理解的设计：
#   ① script：一个列表，每次 invoke 弹出一条，模拟「模型这次说什么」
#   ② bind_tools 返回**副本**（真实的 LangChain 就是这样）：
#        副本 bound=True，原对象 bound=False。
#        于是「收尾调用」走的是未绑定分支，永远给文本 → 测试能分清两条路径。
#   ③ script 和 calls 在副本间**共享**：
#        否则副本 pop 自己的列表，原对象看不到进度，剧本对不上。
#
# 为什么是半手敲：结构可以参照，但上面 ③ 这种共享细节必须自己想清楚，
# 否则测试会「看起来通过但测的是错的东西」。
# ====================================================================


class FakeToolModel:
    """不访问网络的假模型，按剧本返回 AIMessage。"""

    def __init__(self, script: list[AIMessage], final_answer: str = "收尾回答") -> None:
        self.script = list(script)
        self.final_answer = final_answer
        self.bound = False
        self.bound_tools: list[Any] = []
        # calls 记录每次 invoke 收到的完整消息列表，用于断言「模型到底看到了什么」
        self.calls: list[list[Any]] = []

    def bind_tools(self, tools: list[Any]) -> "FakeToolModel":
        """返回一个绑定了工具的副本，原对象保持不变。"""

        clone = FakeToolModel(self.script, self.final_answer)
        clone.script = self.script   # 共享剧本：pop 会互相影响，这是故意的
        clone.bound = True
        clone.bound_tools = list(tools)
        clone.calls = self.calls     # 共享调用记录
        return clone

    def invoke(self, messages: list[Any]) -> AIMessage:
        """返回剧本里的下一条消息。"""

        self.calls.append(list(messages))

        if not self.bound:
            # 未绑定工具 = 看不见工具 = 只能给文本。
            # 这正是「预算用尽后强制收尾」能收敛的原理。
            return AIMessage(content=self.final_answer)

        if not self.script:
            # 剧本弹完了还来调，返回固定句，防止测试莫名死循环
            return AIMessage(content="脚本已空")

        return self.script.pop(0)


# ====================================================================
# [复制粘贴] 块 3：两个造数据的小工具
# --------------------------------------------------------------------
# 逻辑链：
#   tool_call()          造一个 tool_call 字典（形状必须和真实模型一致，
#                        否则测试通过、线上崩）
#   last_tool_messages() 从「模型最后一次收到的消息列表」里挑出 ToolMessage，
#                        用来断言「我们到底把什么结果喂回给了模型」
# 为什么可以复制：纯数据构造，没有判断逻辑。
# ====================================================================


def tool_call(name: str = "web_search", query: str = "测试", call_id: str = "call_1") -> dict:
    """造一个 tool_call 字典，形状和真实模型返回的一致。"""

    return {
        "name": name,
        "args": {"query": query},
        "id": call_id,
        "type": "tool_call",
    }


def last_tool_messages(model: FakeToolModel) -> list[ToolMessage]:
    """取最后一次 invoke 时，消息历史里的全部 ToolMessage。"""

    return [m for m in model.calls[-1] if isinstance(m, ToolMessage)]


# ====================================================================
# [半手敲] 块 4：假 search 工厂 + 示例结果
# --------------------------------------------------------------------
# 逻辑链：
#   web_search 内部调用 search(query, top_k=5)
#        ↓
#   用 monkeypatch 把 agent.search 换成 _fake_search
#        ↓
#   _fake_search 记录调用参数，并按需返回结果或抛异常
#        ↓
#   断言 fake_search.calls，就能证明「工具到底执行了没有、传了什么」
#
# 为什么是工厂函数（返回函数）而不是直接写一个假函数：
#   不同测试需要「返回结果」或「抛异常」两种行为，工厂能带参数定制。
# ====================================================================


def fake_search_factory(
    results: list[dict[str, str]] | None = None,
    error: Exception | None = None,
) -> Any:
    """造一个假的 search 函数，可返回固定结果或抛指定异常。"""

    calls: list[dict[str, Any]] = []

    def _fake_search(query: str, top_k: int = 5) -> list[dict[str, str]]:
        calls.append({"query": query, "top_k": top_k})
        if error is not None:
            raise error
        return results if results is not None else []

    _fake_search.calls = calls  # type: ignore[attr-defined]
    return _fake_search


SAMPLE_RESULTS = [
    {
        "title": "LangChain 官方文档",
        "url": "https://example.com/langchain",
        "snippet": "LangChain 是一个构建 LLM 应用的框架。",
    }
]


# ====================================================================
# [复制粘贴] 块 5：第 1 天的两条测试（原样保留）
# --------------------------------------------------------------------
# 逻辑链：只验证「输入不合法时在发请求之前就拦下」，与第 4 天无关。
# ====================================================================


def test_ask_model_rejects_empty_question() -> None:
    """空问题应在发起网络请求前被拒绝。"""

    with pytest.raises(ValueError, match="问题不能为空"):
        ask_model("   ")


def test_create_model_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有 Key 时应给出明确提示，而不是发出不完整请求。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        create_model()


# ====================================================================
# [手敲] 块 6：正常路径 3 条
# --------------------------------------------------------------------
# 逻辑链（第 2 条是全场最重要的一条）：
#   剧本第 1 条 = 模型说「我要搜」
#        ↓
#   run_agent 执行 web_search → search() → 结果包装成 ToolMessage
#        ↓
#   剧本第 2 条 = 模型拿到结果后作答
#
# 断言要覆盖四个层面（少一个都测不完整）：
#   ① 工具真的执行了，参数是模型给的那个 query
#   ② 循环步数与工具次数对得上
#   ③ 模型第二次看到的消息里确实有 ToolMessage，且内容是格式化的结果
#   ④ tool_call_id 对得上（对不上真实服务端会 400）
#
# 为什么手敲：断言什么，就是你对「正确行为」的定义。抄来的是别人的定义。
# ====================================================================


def test_agent_answers_without_using_tools() -> None:
    """模型判断不需要搜索时，一次调用就结束，不该白白调工具。"""

    model = FakeToolModel([AIMessage(content="LangChain 是一个应用框架。")])

    run = run_agent("LangChain 是什么？", model=model)

    assert run.answer == "LangChain 是一个应用框架。"
    assert run.steps == 1
    assert run.tool_call_count == 0
    assert run.tool_outputs == []
    assert run.stopped_by_budget is False


def test_agent_calls_tool_then_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """最重要的一条：模型要工具 → 我们执行 → 结果回给模型 → 模型作答。"""

    fake_search = fake_search_factory(results=SAMPLE_RESULTS)
    monkeypatch.setattr(agent, "search", fake_search)

    model = FakeToolModel(
        [
            AIMessage(content="", tool_calls=[tool_call(query="LangChain 是什么")]),
            AIMessage(content="根据资料，LangChain 是一个应用框架。"),
        ]
    )

    run = run_agent("LangChain 是什么？", model=model)

    # ① 工具真的被调用了，用的是模型给的 query
    assert len(fake_search.calls) == 1
    assert fake_search.calls[0]["query"] == "LangChain 是什么"

    # ② 两步：一步要工具，一步给答案
    assert run.steps == 2
    assert run.tool_call_count == 1
    assert run.answer == "根据资料，LangChain 是一个应用框架。"

    # ③ 第二次 invoke 时，消息里必须有 ToolMessage，且内容是格式化后的结果
    outputs = last_tool_messages(model)
    assert len(outputs) == 1
    assert "<search_results>" in outputs[0].content
    assert "https://example.com/langchain" in outputs[0].content

    # ④ id 对得上（对不上，真实 OpenAI 服务端会拒绝这次请求）
    assert outputs[0].tool_call_id == "call_1"

    # ⑤ tool_outputs 留档，第 6 天要从这里提取来源
    assert run.tool_outputs == [outputs[0].content]


def test_agent_executes_multiple_tool_calls_in_one_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型一轮里同时要搜两个词，两个都要执行，并各自回一条 ToolMessage。"""

    fake_search = fake_search_factory(results=SAMPLE_RESULTS)
    monkeypatch.setattr(agent, "search", fake_search)

    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    tool_call(query="查询一", call_id="call_a"),
                    tool_call(query="查询二", call_id="call_b"),
                ],
            ),
            AIMessage(content="合并两次搜索结果后的回答。"),
        ]
    )

    run = run_agent("对比两个概念", model=model)

    assert run.tool_call_count == 2
    assert [c["query"] for c in fake_search.calls] == ["查询一", "查询二"]

    outputs = last_tool_messages(model)
    assert len(outputs) == 2
    # 两个 id 都在，说明没有把结果串号
    assert {m.tool_call_id for m in outputs} == {"call_a", "call_b"}


# ====================================================================
# [手敲] 块 7：失败路径 6 条
# --------------------------------------------------------------------
# 逻辑链：正常路径证明「能跑」，失败路径才证明「不会失控」。
# 这 6 条分别对应 Agent 的 6 种异常情形，缺一条就是一个线上事故入口：
#   搜索挂了     → 必须降级成文本，不能抛
#   工具名幻觉   → 必须提示可用工具，不能让程序崩
#   参数为空     → 必须在调用前拦下，不能白花一次额度
#   预算用尽     → 必须停手 + 强制收尾，不能空转
#   步数用尽     → 必须报错，不能静默返回半成品
#   模型空回复   → 必须报错，不能返回空串让上层以为成功
#
# 为什么手敲：这是你给自己写的安全网。面试问「你怎么保证 Agent 不会死循环」，
# 答案就是这 6 条测试的断言。
# ====================================================================


def test_agent_survives_search_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """搜索抛 SearchError 时，Agent 不能崩；错误要以文本形式交给模型。"""

    fake_search = fake_search_factory(error=SearchError("Tavily 返回 HTTP 401"))
    monkeypatch.setattr(agent, "search", fake_search)

    model = FakeToolModel(
        [
            AIMessage(content="", tool_calls=[tool_call()]),
            AIMessage(content="没有找到相关资料，下面是基于常识的回答。"),
        ]
    )

    run = run_agent("一个搜不到的问题", model=model)

    assert run.answer.startswith("没有找到相关资料")

    # 错误信息进了 ToolMessage → 说明失败被「降级成文本」而不是抛出去
    outputs = last_tool_messages(model)
    assert "搜索失败" in outputs[0].content
    assert "401" in outputs[0].content


def test_agent_reports_unknown_tool_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型幻觉出不存在的工具名时，回一句「没有这个工具」，并列出可用的。"""

    monkeypatch.setattr(agent, "search", fake_search_factory(results=SAMPLE_RESULTS))

    model = FakeToolModel(
        [
            AIMessage(content="", tool_calls=[tool_call(name="google_search")]),
            AIMessage(content="换个方式回答。"),
        ]
    )

    run = run_agent("测试未知工具", model=model)

    outputs = last_tool_messages(model)
    assert "不存在名为 google_search 的工具" in outputs[0].content
    assert "web_search" in outputs[0].content   # 提示里带了可用工具列表
    assert run.answer == "换个方式回答。"


def test_agent_reports_invalid_tool_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型传空 query 时，在调用搜索之前就拒绝，不浪费一次 API 额度。"""

    fake_search = fake_search_factory(results=SAMPLE_RESULTS)
    monkeypatch.setattr(agent, "search", fake_search)

    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "web_search", "args": {}, "id": "call_x", "type": "tool_call"}
                ],
            ),
            AIMessage(content="参数不对，我直接回答。"),
        ]
    )

    run_agent("测试空参数", model=model)

    outputs = last_tool_messages(model)
    assert "query 参数必须是非空字符串" in outputs[0].content

    # 关键断言：搜索一次都没被真正调用
    assert fake_search.calls == []


def test_agent_stops_at_tool_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """工具次数用尽后，不再执行工具，改用未绑定工具的模型强制收尾。"""

    fake_search = fake_search_factory(results=SAMPLE_RESULTS)
    monkeypatch.setattr(agent, "search", fake_search)

    model = FakeToolModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    tool_call(query="第一个", call_id="call_1"),
                    tool_call(query="第二个", call_id="call_2"),
                ],
            ),
        ],
        final_answer="基于已有资料的回答。",
    )

    # 预算只给 1 次，但模型一次要调 2 个
    run = run_agent("测试预算", model=model, max_tool_calls=1)

    assert run.tool_call_count == 1
    assert run.stopped_by_budget is True
    assert run.answer == "基于已有资料的回答。"

    # 只执行了第一个查询，第二个被预算拦下
    assert [c["query"] for c in fake_search.calls] == ["第一个"]

    # 被拦下的那个也必须有 ToolMessage，内容是「预算已用尽」。
    # 缺这条，真实服务端会因「tool_call 没有对应结果」拒绝请求。
    outputs = last_tool_messages(model)
    assert any("预算已用尽" in m.content for m in outputs)


def test_agent_raises_when_steps_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型一直要工具、永不收尾时，必须抛 AgentLoopError，不能静默返回。"""

    monkeypatch.setattr(agent, "search", fake_search_factory(results=SAMPLE_RESULTS))

    model = FakeToolModel(
        [
            AIMessage(content="", tool_calls=[tool_call(call_id="c1")]),
            AIMessage(content="", tool_calls=[tool_call(call_id="c2")]),
            AIMessage(content="", tool_calls=[tool_call(call_id="c3")]),
        ]
    )

    with pytest.raises(AgentLoopError, match="最大步数"):
        run_agent("测试步数上限", model=model, max_steps=2, max_tool_calls=10)


def test_agent_rejects_empty_question() -> None:
    """空问题在创建模型之前就被拒绝。"""

    with pytest.raises(ValueError, match="不能为空"):
        run_agent("   ")


def test_agent_raises_when_model_returns_nothing() -> None:
    """模型既不给文字也不要工具，属于异常行为，应明确报错。"""

    model = FakeToolModel([AIMessage(content="")])

    with pytest.raises(AgentLoopError, match="没有给出文字回答"):
        run_agent("测试空回复", model=model)


# ====================================================================
# [复制粘贴] 块 8：工具元信息
# --------------------------------------------------------------------
# 逻辑链：@tool 的 docstring 会变成 web_search.description，
#        模型靠它判断「什么时候用这个工具」，所以不能是空话。
# ====================================================================


def test_web_search_description_is_meaningful() -> None:
    """@tool 的 docstring 就是给模型看的 prompt，不能是空话。"""

    assert web_search.name == "web_search"
    assert web_search.description
    assert "搜索" in web_search.description
