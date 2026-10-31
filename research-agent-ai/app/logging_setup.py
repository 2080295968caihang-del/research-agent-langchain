"""第 7b 天：日志初始化。

════════════════════════════════════════════════════════════════════
一、这份文件在做什么
════════════════════════════════════════════════════════════════════
它只做一件事：给 root logger 装上「终端输出 + 可选的文件轮转输出」，
并统一日志行格式。业务代码里只管 `logging.getLogger("app.api").info(...)`，
不需要知道日志最终落到哪里。

调一次就够：

    from app.logging_setup import setup_logging
    setup_logging(log_file=os.getenv("LOG_FILE"))

不给 log_file 就只往终端打，磁盘上不生成任何文件（默认行为，安全）。

════════════════════════════════════════════════════════════════════
二、逻辑链
════════════════════════════════════════════════════════════════════
    setup_logging(level, log_file)
         ↓ 已经有标记符？→ 直接返回（幂等，重复调不会叠加 handler）
    _resolve_level(level or os.getenv("LOG_LEVEL"))
         ↓ "debug" / "INFO" / "25" / 空 → 一律变成 int
    root.setLevel(级别)
         ↓
    加 StreamHandler（stderr），装 _RedactFilter
         ↓ log_file 非空？
    加 RotatingFileHandler（5MB × 3 个备份，delay=True）
         ↓ 目录不可写？
    静默降级为「只有终端」，不抛异常

════════════════════════════════════════════════════════════════════
三、四个容易写错的点
════════════════════════════════════════════════════════════════════
1. `logging.handlers` 是子模块，光 `import logging` 拿不到 RotatingFileHandler。
   `logging.handlers.RotatingFileHandler` 会报 AttributeError，必须单独 import。

2. `delay=True` 的构造函数**不碰磁盘**。所以「目录不存在」这个错误
   在构造时根本不会发生，光包 try/except 是白写——必须**先** os.makedirs。
   反过来说，delay=True 的好处是：import 了日志模块但没写日志，就不产生空文件。

3. 幂等靠 root 上的一个私有属性做标记。不用模块级 bool：
   测试里 `logging.getLogger().handlers.clear()` 之后，模块级 bool 还在，
   会导致「明明清空了却再也不装 handler」。

4. 遮敏感信息用 Filter 挂在 handler 上，而不是在每条日志里手动处理。
   挂在 handler 上意味着「不管谁写日志，出门前都会被检查一遍」。
   代价是 record 被就地改写（msg/args 被压成一条完整字符串），
   所以 Filter 必须是最后一个动的环节。

════════════════════════════════════════════════════════════════════
四、和「结构化日志」的区别（为什么不做）
════════════════════════════════════════════════════════════════════
结构化日志（JSON 一行一条）适合被 ELK / Loki 采集。
这个项目是单机演示，人肉 tail 日志，可读性优先，所以用最朴素的一行式。
真要接采集，把 StreamHandler 换掉即可，业务代码不用改。
"""

import logging
import logging.handlers
import os
import re

# 日志行格式：时间 级别(左对齐 8 位) 模块名 | 正文
# levelname 左对齐是为了让不同级别的日志正文起点对齐，tail 的时候一眼能扫。
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 不配 LOG_LEVEL 时的默认级别。
# 为什么默认 INFO 而不是 WARNING：这个项目的日志量本来就小，
# INFO 里放的是「每次请求一行」，正是排障时最需要的那一行。
DEFAULT_LOG_LEVEL = logging.INFO

# 单文件 5MB，留 3 个历史文件：最坏占 20MB，个人项目够用。
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# 需要遮掉的密钥形态：博查/OpenAI 的 sk-，以及 Kaggle 的 KGAT_。
# 只匹配「前缀 + 至少 4 位」，避免把正文里偶发的 "sk-" 误伤。
SECRET_PATTERN = re.compile(r"(?:sk-|KGAT_)[A-Za-z0-9_\-]{4,}")

# 遮掉时保留前几位，方便判断「是哪一把 key」，又不泄露完整值。
KEEP_PREFIX_CHARS = 6


# ====================================================================
# [手敲] 第 7b 天块 1：级别解析
# --------------------------------------------------------------------
# 逻辑链：任意输入 → 统一成 logging 认识的 int 级别
#
# 为什么要兼容三种写法：
#   · "info" / "INFO"  —— .env 里人手写的，大小写经常不统一
#   · "25"            —— logging 允许自定义级别，有人会直接写数字
#   · 空 / 不写        —— 用默认值，不要让程序因为少一个环境变量启动失败
#
# 为什么未知值回落默认而不是抛异常：
#   日志级别配错不该让服务起不来。写错了就按默认跑，行为可预测。
# ====================================================================
def _resolve_level(raw_value: str | None) -> int:
    """把 "info" / "DEBUG" / "25" 这类写法统一成 logging 的整数级别。"""

    if raw_value is None or not str(raw_value).strip():
        return DEFAULT_LOG_LEVEL

    text = str(raw_value).strip().upper()

    # 纯数字：logging 允许 0-50 之外的数字，原样交给它。
    if text.isdigit():
        return int(text)

    # 名字：logging.INFO 等常量就是 int，getattr 拿不到说明名字拼错了。
    resolved = getattr(logging, text, None)
    if isinstance(resolved, int):
        return resolved

    return DEFAULT_LOG_LEVEL


# ====================================================================
# [手敲] 第 7b 天块 2：敏感串遮蔽
# --------------------------------------------------------------------
# 逻辑链：record 进 handler → Filter 改写 → formatter 拿改写后的内容
#
# 为什么改 record.msg 和 record.args 而不是改 record.msgstr：
#   Filter 在 format() 之前执行，此时还没有最终字符串。
#   record.getMessage() 会做一次 "%s" 插值，所以改完要把 args 清空，
#   否则 formatter 会拿「已插值的字符串」再和 args 插值一次，直接报错。
# ====================================================================
class _RedactFilter(logging.Filter):
    """把日志文本里形如 sk-xxxx / KGAT_xxxx 的串只留前 6 位。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # 永远返回 True：这个 Filter 只改内容，不拦日志。
        message = record.getMessage()
        redacted = SECRET_PATTERN.sub(self._mask, message)

        if redacted != message:
            record.msg = redacted
            record.args = ()

        return True

    @staticmethod
    def _mask(match: re.Match[str]) -> str:
        secret = match.group(0)
        return secret[:KEEP_PREFIX_CHARS] + "***"


# ====================================================================
# [手敲] 第 7b 天块 3：文件 handler（可选，失败就降级）
# --------------------------------------------------------------------
# 逻辑链：目录不存在 → 建目录 → 建 handler → 失败则返回 None
#
# 顺序不能颠倒（这次真踩过）：
#   RotatingFileHandler(..., delay=True) 的构造函数**不打开文件**，
#   所以「目录不存在」在这里不会报错，except OSError 永远不触发。
#   必须在构造**之前** os.makedirs，否则第一行日志写下去时才会炸，
#   而那时已经在请求处理流程里了，表现为「请求 500，日志里什么都没有」。
#
# 为什么失败只降级不抛：
#   日志是辅助设施，不能因为它坏了就让服务起不来。
#   降级后只剩终端输出，功能不受影响，但要在终端看得见这条告警。
# ====================================================================
def _build_file_handler(
    log_file: str,
    formatter: logging.Formatter,
    redact: logging.Filter,
) -> logging.Handler | None:
    """建一个带轮转的文件 handler；目录不可写时返回 None。"""

    try:
        directory = os.path.dirname(os.path.abspath(log_file))
        if directory:
            # exist_ok=True：目录已存在不报错；多进程同时启动也不打架。
            os.makedirs(directory, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
            # delay=True：构造时不建文件，第一条日志写下去才建。
            # 好处是「配了 LOG_FILE 但这次没打日志」不会留下空文件。
            delay=True,
        )
    except OSError as error:
        logging.getLogger(__name__).warning(
            "日志文件不可用，降级为只输出到终端：%s（%s）", log_file, error
        )
        return None

    handler.setFormatter(formatter)
    handler.addFilter(redact)
    return handler


# ====================================================================
# [手敲] 第 7b 天块 4：对外唯一入口
# --------------------------------------------------------------------
# 逻辑链：检查标记 → 定级别 → 装 StreamHandler → 装（可选）FileHandler → 打标记
#
# 幂等为什么重要：
#   测试里每个用例都会 import 一次 app，uvicorn 的 reload 也会重复执行模块。
#   不幂等的话 handler 会越叠越多，同一条日志打印 N 遍。
#
# 为什么直接在 root 上装，而不是给 "app" 包单独建 logger：
#   root 是兜底，第三方库（httpx / openai）的日志也会一起被格式化，
#   排查「模型调用超时」时它们的信息很有用。缺点是要靠级别过滤噪音，
#   所以默认 INFO 而不是 DEBUG。
# ====================================================================
def setup_logging(
    level: str | int | None = None,
    log_file: str | None = None,
) -> logging.Logger:
    """配置 root logger；重复调用只生效一次。

    参数：
        level     级别，可传 "info" / logging.DEBUG / 25；默认读 LOG_LEVEL，再默认 INFO
        log_file  日志文件路径；不传或空串则只输出到终端

    返回：root logger，方便调用方直接 `setup_logging().info(...)`。
    """

    root = logging.getLogger()

    # 幂等标记挂在 root 上而不是模块级变量：测试清空 handlers 时可以连标记一起清。
    if getattr(root, "_research_agent_configured", False):
        return root

    resolved_level = _resolve_level(level if level is not None else os.getenv("LOG_LEVEL"))
    root.setLevel(resolved_level)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    redact = _RedactFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(redact)
    root.addHandler(stream_handler)

    if log_file and log_file.strip():
        file_handler = _build_file_handler(log_file.strip(), formatter, redact)
        if file_handler is not None:
            root.addHandler(file_handler)

    # 标记放在最后：中途抛异常时不留「已配置」的假标记。
    setattr(root, "_research_agent_configured", True)
    return root
