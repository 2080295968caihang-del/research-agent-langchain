"""界面与接口之间的一层：发请求 + 把错误翻译成人话。

为什么不直接 `from app.agent import run_agent`：
那样界面会自己跑一遍业务逻辑，接口层的状态码、耗时头、错误文案
一次都没被验证过。走 HTTP 一次，等于顺手做了接口的二次验收。

这一层不 import streamlit：
Streamlit 的脚本模型（每次交互整份脚本重跑）几乎没法单测，
但「请求 + 错误翻译」是纯 Python，能测，也必须测。
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


class ResearchAPIError(Exception):
    """一次失败的调用。

    status 为 None 表示请求根本没发出去（连不上 / 超时）；
    有状态码表示服务端回了 4xx / 5xx。界面只用 detail 显示文案。
    """

    def __init__(self, status: int | None, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(detail)


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
