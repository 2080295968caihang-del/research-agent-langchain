"""第 2 天：结构化研究报告测试。"""

from types import SimpleNamespace

import pytest
import requests
from pydantic import ValidationError

from app.report import (
    _probe_url,
    apply_citations,
    build_report_prompt,
    format_sources_block,
    generate_report,
    parse_report,
    report_to_markdown,
)
from app.schemas import (
    ResearchQuestion,
    ResearchReport,
    SourceRef,
    collect_citation_indexes,
)


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


# ====================================================================
# 第 6 天：引用与来源回填
# ====================================================================
# 这一组测试全部离线：不联网、不调用真实模型。
# 覆盖四类风险：
#   ① 来源字段被模型污染（URL 编造）
#   ② 正文引用越界（[9] 但只有 3 条资料）
#   ③ 编号断号（只引用 1 和 3，报告里出现空洞）
#   ④ 证据不足时不肯说「资料不足」
# ====================================================================

SOURCES = [
    {"title": "来源甲", "url": "https://a.example/1", "snippet": "甲的摘要"},
    {"title": "来源乙", "url": "https://b.example/2", "snippet": "乙的摘要"},
    {"title": "来源丙", "url": "https://c.example/3", "snippet": "丙的摘要"},
]


def make_payload(**overrides: object) -> dict:
    """造一份模型输出的原始字典（还没校验、没回填）。"""

    payload: dict = {
        "title": "测试标题",
        "summary": "摘要 [1]",
        "key_findings": ["发现一 [1]", "发现二 [2]"],
        "conclusion": "结论 [2]",
        "limitations": [],
    }
    payload.update(overrides)
    return payload


def test_source_ref_rejects_non_http_url() -> None:
    """javascript: / data: 这类协议必须拒绝，否则报告里的链接可能被当作脚本执行。"""

    with pytest.raises(ValidationError):
        SourceRef(index=1, title="坏来源", url="javascript:alert(1)")


def test_source_ref_requires_real_title() -> None:
    """空白标题在报告里等于没有出处。"""

    with pytest.raises(ValidationError):
        SourceRef(index=1, title="   ", url="https://a.example/1")


def test_report_sources_default_to_empty_list() -> None:
    """第 2 天的调用方式必须继续可用，不能因为新增字段就报错。"""

    report = ResearchReport(
        title="标题",
        summary="摘要",
        key_findings=["发现"],
        conclusion="结论",
    )

    assert report.sources == []


def test_report_rejects_sources_without_any_citation() -> None:
    """列了来源却一处都没引用 = 说不出资料用在哪，必须拒绝。"""

    with pytest.raises(ValidationError, match="引用"):
        ResearchReport(
            title="标题",
            summary="整段没有任何编号",
            key_findings=["也没有编号"],
            conclusion="还是没有",
            sources=[SourceRef(index=1, title="来源甲", url="https://a.example/1")],
        )


def test_format_sources_block_numbers_from_one() -> None:
    """模型看到的编号必须是 1 起步的连续整数。"""

    block = format_sources_block(SOURCES)
    lines = block.splitlines()

    assert lines[0].startswith("[1]")
    assert "[2]" in block
    assert "[3]" in block
    assert SOURCES[0]["url"] in block
    assert "甲的摘要" in block


def test_collect_citation_indexes_dedupes_and_sorts() -> None:
    """同一来源被引用多次只算一条，且结果升序。"""

    assert collect_citation_indexes(["甲 [2]", "乙 [1]", "丙 [2] [1]"]) == [1, 2]


def test_collect_citation_indexes_ignores_non_numeric_markup() -> None:
    """只有方括号里的 1-3 位数字才算引用编号。"""

    assert collect_citation_indexes(["[abc]", "[待补充]", "[[1]]", "[12345]"]) == [1]


def test_apply_citations_drops_unknown_index() -> None:
    """越界编号必须删掉，并写进局限性，不能让读者以为漏了来源。"""

    payload = make_payload(
        summary="摘要 [9]",
        key_findings=["发现 [9]"],
        conclusion="结论",
    )

    data, notes = apply_citations(payload, SOURCES)
    report = ResearchReport.model_validate(data)

    assert "[9]" not in report.summary
    assert report.sources == []
    assert any("编号" in note for note in notes)
    assert any("编号" in item for item in report.limitations)


def test_apply_citations_removes_model_written_urls() -> None:
    """模型自己贴的网址一律剥掉——这是「不把虚构 URL 当引用」的落点。"""

    payload = make_payload(summary="摘要 [1]，详见 https://fake.example/x")

    data, notes = apply_citations(payload, SOURCES)
    report = ResearchReport.model_validate(data)

    assert "fake.example" not in report.summary
    assert any("网址" in note for note in notes)


def test_apply_citations_ignores_model_supplied_sources() -> None:
    """模型输出里自带的 sources 字段必须被丢弃，来源只认程序回填的那份。"""

    payload = make_payload(
        sources=[{"index": 1, "title": "编造的来源", "url": "https://fake.example/1"}]
    )

    data, _ = apply_citations(payload, SOURCES)
    report = ResearchReport.model_validate(data)

    assert report.sources[0].url == SOURCES[0]["url"]
    assert report.sources[0].title == SOURCES[0]["title"]


def test_apply_citations_renumbers_to_contiguous() -> None:
    """只引用第 3 条时，报告里应该显示 [1]，而不是留一个 [3] 的断号。"""

    payload = make_payload(
        summary="只引用 [3]",
        key_findings=["发现 [3]"],
        conclusion="结论 [3]",
    )

    data, _ = apply_citations(payload, SOURCES)
    report = ResearchReport.model_validate(data)

    assert report.summary == "只引用 [1]"
    assert [source.index for source in report.sources] == [1]
    assert report.sources[0].url == SOURCES[2]["url"]


def test_apply_citations_keeps_only_cited_sources() -> None:
    """引用 1 和 3 时，来源清单应是这两条，并重排成 1、2。"""

    payload = make_payload(
        summary="摘要 [1]",
        key_findings=["甲 [1]", "丙 [3]"],
        conclusion="结论 [3]",
    )

    data, _ = apply_citations(payload, SOURCES)
    report = ResearchReport.model_validate(data)

    assert [source.index for source in report.sources] == [1, 2]
    assert report.sources[0].url == SOURCES[0]["url"]
    assert report.sources[1].url == SOURCES[2]["url"]
    assert "[2]" in report.key_findings[1]


def test_generate_report_without_sources_states_limitation() -> None:
    """零资料时必须明确写出「未检索到可靠来源」，不能装作查过了。"""

    model = FakeModel(
        '{"title":"尚未确认","summary":"没有资料",'
        '"key_findings":["暂无"],"conclusion":"等待确认","limitations":[]}'
    )

    report = generate_report("一个查不到的问题？", sources=None, model=model)

    assert any("未检索到可靠来源" in item for item in report.limitations)
    assert report.sources == []


def test_generate_report_flags_insufficient_sources() -> None:
    """资料少于 MIN_USEFUL_RESULTS（2 条）时，局限性必须提示结论不完整。"""

    model = FakeModel(
        '{"title":"标题","summary":"摘要 [1]",'
        '"key_findings":["发现 [1]"],"conclusion":"结论 [1]","limitations":[]}'
    )

    report = generate_report("问题？", sources=SOURCES[:1], model=model)

    assert len(report.sources) == 1
    assert any("不足以支撑" in item for item in report.limitations)


def test_generate_report_fills_real_sources_from_materials() -> None:
    """回填的 URL 必须来自传入的资料，调 after 之后索引连续。"""

    model = FakeModel(
        '{"title":"标题","summary":"摘要 [1]",'
        '"key_findings":["甲 [1]","丙 [3]"],"conclusion":"结论 [3]","limitations":[]}'
    )

    report = generate_report("问题？", sources=SOURCES, model=model)

    assert [source.url for source in report.sources] == [
        SOURCES[0]["url"],
        SOURCES[2]["url"],
    ]
    assert all(source.url.startswith("https://") for source in report.sources)


def test_report_to_markdown_links_citations() -> None:
    """渲染时 [1] 应变成指向原文的 Markdown 链接，文末还要有来源清单。"""

    data, _ = apply_citations(make_payload(), SOURCES)
    report = ResearchReport.model_validate(data)

    markdown = report_to_markdown(report)

    assert "[[1]](https://a.example/1)" in markdown
    assert "## 来源" in markdown
    assert "1. [来源甲](https://a.example/1)" in markdown


# ====================================================================
# 链接探测：_probe_url
# --------------------------------------------------------------------
# 这个函数此前零覆盖（96 条测试全绿也抓不到它写错），
# 而它决定「报告里的来源算不算可用」，7b 顺手补两条。
#
# 两条要一起看才有意义：
#   超时       → None（没验过，不算失效）
#   403 / 401  → 原样返回（网站拒绝爬虫，但链接本身是好的）
# 把 403 也当成 None，报告会把真实来源标成坏链接 —— 这是最典型的错法。
# ====================================================================
class FakeResponse:
    """假的 requests 响应：既能 `with` 用，也能单独 close()。

    为什么要有 __enter__/__exit__：_probe_url 7b 之后改成了 with 写法，
    换成一个普通对象会 AttributeError，测试就测不到逻辑本身。
    """

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.closed = False

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.closed = True
        return False

    def close(self) -> None:
        self.closed = True


def test_probe_url_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """探测超时必须返回 None，不能让一份报告卡在某个慢网站上。"""

    def raise_timeout(*args: object, **kwargs: object) -> FakeResponse:
        raise requests.exceptions.Timeout("连接超时")

    monkeypatch.setattr("app.report.requests.head", raise_timeout)

    assert _probe_url("https://a.example/1") is None


def test_probe_url_403_is_reported_as_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 是「网站拒绝程序访问」，不等于链接失效，要原样返回状态码。

    改成 `if status >= 400: return None` 的话，所有拒绝爬虫的
    正经网站（知乎、微信公众号等）都会被标成坏来源，这条会红。
    """

    monkeypatch.setattr(
        "app.report.requests.head", lambda *a, **k: FakeResponse(403)
    )

    assert _probe_url("https://a.example/1") == 403
