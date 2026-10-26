"""研究报告生成与来源回填。

第 2 天：验证问题 -> 构造 Prompt -> 调用模型 -> 解析 JSON -> Pydantic 校验。
第 6 天：把搜索到的资料喂进 Prompt，模型只写 [n]，来源由程序回填并校验。

第 6 天最重要的两条不变式：
    1. 报告里的 url 只能来自真实搜索结果，模型写的 url 一律剥掉；
    2. 正文的 [n] 必须能在来源清单里找到，找不到就删掉并记进 limitations。
"""

import json
import re
from typing import Any, Callable

import requests
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.agent import create_model
from app.schemas import (
    CITATION_PATTERN,
    ResearchQuestion,
    ResearchReport,
    collect_citation_indexes,
)
from app.tools import MIN_USEFUL_RESULTS


# 正文里的裸网址。结尾排除中英文标点和右括号，避免把「见 https://a.com。」整段吞掉。
URL_PATTERN = re.compile(r"https?://[^\s，。；、）)\]】\"']+")

# 判定「链接已失效」的状态码。只有这两类才剔除。
LINK_DEAD_STATUS = {404, 410}

# 这些状态码说明站点存在、只是不配合自动探测，必须保留，否则会误杀。
LINK_SKIP_STATUS = {401, 403, 405, 429, 501}

LINK_TIMEOUT_SECONDS = 5.0

# 没有检索到任何资料时的固定文案。写成常量方便测试断言，也避免各处措辞不一致。
NO_SOURCE_NOTE = "本次未检索到可靠来源，报告内容仅基于模型已有知识，未经外部资料验证。"


# ====================================================================
# [手敲] 第 6 天块 5：报告系统提示词（改第 2 天那段）
# --------------------------------------------------------------------
# 逻辑链：
#   第 2 天：「不要虚构来源 URL；暂时不接入搜索和引用」
#        ↓ 现在有真资料了，禁止项要变成「不要写 URL，只写编号」
#   三条硬性规则各防一件事：
#     规则 1 → 引用越界（编 [7] 但只有 5 条）
#     规则 2 → 模型自己拼 URL（这是本条提示词存在的理由）
#     规则 3 → 资料不足时硬编结论
#   为什么要求「每项 key_findings 都标注 [n]」：
#     没有编号的发现等于没有出处，校验时会被判为不可追溯。
# ====================================================================

REPORT_SYSTEM_PROMPT = """你是一个严谨的研究报告助手。

请根据「研究问题」和「参考资料」生成一份结构化报告。
参考资料每一条都带编号，例如 [1]、[2]。正文陈述某个事实时，在该句末尾标注来源编号。

只返回一个合法 JSON 对象，不要使用 Markdown 代码块，不要添加 JSON 以外的解释。
JSON 必须包含以下字段：
- title: 字符串，报告标题
- summary: 字符串，摘要
- key_findings: 字符串数组，至少一项关键发现
- conclusion: 字符串，结论
- limitations: 字符串数组，列出局限性，没有时返回空数组

硬性规则：
1. 只能使用参考资料里出现过的编号，不允许出现资料里没有的编号。
2. 绝对不要在正文里写网址（http:// 或 https://）。来源由程序根据 [n] 自动回填。
3. 资料不足以支撑结论时不要猜，请在 limitations 里明确写出「资料不足」，
   并在 title 和 conclusion 中说明这是一个尚未确认的问题。
"""


# ====================================================================
# [半手敲] 第 6 天块 6：format_sources_block —— 把资料编好号给模型看
# --------------------------------------------------------------------
# 逻辑链：
#   真实搜索结果 list[dict]  → enumerate(start=1) → "[1] 标题 / URL / 摘要"
#        ↓
#   拼进 HumanMessage → 模型看到的编号就是最终报告里的编号
#   为什么 start=1 而不是 0：报告里没人写 [0]，人会从 1 开始数。
#   为什么标题为空时退回 URL：宁可显示一个丑出处，也不要空标题。
# ====================================================================


def format_sources_block(sources: list[dict[str, str]]) -> str:
    """把搜索结果排成带编号的纯文本资料清单。"""

    lines: list[str] = []
    for index, item in enumerate(sources, start=1):
        title = (item.get("title") or "").strip() or (item.get("url") or "")
        lines.append(f"[{index}] {title}")
        lines.append(f"    URL: {item.get('url', '')}")
        snippet = (item.get("snippet") or "").strip()
        if snippet:
            # 摘要截到 200 字：5 条资料 × 200 字 ≈ 1000 字，不会把上下文撑爆。
            lines.append(f"    摘要: {snippet[:200]}")
    return "\n".join(lines)


# ====================================================================
# [半手敲] 第 6 天块 7：build_report_prompt —— 问题 + 资料
# --------------------------------------------------------------------
# 逻辑链：
#   第 2 天：只放问题
#        ↓
#   现在：问题 + format_sources_block(资料)
#        ↓
#   没资料时不能什么都不说 —— 模型会以为「用户忘了给」，于是凭记忆编。
#   所以零资料分支要显式告诉它：这次没有资料，正文不许写 [n]。
# ====================================================================


def build_report_prompt(
    question: str,
    sources: list[dict[str, str]] | None = None,
) -> str:
    """验证研究问题，并把资料一起拼成用户 Prompt。"""

    validated_question = ResearchQuestion(question=question)
    parts = [
        "请研究下面的问题，并严格按照系统消息规定的 JSON 字段返回报告。",
        "",
        f"研究问题：{validated_question.question}",
    ]

    if sources:
        parts += [
            "",
            "参考资料（正文引用时使用方括号里的编号）：",
            format_sources_block(sources),
        ]
    else:
        parts += [
            "",
            "本次没有检索到任何外部资料。正文不要写任何 [n] 引用，"
            "limitations 第一条必须说明「未检索到可靠来源」。",
        ]

    return "\n".join(parts)


def _remove_json_fence(raw_text: str) -> str:
    """去掉模型偶尔添加的 ```json 代码块标记。"""

    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        # 第一行是 ``` 或 ```json，最后一行是结束标记。
        return "\n".join(lines[1:-1]).strip()
    return text


def _load_payload(raw_text: str) -> dict[str, Any]:
    """把模型文本解析成字典，并确认顶层是对象。"""

    try:
        payload: Any = json.loads(_remove_json_fence(raw_text))
    except json.JSONDecodeError as error:
        raise ValueError("模型没有返回有效 JSON 报告") from error

    if not isinstance(payload, dict):
        # 模型偶尔返回数组。提前拦下，后面所有 .get() 才不会炸。
        raise ValueError("模型返回的报告不是 JSON 对象")

    return payload


def parse_report(raw_text: str) -> ResearchReport:
    """将模型文本解析为经过 Pydantic 校验的 ResearchReport。

    这是不带资料的低层解析：不抽引用、不回填来源，
    所以只适合「模型完全没引用」的场景或单元测试。
    走完整流程的是 generate_report。
    """

    payload = _load_payload(raw_text)

    try:
        return ResearchReport.model_validate(payload)
    except ValidationError as error:
        raise ValueError("模型返回的报告字段不符合要求") from error


# ====================================================================
# [手敲] 第 6 天块 8：apply_citations —— 今天真正的核心
# --------------------------------------------------------------------
# 逻辑链（五步，顺序不能换）：
#   ① 丢掉模型自带的 sources/urls 字段  ← 从源头断掉「编造 URL」
#   ② 剥掉正文里的裸网址                ← 模型有时不用 [n] 而是直接贴链接
#   ③ 越界编号（> 资料条数）删掉并记账
#   ④ 只保留被引用到的来源，重新从 1 连续编号，正文同步改号
#   ⑤ 按资料条数分档补 limitations（0 条 / 1-2 条 / 3 条以上）
#
# 为什么 ④ 要重排而不沿用原编号：
#   资料有 5 条、模型只用 1、3、5，不重排的话报告里会出现 [1][3][5] 三个断号，
#   读者会以为是漏了两条来源。
# 为什么用一次 re.sub 而不是循环 replace：
#   若映射是 1→2、2→1，逐条 replace 会把 [1] 先改成 [2] 再被下一条改回来，来回串号。
#   单次正则替换每个 [n] 只处理一次，不会级联。
# ====================================================================


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """删掉模型自己写的来源字段。来源只能由程序回填。"""

    cleaned = dict(payload)
    for key in ("sources", "source", "urls", "references", "citations"):
        cleaned.pop(key, None)
    return cleaned


def _report_texts(payload: dict[str, Any]) -> list[str]:
    """取出报告里可能带引用的所有正文段落。"""

    texts: list[str] = []
    for key in ("summary", "conclusion"):
        value = payload.get(key)
        if isinstance(value, str):
            texts.append(value)

    findings = payload.get("key_findings")
    if isinstance(findings, list):
        texts.extend(str(item) for item in findings)

    return texts


def _map_texts(payload: dict[str, Any], transform: Callable[[str], str]) -> dict[str, Any]:
    """对 summary / conclusion / key_findings 里的每段文本做同一个转换。"""

    updated = dict(payload)

    for key in ("summary", "conclusion"):
        if isinstance(updated.get(key), str):
            updated[key] = transform(updated[key])

    findings = updated.get("key_findings")
    if isinstance(findings, list):
        updated["key_findings"] = [transform(str(item)) for item in findings]

    return updated


def _scrub_urls(text: str) -> str:
    """把正文里模型自己写的网址删掉，并收拾留下的多余空格。"""

    without_urls = URL_PATTERN.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", without_urls).strip()


def _drop_marks(text: str, indexes: list[int]) -> str:
    """删掉指定的 [n] 标记。"""

    for index in indexes:
        text = text.replace(f"[{index}]", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _renumber_marks(text: str, mapping: dict[int, int]) -> str:
    """按映射一次性改写所有 [n]，不级联。"""

    def _replace(match: "re.Match[str]") -> str:
        old = int(match.group(1))
        return f"[{mapping[old]}]" if old in mapping else match.group(0)

    return CITATION_PATTERN.sub(_replace, text)


def _push_limits(payload: dict[str, Any], notes: list[str], at_front: bool = False) -> None:
    """把新的局限性并入 limitations，重复的不再加。

    去重规则：只要新旧两条互相包含，就算重复。
    为什么不用严格相等：模型常写「未检索到可靠来源」，而我们写的是
    「本次未检索到可靠来源，报告内容仅基于模型已有知识……」——
    严格比较会把同意思的两句话都留下，读者看到的就是重复。
    """

    existing = [str(item).strip() for item in (payload.get("limitations") or [])]
    existing = [item for item in existing if item]
    fresh: list[str] = []

    for raw in notes:
        note = (raw or "").strip()
        if not note:
            continue

        duplicated = any(note in item or item in note for item in existing)
        if duplicated:
            continue

        fresh.append(note)

    payload["limitations"] = (fresh + existing) if at_front else (existing + fresh)


def apply_citations(
    payload: dict[str, Any],
    sources: list[dict[str, str]],
    extra_notes: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把模型输出和真实来源对齐：剥网址、删越界编号、重排来源、补局限性。

    返回 (修好的 payload, 本次做过的修改说明)。
    修改说明会并入 limitations，让读者知道报告被动过哪些手脚。
    """

    notes: list[str] = list(extra_notes or [])
    available = len(sources)

    # ① 来源字段只能由程序写。
    data = _sanitize_payload(payload)

    # ② 正文里的裸网址一律删掉。
    before = "".join(_report_texts(data))
    data = _map_texts(data, _scrub_urls)
    if before != "".join(_report_texts(data)):
        notes.append("正文里模型自行写入的网址已被移除；来源只保留检索结果中的真实链接")

    # ③ 越界编号删掉。
    cited = collect_citation_indexes(_report_texts(data))
    unknown = [index for index in cited if index < 1 or index > available]
    if unknown:
        data = _map_texts(data, lambda text: _drop_marks(text, unknown))
        notes.append(
            f"已移除不存在的引用编号 {unknown}（本次只有 1-{available} 号来源可用）"
        )
        cited = [index for index in cited if index not in unknown]

    # ④ 只保留被引用到的来源，重新连续编号。
    if cited:
        order = sorted(set(cited))
        mapping = {old: new for new, old in enumerate(order, start=1)}
        data = _map_texts(data, lambda text: _renumber_marks(text, mapping))
        data["sources"] = [
            {
                "index": mapping[old],
                "title": (sources[old - 1].get("title") or "").strip()
                or sources[old - 1]["url"],
                "url": sources[old - 1]["url"],
            }
            for old in order
        ]
    else:
        data.pop("sources", None)
        if available:
            notes.append(
                "模型没有在正文标注任何 [n]，因此未附来源清单（无法对应的出处不能写进报告）"
            )

    # ⑤ 按资料条数分档。
    if available == 0:
        _push_limits(data, [NO_SOURCE_NOTE, *notes], at_front=True)
    elif available < MIN_USEFUL_RESULTS:
        _push_limits(
            data,
            [
                f"本次只检索到 {available} 条资料，不足以支撑确定结论，结论仅供参考",
                *notes,
            ],
            at_front=True,
        )
    else:
        _push_limits(data, notes)

    return data, notes


# ====================================================================
# [复制粘贴] 第 6 天块 9：来源链接可访问性检查
# --------------------------------------------------------------------
# 逻辑链：
#   每条来源 → HEAD 请求 → 看状态码
#        ↓
#     404 / 410        → 站点明确说「没有这个页面」→ 剔除
#     401/403/405/429  → 站点存在但拒绝探测      → 保留（判死会大量误杀）
#     网络异常          → 不知道                  → 保留
#   为什么用 HEAD 而不是 GET：只要状态码，不需要下载正文。
#   为什么 405 还要再试 GET：不少站点不实现 HEAD，直接返回 405，
#   只有少数站点连 GET 都拒。两次都拒才当作「不知道」。
# 为什么可以复制：这是纯 I/O 封装，没有业务判断。
# ====================================================================


def _probe_url(url: str, timeout: float = LINK_TIMEOUT_SECONDS) -> int | None:
    """探测一个网址的状态码；网络异常时返回 None。"""

    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        status = response.status_code
        response.close()
    except requests.RequestException:
        return None

    if status in (405, 501):
        try:
            response = requests.get(
                url, timeout=timeout, allow_redirects=True, stream=True
            )
            status = response.status_code
            response.close()
        except requests.RequestException:
            return None

    return status


def drop_unreachable_sources(
    sources: list[dict[str, str]],
    timeout: float = LINK_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, str]], list[str]]:
    """剔除明确失效的来源，返回 (保留的来源, 说明)。"""

    kept: list[dict[str, str]] = []
    notes: list[str] = []

    for item in sources:
        status = _probe_url(item.get("url", ""), timeout)

        if status is not None and status in LINK_DEAD_STATUS:
            notes.append(f"来源已失效（HTTP {status}），已从清单移除：{item.get('url', '')}")
            continue

        kept.append(item)

    return kept, notes


# ====================================================================
# [半手敲] 第 6 天块 10：generate_report —— 接线
# --------------------------------------------------------------------
# 逻辑链：
#   问题 + 来源
#        ↓ (可选) 先剔除失效链接 ← 必须在编号之前做，否则编号和正文会错位
#        ↓ build_report_prompt 编号并拼 Prompt
#        ↓ model.invoke
#        ↓ _load_payload 解析 JSON
#        ↓ apply_citations 剥网址 / 删越界 / 重排 / 补局限性
#        ↓ ResearchReport.model_validate 最后一道校验
#
# 顺序为什么不能换：先探测再编号，模型看到的编号才和最终来源一一对应。
# 如果先编号再剔除，第 2 条被删掉后正文的 [3] 就指向了第 4 条。
# ====================================================================


def generate_report(
    question: str,
    sources: list[dict[str, str]] | None = None,
    model: Any | None = None,
    verify_links: bool = False,
) -> ResearchReport:
    """带着检索资料生成一份可追溯的结构化报告。

    参数：
        question:     研究问题，空字符串会被拒绝。
        sources:      真实搜索结果（来自 AgentRun.sources），为空表示没有资料。
        model:        可选的模型（测试时传假模型，不消耗额度）。
        verify_links: 是否检查来源可访问性。默认关闭，因为要发真实 HTTP 请求。

    抛出：
        ValueError: 问题为空、模型输出不是 JSON、或校验不通过。
    """

    materials = [dict(item) for item in (sources or [])]
    link_notes: list[str] = []

    if verify_links and materials:
        materials, link_notes = drop_unreachable_sources(materials)

    prompt = build_report_prompt(question, materials)
    # 用显式判断而不是 `model = model or create_model()`：
    # `or` 之后 Pyright 仍把变量看成 Any | None，`.invoke` 会一直报「可能为 None」。
    active_model = model if model is not None else create_model()
    response = active_model.invoke(
        [
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
    )

    payload = _load_payload(str(response.content))
    payload, _ = apply_citations(payload, materials, extra_notes=link_notes)

    try:
        return ResearchReport.model_validate(payload)
    except ValidationError as error:
        raise ValueError("模型返回的报告字段不符合要求") from error


# ====================================================================
# [手敲] 第 6 天块 11：report_to_markdown —— 把 [n] 变成可点的链接
# --------------------------------------------------------------------
# 逻辑链：
#   ResearchReport.sources 里 index → url 建一张表
#        ↓
#   正文每个 [n] 用 CITATION_PATTERN 找到，替换成 [[n]](url)
#        ↓
#   Markdown 渲染时 [1] 就变成指向原文的链接
#   为什么不用 [1] 直接接在文末：读者要滚到文末才知道 [1] 是哪条，体验差。
#   为什么找不到 url 时原样返回：宁可不变成链接，也不要生成空链接。
# ====================================================================


def report_to_markdown(report: ResearchReport) -> str:
    """把报告渲染成带可点击引用的 Markdown。"""

    url_by_index = {source.index: source.url for source in report.sources}

    def _link(text: str) -> str:
        def _replace(match: "re.Match[str]") -> str:
            index = int(match.group(1))
            url = url_by_index.get(index)
            return f"[[{index}]]({url})" if url else match.group(0)

        return CITATION_PATTERN.sub(_replace, text)

    lines: list[str] = [f"# {report.title}", "", "## 摘要", _link(report.summary)]
    lines += ["", "## 关键发现"]
    lines += [f"- {_link(item)}" for item in report.key_findings]
    lines += ["", "## 结论", _link(report.conclusion)]

    if report.limitations:
        lines += ["", "## 局限性"]
        lines += [f"- {item}" for item in report.limitations]

    if report.sources:
        lines += ["", "## 来源"]
        lines += [
            f"{source.index}. [{source.title}]({source.url})"
            for source in report.sources
        ]

    return "\n".join(lines)
