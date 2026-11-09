r"""研究报告生成与来源回填。

第 2 天：验证问题 -> 构造 Prompt -> 调用模型 -> 解析 JSON -> Pydantic 校验。
第 6 天：把搜索到的资料喂进 Prompt，模型只写 [n]，来源由程序回填并校验。

第 6 天最重要的两条不变式：
    1. 报告里的 url 只能来自真实搜索结果，模型写的 url 一律剥掉；
    2. 正文的 [n] 必须能在来源清单里找到，找不到就删掉并记进 limitations。

════════════════════════════════════════════════════════════════════
给只学过基础语法的你：本文件出现的 10 个新语法
════════════════════════════════════════════════════════════════════
1. re.compile(r"...")        把正则预编译成对象，重复使用时比每次传字符串更快也更明确。
                             r 前缀是原始字符串：反斜杠不转义，正则必须靠它保住。
2. [^...] 字符集取反          匹配「任意一个不在方括号里的字符」，常用来「一路吃到某个字符为止」。
3. (\d{1,3}) 捕获组          只有括号里的内容会被 findall / group(1) 取出来；
                             没有括号时 findall 返回整段匹配。
4. enumerate(列表, start=1)  同时拿到「序号」和「元素」，返回的是 (序号, 元素) 二元组，
                             左边可以一次解包成两个变量。start 只改起点，不改长度。
5. dict(原字典)              浅拷贝：新的 dict 对象，但里面的值还是同一批对象。
                             只改顶层键值时够用；要改嵌套结构得用 copy.deepcopy。
6. pop(键, 默认值)           取出并删除；第二个参数是「键不存在时返回什么」，
                             不写它键不存在就抛 KeyError。
7. list.extend(可迭代)       把元素逐个加进去；append 会把整个对象当成一个元素。
                             extend(str(x) for x in y) 传的是生成器，元素是逐个产出的。
8. Callable[[str], str]      类型注解：「接收 str、返回 str 的可调用对象」。
                             方括号里最后一个是返回值类型，前面是参数类型列表。
9. re.sub(模式, 回调, 文本)   替换串可以传函数：每匹配到一处就调用一次，
                             用它的返回值替换这一段。需要「根据匹配内容算」时只能用回调。
10. tuple[A, B] 与解包       返回定长二元组的类型写法；调用方用 a, b = f() 解包。
                             它是 tuple 不是 list，长度不可变，也就不会被误 append。
"""

import json
import re
import time
from typing import Any, Callable

import requests
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.agent import create_model, record_usage
from app.schemas import (
    CITATION_PATTERN,
    ResearchQuestion,
    ResearchReport,
    collect_citation_indexes,
)
from app.tools import MIN_USEFUL_RESULTS


# 正文里的裸网址。结尾排除中英文标点和右括号，避免把「见 https://a.com。」整段吞掉。
#
# 语法（3 个点）：
#   · [^...] 是「字符集取反」：匹配任意一个不在方括号里的字符。
#     所以 [^\s，。]+ 的含义是「一个或多个既不是空白也不是中文标点的字符」。
#   · 字符集里 ] 和 " ' 都需要处理：] 放在首位表示字面量，\ 用来转义，
#     这就是 [^...)\]】\"'] 里那串看起来杂乱的来源。
#   · 末尾的 + 是贪婪量词：它会一路吃到遇见「排除字符」为止，
#     正因为排除了 。，；、 这些标点，句号才不会被当成 URL 的一部分。
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
#
# 语法（4 个点）：
#   · enumerate(sources, start=1) 返回 (序号, 元素) 二元组，解包成 index 和 item。
#     start 只是起点，不改变长度，所以序号和列表长度仍然一致。
#   · (item.get("title") or "").strip() or (item.get("url") or "") 是「三级兜底」：
#     get 拿不到返回 None → None or "" 得 "" → strip 后若为空（falsy）→ 再取 url；
#     url 也可能缺失，所以那里的 .get 也补了 or ""。整条链保证结果一定是 str。
#   · item.get('url', '') 与 item.get('url') or '' 的区别：前者只在「键不存在」时用默认值，
#     键存在但值为 None 时会返回 None；后者把 None 也兜住。字符串插值里用前者足够，
#     因为 dict 里 url 缺失通常就是 key 不存在。
#   · snippet[:200] 是切片，不是「截断函数」：越界不报错，短于 200 就返回原串。
#   · "\n".join(lines) 把 list[str] 拼成一个字符串；用 join 而不是循环 +=，
#     因为字符串不可变，循环拼接每次都要复制整串，复杂度 O(n²)。
# ====================================================================


def format_sources_block(sources: list[dict[str, str]]) -> str:
    """把搜索结果排成带编号的纯文本资料清单。"""

    lines: list[str] = []
    for index, item in enumerate(sources, start=1):
        title = (item.get("title") or "").strip() or (item.get("url") or "")
        lines.append(f"[{index}] {title}")
        # 语法：f-string 里不能出现和定界符相同的引号（3.12 之前）。
        #       外层是双引号，所以字典键用单引号 'url'，否则会提前结束字符串。
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
#
# 语法（4 个点）：
#   · sources: list[dict[str, str]] | None = None —— 「或 None」的联合类型。
#     默认值写成 None 而不是 []，同样是为了躲开可变默认参数陷阱；函数内部再用 or [] 兜。
#   · if sources: 判断的是「非空」而不是「不是 None」。空列表是 falsy，
#     所以 sources=[] 和 sources=None 会走同一个分支，不需要写两个条件。
#   · parts += [...] 等价于 parts.extend([...])：就地追加多个元素，
#     和 parts.append([...]) 不同——后者会把整个列表当成一个元素塞进去。
#   · f"研究问题：{validated_question.question}" 用的是校验后的对象，
#     而不是入参 question：入参可能是 "  问题  "，这里要的是 strip 过的版本。
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


# ====================================================================
# 解析三件套：文本 → dict → 校验过的模型
# --------------------------------------------------------------------
# 分层理由：每一步只做一件事，出错时报错点精确。
#   _remove_json_fence  只管「模型套了 ```json 外壳」
#   _load_payload       只管「是不是合法 JSON、顶层是不是对象」
#   parse_report        只管「字段齐不齐、类型对不对」
#
# 语法（6 个点）：
#   · text.splitlines() 按「任意换行符」切（\n、\r\n、\r 都算），比 split("\n") 稳；
#     带 \r\n 的文本用 split("\n") 会留下尾随的 \r。
#   · lines[1:-1] 是「去掉第一个和最后一个元素」的切片写法，负索引从末尾数；
#     这就是「砍掉 ```json 和 ``` 两行」的惯用法，不需要写 pop(0) 再 pop()。
#   · except json.JSONDecodeError 只捕获这一个具体异常，网络错误、内存错误照旧向上抛。
#     捕获 Exception 会把拼写错误一类的真 bug 一起吞掉。
#   · raise ... from error 建立「异常链」：新异常.__cause__ 指向原异常，
#     报错时两个 traceback 都会显示。不加 from 会变成
#     「During handling of the above exception, another exception occurred」，
#     加 from 则显示「The above exception was the direct cause」，语义更准确。
#   · payload: Any = json.loads(...) 先标注成 Any：json.loads 静态类型就是 Any，
#     紧随其后的 isinstance 检查才是真正的窄化，之后 pyright 才把 payload 当 dict。
#   · model_validate(payload) 和 ResearchReport(**payload) 的区别：
#     前者接受「已经存在的 dict」，后者要求逐个关键字展开；字段名带非法字符时就只能用前者。
# ====================================================================


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
    """删掉模型自己写的来源字段。来源只能由程序回填。

    语法：dict(payload) 是「浅拷贝」——新 dict 对象，但里面的值仍是同一批对象。
    这里够用，因为只删顶层键；若后面要改嵌套结构就得 copy.deepcopy。
    pop(key, None) 的第二个参数是「键不存在时的默认值」，不写它会抛 KeyError。
    """

    cleaned = dict(payload)
    for key in ("sources", "source", "urls", "references", "citations"):
        cleaned.pop(key, None)
    return cleaned


def _report_texts(payload: dict[str, Any]) -> list[str]:
    """取出报告里可能带引用的所有正文段落。

    语法：isinstance(value, str) 是必需的防御。模型偶尔把 summary 返回成数字或列表，
    不检查直接 .strip() 会 AttributeError —— 那是「程序崩」而不是「数据不合规」，
    区别在于用户看到的是 traceback 而不是一句可读的报错。

    语法：texts.extend(生成器) 和 texts.append(列表) 完全不同。
    extend 把可迭代对象的元素逐个加进去；append 会把「生成器对象本身」当成一个元素。
    写成 append(str(x) for x in findings) 时，texts 末尾会多出一个 generator，
    直到后面 "".join(texts) 才炸，报错点离病因很远（day6 手敲区真踩过这个坑）。
    """

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
    """对 summary / conclusion / key_findings 里的每段文本做同一个转换。

    语法：Callable[[str], str] 读作「接受一个 str、返回一个 str 的可调用对象」。
    方括号里最后一个是返回值类型，前面是参数类型列表。这个注解不限制必须传函数，
    匿名函数 lambda、类的 __call__ 实例、functools.partial 都满足。

    语法：updated = dict(payload) 先浅拷贝，再逐键覆盖，最后返回新 dict。
    这样调用方手里的原 payload 不会被改动 —— 纯函数风格，测试里才能放心复用同一份数据。
    """

    updated = dict(payload)

    for key in ("summary", "conclusion"):
        if isinstance(updated.get(key), str):
            updated[key] = transform(updated[key])

    findings = updated.get("key_findings")
    if isinstance(findings, list):
        updated["key_findings"] = [transform(str(item)) for item in findings]

    return updated


def _scrub_urls(text: str) -> str:
    r"""把正文里模型自己写的网址删掉，并收拾留下的多余空格。

    语法：re.sub(模式, 替换串, 原文) 把「所有」匹配都替换掉，不是只换第一个
    （只换第一个的是 count=1）。替换串传空字符串就等于删除。

    语法：re.sub(r"[ \t]{2,}", " ", ...) 里的 {2,} 是「至少 2 次」量词。
    删掉网址后原位置常留下两个以上连续空格，这一步把多个压成一个；
    只用 " " 而不用 "" 是为了保住词与词之间的一个空格。

    语法：.strip() 放在最后：删完网址句子结尾可能剩下一个空格，
    不 strip 的话下一段拼接时会多出一个空格。

    注意 docstring 前面的 r：文档里要原样显示 \t 这类正则写法，
    不加 r 的话 Python 会先把 \t 解释成真正的制表符，读到的就不再是代码了。
    """

    without_urls = URL_PATTERN.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", without_urls).strip()


def _drop_marks(text: str, indexes: list[int]) -> str:
    """删掉指定的 [n] 标记。

    语法：这里用循环 replace 是安全的，和 _renumber_marks 必须用一次 re.sub 不同。
    区别在于「删除」不会产生新的待删目标：删掉 [9] 后文本里不会冒出新的 [9]；
    而「改号」会产生串号——1→2 之后再处理 2→1，刚写进去的 2 又被改成 1。

    f"[{index}]" 而不是 f"[{index}" —— 右括号也要一起删，否则会剩半个标记。
    """

    for index in indexes:
        text = text.replace(f"[{index}]", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _renumber_marks(text: str, mapping: dict[int, int]) -> str:
    r"""按映射一次性改写所有 [n]，不级联。

    语法（4 个点）：
    · 嵌套函数 _replace 是「回调」：re.sub 每匹配到一处就调用它一次，
      把 match 对象传进来，用它返回的字符串替换这一段。
      如果替换串是固定字符串就直接传字符串；需要「根据匹配内容算」时才传函数。
    · 内层函数名可以随便取，但写成 _replace 是惯例；它只在本函数作用域可见。
    · match: "re.Match[str]" 加引号同样是前向引用，避免在运行期求值泛型；
      re.Match[str] 表示「匹配 str 得到的 Match 对象」。
    · match.group(1) 取第 1 个捕获组（正则里 (\d{1,3}) 那对括号）；
      match.group(0) 是「整段匹配的原文」，所以下面兜底返回它表示「原样不动」。

    docstring 前的 r 同样是为了让 \d 原样显示（否则 3.12+ 会报 SyntaxWarning）。
    """

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

    # 语法：get(...) or [] 一次兜住三种情况——键不存在（None）、值为 None、值为空列表。
    # 直接写 payload.get("limitations", []) 兜不住「键存在但值是 None」。
    existing = [str(item).strip() for item in (payload.get("limitations") or [])]
    existing = [item for item in existing if item]
    fresh: list[str] = []

    for raw in notes:
        note = (raw or "").strip()
        if not note:
            continue

        # 语法：a in b 对 str 是「子串判断」。note in item or item in note 双向各查一次，
        #       于是「A 包含 B」和「B 包含 A」都算重复。any(...) 短路，命中即停。
        duplicated = any(note in item or item in note for item in existing)
        if duplicated:
            continue

        fresh.append(note)

    # 语法：`A if 条件 else B` 是条件表达式（三元的 Python 写法），
    #       整行赋值只执行其中一个分支；靠它统一「插到最前还是追加到末尾」两个方向。
    #       at_front 默认 False，所以平常是「原有 + 新增」，只有零资料/资料不足时才置前。
    payload["limitations"] = (fresh + existing) if at_front else (existing + fresh)


def apply_citations(
    payload: dict[str, Any],
    sources: list[dict[str, str]],
    extra_notes: list[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把模型输出和真实来源对齐：剥网址、删越界编号、重排来源、补局限性。

    返回 (修好的 payload, 本次做过的修改说明)。
    修改说明会并入 limitations，让读者知道报告被动过哪些手脚。

    语法（6 个点）：
    · 返回类型 tuple[dict[str, Any], list[str]] 表示「二元组」，调用方用
      payload, notes = apply_citations(...) 解包。它不是 list，长度不可变。
    · list(extra_notes or []) 先复制再改：notes.append 会修改列表本身，
      直接写 notes = extra_notes or [] 的话，就会把调用方传进来的 list 也改掉
      （调用方在 generate_report 里还要用 link_notes 做别的判断）。
    · lambda text: _drop_marks(text, unknown) 是闭包：lambda 体里用到外层的 unknown，
      调用时才读它的值。变量在 lambda 定义之后被改，lambda 看到的是新值。
    · sorted(set(cited)) 先转集合去重、再排序。cited 来自 collect_citation_indexes，
      本身已去重有序，这里再用 set 是防御「有人传了脏数据进来」。
    · {old: new for new, old in enumerate(order, start=1)} 是字典推导，
      注意解包的顺序被故意反过来写成 new, old：enumerate 产出 (序号, 值)，
      这么写就得到「旧编号 → 新编号」的映射，正好是 _renumber_marks 需要的方向。
    · [NO_SOURCE_NOTE, *notes] 里的 *notes 是「解包并摊平」：
      等价于 [NO_SOURCE_NOTE] + notes，但少建一个临时列表。
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
    """探测一个网址的状态码；网络异常时返回 None。

    语法（5 个点）：
    · 返回注解 int | None：Python 3.10+ 的联合类型写法，等价于 Optional[int]。
      返回 None 表示「不知道」，返回数字表示「服务端明确答复了」——两者语义不同，
      调用方必须区分，不能把 None 当成 0 或 404。
    · requests.head(url, timeout=timeout, allow_redirects=True)：
      timeout 必须显式给，requests 默认「无限等待」，遇到慢站点会卡死整个进程。
      传单个数字表示「连接和读取各自都不超过 N 秒」。
    · except requests.RequestException 是 requests 所有网络异常的公共基类
      （连接失败、超时、DNS 错误、SSL 错误都在内）。捕获它比捕获 Exception 精确，
      不会顺带吞掉代码里的 KeyError。
    · with ... as response 自动关闭：7b 从「手动 response.close()」改成 with，
      因为手动 close 在 try 里只覆盖了「正常返回」这条路——
      只要 status_code 读完之前抛异常，close() 就永远走不到，连接被漏掉。
      with 保证无论正常返回还是抛异常都会关（本质是 try/finally 的语法糖）。
    · stream=True 只建立连接、不下载正文：这一层只要状态码，正文一个字节都不需要。
    """

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
    """剔除明确失效的来源，返回 (保留的来源, 说明)。

    语法（3 个点）：
    · 判断写成 status is not None and status in LINK_DEAD_STATUS：
      None in {404, 410} 本身不会报错（返回 False），但显式先排除 None
      是为了让「网络异常」和「明确 404」在读代码时就是两件事，不是靠巧合相等。
    · continue 而不是 if/else：命中的走「记账 + 跳过」，没命中的直接落到下面的
      kept.append。用 continue 省掉一层缩进，是「早返回」精神的循环版。
    · 返回定长二元组而不是一个带状态的对象：调用方只有一处，用解包最省事。
    """

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

    语法（4 个点）：
    · [dict(item) for item in (sources or [])] 是列表推导 + 逐条浅拷贝。
      为什么不直接用 sources：drop_unreachable_sources 之后会整体替换 materials，
      先复制一份，调用方传进来的 list 就不会被这条流水线改到。
    · model: Any | None = None —— 「不传就用默认模型」。写成 Any 而不是具体类型，
      是为了测试时能塞进一个只有 invoke 方法的假对象，不必真去实现模型接口。
    · SystemMessage / HumanMessage 是 LangChain 的消息包装：前者是「角色设定」，
      后者是「用户这次的请求」。两者拼成 list 一起传给 invoke，模型按角色区分优先级。
    · str(response.content) 外面套 str：新版 LangChain 的 content 可能是字符串，
      也可能是「内容块列表」（多模态时）。套一层 str 保证后面 json.loads 拿到的一定是文本。
    """

    materials = [dict(item) for item in (sources or [])]
    link_notes: list[str] = []

    if verify_links and materials:
        materials, link_notes = drop_unreachable_sources(materials)

    prompt = build_report_prompt(question, materials)
    # 用显式判断而不是 `model = model or create_model()`：
    # `or` 之后 Pyright 仍把变量看成 Any | None，`.invoke` 会一直报「可能为 None」。
    active_model = model if model is not None else create_model()
    # [第 9 天] 这是第 4 个模型调用点，也是最贵的一处（output 几百到上千 token）。
    # 这里绝不能 reset_usage —— 会把 agent 那三次的累计全清掉，只剩报告这一次。
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
    """把报告渲染成带可点击引用的 Markdown。

    语法（5 个点）：
    · {source.index: source.url for source in report.sources} 是字典推导，
      建一张「编号 → 网址」的查找表，后面每个 [n] 查它 O(1)，
      不用每次遍历 sources 列表去找。
    · 两层嵌套函数：_link 给一段文本套链接，_replace 是交给 re.sub 的逐匹配回调。
      分两层是因为 re.sub 的回调签名固定（只收 match），而我们要额外传 text 进去。
    · f"[[{index}]]({url})" 是 Markdown 链接语法的字面量：
      想输出 [[1]](http://…) 这串字符，方括号圆括号在 f-string 里都按原样输出，
      只有 {index} {url} 会被替换。所以「链接文字」是 [1]，外层再套一层方括号是链接语法。
    · match.group(0) 是整段匹配的原文（含方括号）。url 查不到时返回它，
      等于「这个 [n] 原样保留」，不会渲染成坏链接。
    · lines += [...] 反复追加而不是一次性写完整列表：让每一段的拼接顺序在代码里
      从上到下一目了然，读起来就是最终的渲染顺序。
    """

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
