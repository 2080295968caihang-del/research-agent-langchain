"""第 2 天的数据结构，第 6 天增加来源引用。

把输入和输出定义成明确的 Pydantic 模型，可以让程序在模型返回内容后
立刻发现字段缺失、类型错误和空文本，而不是把不可靠的字典继续传下去。

第 6 天的关键约定：
    来源（sources）只能由程序从真实搜索结果回填，
    模型只负责在正文里写 [n]。这样「模型编造 URL」这条风险从结构上就被堵死了。

════════════════════════════════════════════════════════════════════
给只学过基础语法的你：本文件出现的 8 个新语法
════════════════════════════════════════════════════════════════════
1. class X(BaseModel)        继承 Pydantic 的基类，这个类立刻获得「构造时校验」的能力：
                             传进来的是 dict，出来的是校验过的对象；不合法直接抛异常。
2. Field(...)                给字段加约束，不改变类型。常用参数：
                                 ge / gt / le / lt   → 数值比较（>=、>、<=、<）
                                 min_length          → str 的字数 / list 的元素数
                                 default_factory     → 给可变类型一个「每次新建」的默认值
                                 description         → 给人看的说明，会进 JSON Schema
3. @field_validator("字段名") 自定义单字段校验：只拿得到这一个字段的值，看不到别的字段。
                             必须与 @classmethod 叠用（先 classmethod 再 validator）。
4. @model_validator(mode="after")
                             自定义整模型校验：所有字段都校验完之后才跑，能同时看多个字段。
                             这种校验器**必须 return self**，返回 None 会让构造失败。
5. raise ValueError          在 Pydantic 校验器里抛这个，会被包装成 ValidationError；
                             构造函数因此报错，而不是得到一个「半坏」的对象。
6. default_factory=list      可变类型默认值必须用工厂函数。写 default=[] 会让所有实例
                             共用同一个列表——经典 Python 默认参数陷阱。
7. *self.key_findings        列表解包：把 list 的元素摊平进新的字面量列表，
                             等价于 [...] + self.key_findings，但少一次列表拼接。
8. -> "ResearchReport"       注解加引号是「前向引用」：类体执行时这个类名还没绑定，
                             裸写会 NameError，写成字符串则延迟到需要时才求值。
"""

import re

from pydantic import BaseModel, Field, field_validator, model_validator


# 正文引用编号的正则：只认方括号里的 1-3 位数字。
# 用 1-3 位而不是 \d+，是为了让「[12345]」这种明显不是编号的内容不被当成引用。
# [手敲] 第 6 天块 2：这条正则是「引用可校验」的全部基础，自己写一遍。
#
# 语法（4 个点）：
#   · r"..." 原始字符串：反斜杠不参与转义。若写成普通字符串，\[ 会被 Python 当成
#     「未知转义序列」（3.12 起会报 SyntaxWarning），正则表达式的反斜杠必须靠 r 保住。
#   · \[ 和 \] 必须转义：方括号在正则里是字符集语法（[abc] = a 或 b 或 c），
#     想匹配字面的方括号就得加反斜杠。
#   · (\d{1,3}) 的圆括号是「捕获组」，不是可省略的装饰：
#     有捕获组时 findall 只返回括号里的内容（"12"），无捕获组才返回整个匹配（"[12]"）。
#     这里故意加括号，就是为了让调用方直接拿到数字。
#   · re.compile 预编译成对象：同一条正则在多处复用，编译一次比每次传字符串
#     让 re 模块内部查缓存更明确，也少了缓存查找的开销。
CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")


# [手敲] 第 6 天块 3：collect_citation_indexes —— 抽号、去重、升序
# --------------------------------------------------------------------
# 逻辑链：
#   正文若干段文本 → 正则找出所有 [n] → 字符串转 int → 塞进 set 去重
#        → sorted() 变成升序列表
#   为什么用 set 而不是 list：同一来源被引用 5 次只应算 1 条，
#   用 list 还要写 `if x not in result`，set 天然去重。
#   为什么是纯函数：不碰模型、不碰网络，所以能单独写测试钉住行为。
#
# 语法（5 个点）：
#   · list[str] 参数注解：Python 3.9 起内置类型可以直接当泛型用，不必 from typing import List。
#     这行只是「说明」，运行时不做检查——真正的类型检查交给 pyright / IDE。
#   · text or ""：texts 里若混入 None，`None or ""` 短路成空串，
#     否则 findall 会抛 TypeError（expected string or bytes-like object）。
#   · 双层 for：外层遍历段落，内层遍历该段里所有匹配。findall 的返回值天然是 list，
#     所以内层可以直接 for，不需要再用 while + search 去手动推进。
#   · found: set[int] = set()：变量注解写在赋值左边，同样是给人和 IDE 看的；
#     set 的 add 平均 O(1)，重复 add 同一个值不报错也不增长。
#   · sorted(found)：set 无序，直接返回顺序不稳定（Py3.7+ 对 int 恰好有序，
#     但这是实现细节，不能依赖）。sorted 返回新 list，不修改原 set。
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

    # 语法（4 个点，这套写法全文复用）：
    #   · @field_validator("question") 注册一个「字段校验器」：名字只声明它管哪个字段，
    #     真正被调用是因为装饰器注册到了类里。参数是字段名，不是变量。
    #   · 两层装饰器的顺序不能反。装饰器自下而上应用，所以先变成 classmethod，
    #     再由 field_validator 登记；反过来先登记再包 classmethod，pydantic 拿不到签名。
    #   · 函数第一个参数是 cls 而不是 self：校验发生在「类」这一层，此时还没有实例。
    #     正因为没有实例，只能通过 value 参数拿到当前字段，看不到别的字段。
    #   · raise ValueError 会被 pydantic 接住，包装成 ValidationError；构造函数直接报错。
    #     所以校验器里禁止「静默 return None」，那会让字段变成 None 而不是失败。
    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        """去掉首尾空格，并拒绝空问题。"""

        cleaned = value.strip()   # strip() 只削首尾空白（含 \n \t），中间的空格保留
        if not cleaned:           # 空串和全空白串都是 falsy，一个判断覆盖两种情况
            raise ValueError("研究问题不能为空")
        return cleaned            # 返回值会「替换」原字段值：校验器顺手完成规范化


# [手敲] 第 6 天块 1：SourceRef —— 一条来源长什么样
# --------------------------------------------------------------------
# 逻辑链：
#   真实搜索结果 dict  → SourceRef(index, title, url)  → 进 ResearchReport.sources
#   为什么要单独建模型，不直接用 dict：
#     dict 里的 url 没有任何约束，搜索服务商返回一个 javascript: 链接也能混进来。
#   为什么 index 只校验 ge=1、不校验连续性：
#     连续性由 report 层重排时保证；schema 只负责「别是 0 或负数」。
#
# 语法（4 个点）：
#   · class SourceRef(BaseModel)：继承后这个类就自带你声明的一切能力——构造时校验、
#     类型转换（"3" 会转成 3）、以及 model_validate(dict) / model_dump() 两条序列化通道。
#   · Field(ge=1)：ge = greater or equal，等价于 >=。同类参数还有 gt/lt/le、
#     min_length/max_length（str 和 list 都适用）、pattern（正则）。
#   · Field(min_length=1)：对 str 是「至少 1 个字符」，对 list 是「至少 1 个元素」。
#     注意它不挡纯空格 —— " " 长度为 1 能通过，所以下面还要单独写 strip 校验。
#   · index: int 写在 Field 左边：等号左边是类型注解（给校验和 IDE），
#     等号右边是默认值/约束。两者都会写进 __annotations__ 和 model_fields。
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

    # 语法：一个字段可以叠多个 field_validator（不同函数按定义顺序依次执行），
    # 但同一个字段的两个校验器各管一件事，比写成一个巨型 if 更容易单独测试。
    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        """只接受 http/https。

        javascript:、data:、file: 这类协议一律拒绝——它们可能来自被污染的结果，
        放进报告后如果被渲染成链接会变成可执行内容。
        """

        cleaned = value.strip()
        # 语法：startswith 可以接收「元组」，语义是「任意一个前缀匹配即为真」。
        #       不能用 startswith("http://", "https://") —— 第二个位置是 start/end 索引，
        #       传字符串会直接 TypeError。
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

    # 语法（1 个必须记住的坑）：default_factory=list 而不是 default=[]。
    #   default=[] 会让所有实例共用「同一个 list 对象」——经典 Python 默认参数陷阱：
    #   给其中一个报告的 limitations 追加内容，下一个新建报告会带着上一次的残留。
    #   default_factory 每次实例化都调用一次 list()，得到各自独立的新列表。
    #   凡是可变类型（list / dict / set）做默认值都必须走 factory。
    limitations: list[str] = Field(
        default_factory=list,
        description="研究局限性，可以为空",
    )
    # 语法：sources: list[SourceRef] 是「嵌套模型」——传进来的是 dict 列表时，
    #   pydantic 会逐个用 SourceRef 校验一遍再组装；列表里任何一条不合格，整个报告构造失败。
    sources: list[SourceRef] = Field(
        default_factory=list,
        description="正文实际引用到的来源，编号与 [n] 对应；由程序回填",
    )

    # 语法：field_validator 可以一次接收多个字段名，函数会对每个字段各跑一次
    #   （不是把三个字段一起传进来）。所以下面这个函数的 value 始终是单个 str。
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
        # 语法：any(... for ...) 是生成器表达式，短路——碰到第一个空串就停止遍历。
        #       not item 对空串为 True，所以这里找的是「清洗后仍为空」的项。
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
    #
    # 语法（5 个点，这是全文最容易踩的一处）：
    #   · model_validator 与 field_validator 的分工：前者拿到整个模型（能同时看多个字段），
    #     后者只拿到单个字段的值。跨字段规则（引用 vs 来源清单）只能用前者。
    #   · mode="after" 的校验器「必须 return self」。忘了 return（写成裸 return）时，
    #     pydantic 拿到 None，构造会失败并报「校验器返回值不是模型实例」。
    #   · -> "ResearchReport" 加引号是「前向引用」：类体正在执行时，ResearchReport
    #     这个名字还没绑定到全局，直接写裸类名会 NameError。字符串形式的注解延迟求值。
    #   · *self.key_findings 是列表解包：把 list 的元素摊平进新的字面量 list，
    #     等价于 [self.summary, self.conclusion] + self.key_findings，但少一次拼接。
    #   · {source.index for source in self.sources} 是集合推导：结果自动去重，
    #     后面才能直接用集合差集 cited - allowed 求「越界编号」。
    @model_validator(mode="after")
    def citations_must_match_sources(self) -> "ResearchReport":
        """正文引用和来源清单必须一一对得上。"""

        texts = [self.summary, self.conclusion, *self.key_findings]
        cited = set(collect_citation_indexes(texts))
        allowed = {source.index for source in self.sources}

        # 语法：`not self.sources` 对空列表为 True —— 空容器是 falsy，
        #       比写 len(self.sources) == 0 更直接。
        if not self.sources:
            if cited:
                raise ValueError(f"正文引用了 {sorted(cited)}，但来源清单是空的")
            return self

        if not cited:
            raise ValueError("报告列了来源，但正文没有任何 [n] 引用")

        # 语法：set 之间的 - 是差集（在 cited 里、不在 allowed 里）。
        #       sorted 只是为了报错信息稳定（集合本身无序，直接打印每次顺序可能不同）。
        unknown = sorted(cited - allowed)
        if unknown:
            raise ValueError(f"正文引用了不存在的来源编号：{unknown}")

        return self
