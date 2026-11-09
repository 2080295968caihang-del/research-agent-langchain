"""研究报告生成和来源格式化逻辑。

第 2 天开始在这里增加报告输出逻辑。
"""

import json
import re
import time
from typing import Any,Callable

import requests
from langchain_core.messages import HumanMessage,SystemMessage
from pydantic import ValidationError

from app.agent import create_model, record_usage
from app.schemas import (
    CITATION_PATTERN,
    ResearchQuestion,
    ResearchReport,
    collect_citation_indexes,
)

from app.tools import MIN_USEFUL_RESULTS

URL_PATTERN = re.compile(r"https?://[^\s，。；、）)\]】\"']+")

LINK_DEAD_STATUS = {404, 410}

LINK_SKIP_STATUS = {401, 403, 405, 429, 501}

LINK_TIMEOUT_SECONDS = 5.0

NO_SOURCE_NOTE = "本次未检索到可靠来源，报告内容仅基于模型已有知识，未经外部资料验证。"

REPORT_SYSTEM_PROMPT = """
你是一个严谨的研究报告助手。
请根据【研究问题】和【参考资料】生成一份结构化报告。
参考资料每一条都带编号，例如[1]、[2]。正文陈述某个事实时，在该句末尾标注来源编号。

只返回一个合法 JSON 对象，不要使用 Markdown 代码块，不要添加 JSON 以外的解释。
JSON 必须包含以下字段：
- title: 字符串，报告标题
- summary: 字符串，摘要
- key_findings: 字符串数组，至少一项关键发现
- conclusion: 字符串，结论
- limitations: 字符串数组，列出局限性，没有时返回空数组

硬性要求：
1.只能使用参考资料里出现过的编号，不允许出现资料里没有的编号。
2.绝对不要在正文里写网址（http:// 或 https://）。来源由程序根据 [n] 自动回填。
3.资料不足以支撑结论时不要猜，请在 limitations 里明确写出【资料不足】，
    并在 title 和 conclusion 中说明这是一个尚未确认的问题。 
"""

def format_sources_block(sources:list[dict[str,str]]) -> str:
    #把搜索结果排成带编号的纯文本资料清单
    lines: list[str] = []
    for index, item in enumerate(sources, start=1):
        title = (item.get("title") or "").strip() or (item.get("url") or "")
        lines.append(f"[{index}] {title}")
        lines.append(f"    URL: {item.get('url', '')}")
        snippet = (item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"    摘要: {snippet[:200]}")
    return "\n".join(lines)

def build_report_prompt(
        question:str,
        sources:list[dict[str,str]] | None = None,
) -> str:
    #验证研究问题，并把资料一起拼成用户 Prompt。
    validated_question = ResearchQuestion(question=question)
    parts = [
        "请研究下面的问题，并严格按照系统消息规定的 JSON 字段返回报告。",
        "",
        f"研究问题：{validated_question.question}",
    ]

    if sources:
        parts += [
            "",
            "参考资料（正文引用时使用方括号里的编号）",
            format_sources_block(sources),
        ]
    else:
        parts += [
            "",
            "本次没有检索到任何外部资料。正文不要写任何 [n] 引用，",
            "limitations 第一条必须说明【未检索到可靠来源】。",
        ]

    return "\n".join(parts)

"""
用户传入 question + sources（真实搜索结果，可能为空）
    ↓
build_report_prompt(question, sources=None)
    ↓
ResearchQuestion(question=question)
    ↓
Pydantic 校验问题      去掉首尾空格；空串直接抛 ValueError，不让它白烧一次模型调用
    ↓
parts = ["请研究下面的问题…", "", f"研究问题：{validated_question.question}"]
    ↓                         ↑ 用的是 strip 之后的值，不是原始入参
判断 sources 是否为空
    │
    ├── 有资料 ──────────────────────────────────────────────┐
    │       ↓                                                │
    │   format_sources_block(sources)                        │
    │       ↓                                                │
    │   enumerate(start=1) 编号  →  [1] 标题 / URL / 摘要      人从 1 数，报告里没人写 [0]
    │       ↓                                                │
    │   摘要截到 200 字                                       5 条 × 200 字 ≈ 1000 字，不撑爆上下文
    │       ↓                                                │
    │   追加「参考资料（正文引用时使用方括号里的编号）」+ 清单     这里的编号 = 最终报告的编号
    │                                                        │
    └── 无资料 ──────────────────────────────────────────────┤
            ↓                                                │
        追加「本次没有检索到任何外部资料，正文不要写任何 [n] 引用，    沉默 ≠ 安全：
              limitations 第一条必须说明…」                      不写这句，模型会以为用户忘了给，
                                                                于是凭记忆编报告并自己贴 URL
    ↓
"\n".join(parts)
    ↓
返回 Prompt 字符串（纯文本，交给 HumanMessage）
    ↓
发送给大模型
    ↓
模型返回 JSON → apply_citations 回填来源 → ResearchReport 校验
"""


def _remove_json_fence(raw_text: str)-> str:
    text = raw_text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return text

def parse_report(raw_text: str) -> ResearchReport:
    
    try:
        payload:Any = json.loads(_remove_json_fence(raw_text))
    except json.JSONDecodeError as error:
        raise ValueError ("模型没有返回有效 JSON 报告")from error

    try:
        return ResearchReport.model_validate(payload)
    except ValidationError as error:
        raise ValueError ("模型返回的报告字段不符合要求")from error

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



def _sanitize_payload(payload:dict[str,Any]) ->dict[str,Any]:
    #删除模型自己写的来源，来源由程序回填
    cleaned = dict(payload)
    for key in ("sources","source","urls","references","citations"):
        cleaned.pop(key,None)
    return cleaned

def _report_texts(payload:dict[str,Any]) ->list[str]:
    #取出报告里可能带引用的所有正文段落
    texts:list[str] = []
    for key in ("summary","conclusion"):
        value = payload.get(key)
        if isinstance(value,str):
            texts.append(value)

    findings = payload.get("key_findings")
    if isinstance(findings,list):
        texts.extend(str(item) for item in findings)

    #局限性同样会被模型写入引用编号，必须一起纳入清洗。
    limits = payload.get("limitations")
    if isinstance(limits,list):
        texts.extend(str(item) for item in limits)

    return texts

def _map_texts(payload: dict[str, Any], transform: Callable[[str], str]) -> dict[str, Any]:

    updated = dict(payload)

    for key in ("summary","conclusion"):
        if isinstance(updated.get(key),str):
            updated[key] = transform(updated[key])

    findings = updated.get("key_findings")
    if isinstance(findings,list):
        updated["key_findings"] = [transform(str(item)) for item in findings]

    #局限性要跟着一起改，否则③④两步只会改正文、留下不一致的编号。
    limits = updated.get("limitations")
    if isinstance(limits,list):
        updated["limitations"] = [transform(str(item)) for item in limits]

    return updated


def _scrub_urls(text: str) -> str:
    #删除模型自己写的url
    without_urls = URL_PATTERN.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", without_urls).strip()           

def _drop_marks(text: str, indexes: list[int]) -> str:
    for index in indexes:
        text = text.replace(f"[{index}]", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()

def _renumber_marks(text: str, mapping: dict[int, int]) -> str:
    def _replace(match: "re.Match[str]") -> str:
        old = int(match.group(1))
        return f"[{mapping[old]}]" if old in mapping else match.group(0)

    return CITATION_PATTERN.sub(_replace, text)


def _push_limits(payload: dict[str, Any], notes: list[str], at_front: bool = False) -> None:
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
            "已移除不存在的引用编号 "
            + "、".join(str(index) for index in unknown)
            + f"（本次只有 1-{available} 号来源可用）"
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
    
def _probe_url(url: str, timeout: float = LINK_TIMEOUT_SECONDS) -> int | None:
    """探测一个网址的状态码；网络异常时返回 None。"""

    try:
        with requests.head(url, timeout=timeout, allow_redirects=True) as response:
            status = response.status_code
    except requests.RequestException:
        return None

    if status in (405, 501):
        try:
            with requests.get(
                url, timeout=timeout, allow_redirects=True, stream=True
            ) as response:
                status = response.status_code
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

def generate_report(
    question: str,
    sources: list[dict[str, str]] | None = None,
    model: Any | None = None,
    verify_links: bool = False,
) -> ResearchReport:
   
    materials = [dict(item) for item in (sources or [])]
    link_notes: list[str] = []

    if verify_links and materials:
        materials, link_notes = drop_unreachable_sources(materials)

    prompt = build_report_prompt(question, materials)
    # 用显式判断而不是 `model = model or create_model()`：
    # `or` 之后 Pyright 仍把变量看成 Any | None，`.invoke` 会一直报「可能为 None」。
    active_model = model if model is not None else create_model()
    #第9天：第 4 个模型调用点，也是最贵的一处。这里绝不能 reset_usage（会清掉前面三次）
    report_started = time.perf_counter()
    response = active_model.invoke(
        [
            SystemMessage(content=REPORT_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
    )
    record_usage(response, (time.perf_counter() - report_started) * 1000)

    payload = _load_payload(str(response.content))
    payload, _ = apply_citations(payload, materials, extra_notes=link_notes)

    try:
        return ResearchReport.model_validate(payload)
    except ValidationError as error:
        raise ValueError("模型返回的报告字段不符合要求") from error


def report_to_markdown(report: ResearchReport) -> str:

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
        lines += [f"- {_link(item)}" for item in report.limitations]

    if report.sources:
        lines += ["", "## 来源"]
        lines += [
            f"{source.index}. [{source.title}]({source.url})"
            for source in report.sources
        ]

    return "\n".join(lines)
