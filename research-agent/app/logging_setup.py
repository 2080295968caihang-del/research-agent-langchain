"""
第 7b 天：日志初始化。

"""


import logging
import logging.handlers     # 必须单独写这一行
import os
import re

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

DEFAULT_LOG_LEVEL = logging.INFO

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

SECRET_PATTERN = re.compile(r"(?:sk-|KGAT_)[A-Za-z0-9_\-]{4,}")

KEEP_PREFIX_CHARS = 6

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