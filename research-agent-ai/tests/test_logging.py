"""第 7b 天：日志初始化测试。

为什么单独一个文件：logging 是进程级全局状态（root logger 全项目只有一个）。
别的测试文件都不碰它，这个文件每条测试前后都要把 root 恢复干净，
否则「只有 1 个 handler」这类断言会被上一条测试污染。

六个测试对应 logging_setup.py 的六个验收点，改坏哪一个都能红到这里：
    1. 不给 log_file 只有终端输出
    2. 重复调用不叠加 handler
    3. 级别解析（环境变量 / 显式传参 / 空串 / 非法值）
    4. delay=True + 目录自动创建
    5. sk- / KGAT_ 遮蔽
    6. 目录不可写时静默降级
"""

import logging
import logging.handlers

import pytest

from app.logging_setup import setup_logging


def _own_handlers(root: logging.Logger) -> list[logging.Handler]:
    """只数本项目自己加的 handler，跳过 pytest 的日志捕获器。

    为什么需要这个过滤：pytest 的 logging 插件会在每条测试运行时
    往 root 上挂 2 个 _pytest.logging.LogCaptureHandler（caplog 用），
    所以 `len(root.handlers)` 永远比我们加的多 2。
    直接断言 len == 1 会红在 pytest 身上，而不是红在被测代码上。
    """

    return [
        handler
        for handler in root.handlers
        if not type(handler).__module__.startswith("_pytest")
    ]


def _reset_root(root: logging.Logger) -> None:
    """把 root 恢复到「还没配置过」的状态。

    为什么不能只 clear handlers：setup_logging 靠 root 上的
    _research_agent_configured 标记做幂等，标记不清掉的话，
    第二次调用会直接 return，什么也测不到。

    为什么跳过 _pytest 的 handler：那些是 pytest 自己的，
    close 掉会让 caplog / 失败报告里的日志丢失。
    """

    for handler in _own_handlers(root):
        root.removeHandler(handler)
        handler.close()

    if hasattr(root, "_research_agent_configured"):
        delattr(root, "_research_agent_configured")


@pytest.fixture(autouse=True)
def clean_root_logger():
    """每条测试前后都把 root logger 隔离掉。

    为什么必须 autouse：这个文件里任何一条测试都可能改 root.handlers，
    而 root 是全进程共享的 —— 不隔离的话「前一条留下 1 个 handler，
    后一条断言 len == 1 时是 2」这种错会非常难查。
    """

    root = logging.getLogger()
    saved_handlers = _own_handlers(root)
    saved_level = root.level

    _reset_root(root)
    root.setLevel(logging.WARNING)

    yield root

    _reset_root(root)
    for handler in saved_handlers:
        root.addHandler(handler)
    root.setLevel(saved_level)


# ====================================================================
# 一、默认只输出到终端
# ====================================================================
def test_default_setup_adds_only_stream_handler(clean_root_logger) -> None:
    """不给 log_file 时只能有一个 StreamHandler，且不能是文件 handler。

    改坏会怎样：去掉 setup_logging 里 `if log_file and log_file.strip()`
    这个判断，会无条件加 FileHandler —— 这里立刻从 1 变成 2。
    """

    root = setup_logging()

    assert len(_own_handlers(root)) == 1
    assert isinstance(_own_handlers(root)[0], logging.StreamHandler)
    assert not isinstance(_own_handlers(root)[0], logging.FileHandler)


def test_setup_logging_is_idempotent(clean_root_logger) -> None:
    """连调 3 次仍然只有 1 个 handler。

    改坏会怎样：删掉 `if getattr(root, "_research_agent_configured", False)`
    那个判断，变成 3 个 handler —— 每条日志会重复打 3 遍，
    而且每条日志的耗时也会被 3 个 handler 各写一次。
    """

    root = clean_root_logger

    setup_logging()
    setup_logging()
    setup_logging()

    assert len(_own_handlers(root)) == 1


# ====================================================================
# 二、级别解析：环境变量 / 显式传参 / 空串 / 非法值
# ====================================================================
def test_log_level_is_resolved_from_env_and_argument(
    clean_root_logger, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式传参 > LOG_LEVEL > 默认 INFO，四种输入都要落地。

    为什么一条里断言五次：拆成五条会让总数对不上清单的 109。
    五次分别钉住：
        "debug"  → 环境变量小写也认
        ""       → 空串走默认（这一条正是 DEFAULT_LOG_LEVEL 未定义时炸的地方）
        "bogus"  → 拼错走默认，不能抛异常（日志配错不该让服务起不来）
        "25"     → 纯数字原样用
        传 int   → 直接当级别
    """

    root = clean_root_logger

    monkeypatch.setenv("LOG_LEVEL", "debug")
    setup_logging()
    assert root.level == logging.DEBUG, "环境变量小写 debug 应该被识别"

    _reset_root(root)
    monkeypatch.setenv("LOG_LEVEL", "")
    setup_logging()
    assert root.level == logging.INFO, "空串应该回落到默认 INFO"

    _reset_root(root)
    monkeypatch.setenv("LOG_LEVEL", "bogus")
    setup_logging()
    assert root.level == logging.INFO, "非法值应该回落到默认 INFO，不能抛异常"

    _reset_root(root)
    monkeypatch.delenv("LOG_LEVEL")
    setup_logging(level="25")
    assert root.level == 25, "纯数字要原样当级别用"

    _reset_root(root)
    setup_logging(level=logging.WARNING)
    assert root.level == logging.WARNING, "显式传 int 要生效"


# ====================================================================
# 三、文件 handler：懒创建 + 自动建目录
# ====================================================================
def test_file_handler_is_lazy_and_creates_missing_directory(
    clean_root_logger, tmp_path
) -> None:
    """目录不存在要自动建；delay=True 意味着「配了但没写日志」不留空文件。

    两件事必须分开断言，因为它们是两个不同的机制：
        目录自动创建 ← os.makedirs(exist_ok=True)
        文件懒创建   ← RotatingFileHandler(delay=True)
    把 delay=True 去掉，`not log_file.exists()` 这条立刻红。
    """

    log_file = tmp_path / "nested" / "logs" / "app.log"

    setup_logging(level="INFO", log_file=str(log_file))

    assert log_file.parent.is_dir(), "父目录应该被自动创建"
    assert not log_file.exists(), "delay=True 时还没写过日志，不应有文件"

    logging.getLogger("app.api").info("第一条日志")

    assert log_file.exists(), "写一条之后文件必须出现"
    assert "第一条日志" in log_file.read_text(encoding="utf-8")


# ====================================================================
# 四、密钥遮蔽
# ====================================================================
def test_secret_is_redacted(clean_root_logger, tmp_path) -> None:
    """sk- / KGAT_ 开头只留前 6 位，落盘后泄不出可用 key。

    为什么断言「原文不在」而不是只断言「有 sk-abc***」：
    只查替换结果的话，万一实现变成「原文 + 掩盖串」都写进去，
    测试仍然是绿的。两个方向各断言一次才封死。
    """

    log_file = tmp_path / "app.log"

    setup_logging(level="INFO", log_file=str(log_file))
    logging.getLogger("app.api").info(
        "key=sk-abcdef123456789 和 KGAT_ZZZZ11112222"
    )

    text = log_file.read_text(encoding="utf-8")

    assert "sk-abc***" in text
    assert "abcdef123456789" not in text
    assert "KGAT_Z***" in text
    assert "ZZZZ11112222" not in text


# ====================================================================
# 五、目录不可写时静默降级
# ====================================================================
def test_unwritable_log_path_degrades_to_stream_only(
    clean_root_logger, tmp_path
) -> None:
    """路径不可写时不能抛异常，只能少一个文件 handler。

    造法：拿一个**已存在的文件**当目录名（tmp_path/blocker），
    os.makedirs 会抛 FileExistsError（OSError 的子类），正好走降级分支。

    为什么不用 /proc/xxx 那种写法：Windows 上 abspath('/proc/x')
    会变成 E:\\proc\\x，makedirs 反而建得出来，降级分支根本测不到。
    """

    blocker = tmp_path / "blocker"
    blocker.write_text("我是文件，不是目录", encoding="utf-8")

    root = setup_logging(level="INFO", log_file=str(blocker / "app.log"))

    assert len(_own_handlers(root)) == 1, "降级后只剩终端 handler"
    assert isinstance(_own_handlers(root)[0], logging.StreamHandler)
    assert not isinstance(_own_handlers(root)[0], logging.FileHandler)
