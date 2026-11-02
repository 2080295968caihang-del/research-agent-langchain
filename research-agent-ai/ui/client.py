"""第 8 天：界面与接口之间的一层。

════════════════════════════════════════════════════════════════════
一、这一层存在的理由
════════════════════════════════════════════════════════════════════
界面有两条路能拿到报告：

    A. `from app.agent import run_agent` —— 在 Streamlit 进程里直接跑业务
    B. `requests.post("http://127.0.0.1:8000/research")` —— 走 HTTP 调接口

选 B。理由不是「分层好看」，是**能顺手多做一次验收**：
    · 界面跑通 = 接口的 200 / 422 / 502 / 504 / 500 五条路都被真实端口走过
    · 选 A 的话，接口层永远只有 TestClient 走过，真实端口没被碰过
    · Streamlit 的脚本模型是「每次交互整份重跑」，选 A 意味着每次重跑都可能
      触发一次昂贵的模型调用；选 B 至少还有「按钮」这个明确边界

代价是一次进程内往返（约 1ms）和一个 URL 常量要维护。值。

════════════════════════════════════════════════════════════════════
二、逻辑链
════════════════════════════════════════════════════════════════════
    post_research(question)
         ↓ POST {base_url}/research  {"question": ...}  timeout=180
    ┌── 200         → return resp.json()
    ├── 4xx / 5xx   → ResearchAPIError(status=码, detail="中文提示：后端 detail")
    ├── ReadTimeout → ResearchAPIError(status=None, "超过 180 秒没返回…")
    ├── ConnectTimeout /
    │   ConnectionError → ResearchAPIError(status=None, "连不上服务，先确认…")
    └── 200 非 JSON → ResearchAPIError(status=200, "可能被代理或网关拦截了")

`status=None` 是刻意的：表示「请求根本没到达服务端」，与「到达了但服务端
报错」是两类问题。排障第一步就是分清这两类——前者查进程和端口，后者查日志。

════════════════════════════════════════════════════════════════════
三、三个必须知道的细节
════════════════════════════════════════════════════════════════════
1. 422 的 detail 有两种形状，必须都兼容
   · Pydantic 自动校验   → {"detail": [{"type": "string_too_short", "loc": [...]}]}
   · 7b 的 ValueError handler → {"detail": "内容不合规：..."}
   直接把 detail 塞进 st.error，用户看到的就是一坨 Python 字典。
   块 2 负责把列表翻成一句中文。

2. except 的顺序：ConnectionError 必须在 Timeout 前面
   实测 MRO（requests 2.34.2）：
       ConnectTimeout → ConnectionError → Timeout → RequestException
   ConnectTimeout 同时属于两个父类，**谁在前谁接走**。它发生在 TCP 还没
   连上，真相是「连不上」，不该说「服务可能还在跑」。
   而 ReadTimeout 是 Timeout 的子类、不是 ConnectionError 的子类
   （实测 issubclass(ReadTimeout, ConnectionError) is False），
   所以它一定会落到 Timeout 分支 —— 两个方向都安全。

   ← 这一条第一版写反了（写成「Timeout 必须在前」），
     靠 test_connect_timeout_is_reported_as_unreachable 抓出来。

3. 200 也可能失败
   被 HTTP 代理或网关拦下时，状态码是 200 或 502，body 却是 HTML。
   所以 `response.json()` 自己也要 try / except ValueError。

════════════════════════════════════════════════════════════════════
四、为什么不 import streamlit
════════════════════════════════════════════════════════════════════
Streamlit 的脚本模型几乎无法单测：没有函数入口，每次交互整份重跑。
但「发请求 + 翻译错误」是纯 Python，是界面上唯一可能出现逻辑错的地方，
能测且必须测。

单独放一个模块后，`tests/test_ui.py` 只 import `ui.client`，
pytest 不会为了这 8 条去加载 Streamlit 那 21 个依赖
（实测：只跑 test_ui.py 是 0.03s，跑全套多 import streamlit 要 3s）。

════════════════════════════════════════════════════════════════════
五、怎么验
════════════════════════════════════════════════════════════════════
    cd research-agent
    ./.venv/Scripts/python.exe -m pytest -q tests/test_ui.py     # 8 passed

    后端没起时手动看一眼文案（应该是中文句子，不是英文堆栈）：
    ./.venv/Scripts/python.exe -c "
    from ui.client import post_research, ResearchAPIError
    try:
        post_research('测试')
    except ResearchAPIError as e:
        print(e.status, '|', e.detail)
    "
"""
import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# 实测一次 36.5s（手敲区）/ 65.9s（参考区），最坏估算 512s。
# 180 是「绝大多数能过、真卡住也别等十分钟」的折中。
DEFAULT_TIMEOUT = 180

# 状态码 → 给用户看的第一句。后端给的 detail 拼在后面。
_STATUS_HINT = {
    422: "输入有问题",
    502: "搜索服务不可用，稍后重试",
    504: "研究没在预算内跑完，可以调大 max_steps 再试",
    500: "服务端内部错误（已记日志）",
}

# pydantic 的 loc / type 都是英文，翻成中文；没命中的就把 msg 原样带出来。
_FIELD_NAMES = {
    "question": "研究问题",
    "max_steps": "最大步数",
    "max_tool_calls": "最大工具调用次数",
    "verify_links": "是否验证链接",
}

_ERROR_HINTS = {
    "missing": "不能为空",
    "string_too_short": "太短了（至少 2 个字）",
    "string_too_long": "太长了（最多 200 字）",
    "greater_than_equal": "不能小于 1",
    "less_than_equal": "不能大于 8",
    "bool_parsing": "必须是 true 或 false",
    "bool_type": "必须是 true 或 false",
}


# [手敲] 第 8 天块 1：错误类型
# --------------------------------------------------------------------
# 为什么自定义异常，而不是把 requests 的异常裸传出去：
#   · 界面只需要「一句话文案」，不需要认识 ConnectionError / ReadTimeout /
#     JSONDecodeError 这些实现细节
#   · 翻译收在这一层，界面里就不会出现 except requests.XXX ——
#     界面不该知道底层用的是哪个 HTTP 库
#   · status=None 是有意义的信号，见文件头第二节
class ResearchAPIError(Exception):
    """一次失败的调用。

    status 为 None 表示请求根本没发出去（连不上 / 超时）；
    有状态码表示服务端回了 4xx / 5xx。界面只用 detail 显示文案。
    """

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


# [手敲] 第 8 天块 2：把 pydantic 的校验错误翻成中文
# --------------------------------------------------------------------
# pydantic 给的 loc 是 ["body", "question"]，type 是 "string_too_short"，
# 直接甩给用户等于没翻译。这里查两张表：
#     _FIELD_NAMES  字段名 → 中文名
#     _ERROR_HINTS  错误类型 → 中文解释
# 没命中的退回原 msg（英文），至少信息不丢。
#
# 为什么不翻译 msg 本身：msg 是 pydantic 生成的英文长句，措辞随版本变，
# 硬匹配等于给自己埋一个「升级就失效」的坑。
#
# 界面上的效果：
#     Pydantic 原话 → "String should have at least 2 characters"
#     现在显示     → "输入有问题：研究问题太短了（至少 2 个字）"
def _explain_validation(items: list) -> str:
    """把 pydantic 的校验错误列表拼成一句中文。

    422 有两条来路：pydantic 自动校验（detail 是**列表**）和
    7b 的 ValueError handler（detail 是**字符串**）。这里只处理列表那种。
    """

    parts: list[str] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        loc = item.get("loc") or []
        field = str(loc[-1]) if loc else ""
        name = _FIELD_NAMES.get(field, field or "请求参数")

        hint = _ERROR_HINTS.get(str(item.get("type", "")))
        parts.append(f"{name}{hint}" if hint else f"{name}：{item.get('msg', '不合法')}")

    return "；".join(parts) or "参数校验失败"


def _detail_of(payload: object) -> str:
    """从响应体里取一句话。payload 为 None 表示响应根本不是 JSON。"""

    detail = payload.get("detail", "") if isinstance(payload, dict) else ""

    if isinstance(detail, str):
        return detail.strip() or "没有更多说明"
    if isinstance(detail, list):
        return _explain_validation(detail)

    return str(detail).strip() or "没有更多说明"


# [手敲] 第 8 天块 3：请求封装（三种出口）
# --------------------------------------------------------------------
#     返回 dict                       ← 200
#     抛 ResearchAPIError(status=码)  ← 4xx / 5xx
#     抛 ResearchAPIError(None)       ← 连不上 / 超时 / 回复不是 JSON
#
# 这个函数里唯一的陷阱是 except 的顺序，见文件头第三节。
# 一句话记法：**ConnectionError 在前，Timeout 在后**。
#
# timeout=180 的来历：实测一次 36.5s（手敲区）/ 65.9s（参考区），
# 按最坏情况估算约 512s。180 = 「绝大多数能过、真卡住也别等十分钟」。
def post_research(
    question: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """跑一次研究，返回接口的 JSON。

    三种出口：
      · 200 → 返回 dict
      · 4xx / 5xx → 抛 ResearchAPIError(status=状态码)
      · 连不上 / 超时 / 响应不是 JSON → 抛 ResearchAPIError(status=None 或 200)
    """

    url = f"{base_url.rstrip('/')}/research"

    try:
        response = requests.post(url, json={"question": question}, timeout=timeout)
    except requests.ConnectionError as error:
        # 必须写在 Timeout 前面，顺序反了文案就错。
        # 实测 MRO：ConnectTimeout → ConnectionError → Timeout → RequestException，
        # 即 ConnectTimeout 同时属于两者，谁在前谁接走。它发生在「TCP 还没连上」，
        # 真相是连不上，不该说「服务可能还在跑」。
        # ReadTimeout 不是 ConnectionError 子类，所以一定会落到下面那个分支。
        raise ResearchAPIError(
            None, f"连不上服务，先确认后端在跑：python -m app.api（{base_url}）"
        ) from error
    except requests.Timeout as error:
        raise ResearchAPIError(
            None, f"超过 {timeout} 秒没返回，服务可能还在跑，过一会儿看日志"
        ) from error

    if response.status_code == 200:
        try:
            return response.json()
        except ValueError as error:
            # 被代理或网关拦下时常见：200 但 body 是 HTML 登录页。
            raise ResearchAPIError(200, "服务返回的不是 JSON，可能被代理或网关拦截了") from error

    try:
        payload = response.json()
    except ValueError:
        payload = None

    hint = _STATUS_HINT.get(
        response.status_code, f"请求失败（HTTP {response.status_code}）"
    )
    raise ResearchAPIError(response.status_code, f"{hint}：{_detail_of(payload)}")
