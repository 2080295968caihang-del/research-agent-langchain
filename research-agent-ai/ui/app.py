"""第 8 天：Streamlit 界面。

启动（工作目录 = 项目根）：

    python -m streamlit run ui/app.py

════════════════════════════════════════════════════════════════════
一、为什么必须用 `python -m streamlit`
════════════════════════════════════════════════════════════════════
streamlit 执行脚本时只把**脚本所在目录**（`ui/`）插进 sys.path[0]
（源码：runtime/scriptrunner/exec_code.py:62）。
包 `ui` 自己反而不可见 → `from ui.client import ...` 直接
ModuleNotFoundError。

`python -m streamlit` 的 sys.path[0] 是当前目录（项目根），所以能 import。
块 0 那三行是兜底，兼容有人用 `.venv/Scripts/streamlit.exe run ui/app.py`。

════════════════════════════════════════════════════════════════════
二、脚本的结构（Streamlit 的模型：每次交互整份脚本重跑）
════════════════════════════════════════════════════════════════════
    块 1  页面骨架         永远执行
    块 2  输入 + 按钮       永远执行；按钮空问题禁用
    块 3  with st.status   只在 run=True 时执行（点过一次按钮后到此为止）
    块 4  报告             从 session_state 读，有就画
    块 5  来源             同上

关键点：`result` 必须存 `st.session_state`，不能放局部变量。
Streamlit 里点开一个 expander 也是一次「整份脚本重跑」，局部变量会清零，
用户展开一下来源就得再等 40 秒。存 dict 不存 Response 对象——
session_state 里的东西要跨重跑存活，纯 dict 最安全。

════════════════════════════════════════════════════════════════════
三、为什么错误处理这么写
════════════════════════════════════════════════════════════════════
块 3 里只有一个 `except ResearchAPIError`，因为状态码到中文文案的
翻译全在 `ui/client.py` 里做完了。界面这一层不做任何判断——
不加 if status == 502 这种分支，文案改一处就够了。

失败时顺手 `st.session_state.pop("result", None)`：
否则「上一轮的成功报告 + 这一轮的报错」同屏出现，会被误读成本次成功。

════════════════════════════════════════════════════════════════════
四、怎么验（三条命令）
════════════════════════════════════════════════════════════════════
    # 1. 先起后端（另一个窗口）
    cd research-agent && ./.venv/Scripts/python.exe -m app.api

    # 2. 起界面
    cd research-agent && ./.venv/Scripts/python.exe -m streamlit run ui/app.py
    # 浏览器 http://localhost:8501

    # 3. 不开浏览器也能证明活着
    curl -s http://127.0.0.1:8501/_stcore/health      # ok

验收：真提一个问题 → 报告 / 来源 / 耗时都对；停掉后端再点 →
看到「连不上服务」，不是 traceback。
"""
# [手敲] 第 8 天块 0：路径兜底（3 行）
# --------------------------------------------------------------------
# 见文件头第一节。两个解法叠着用：
#   1. 启动命令用 `python -m streamlit run ui/app.py`（sys.path[0] = cwd）
#   2. 这三行兜底，兼容 `.venv/Scripts/streamlit.exe run ui/app.py`
# 这不是坏味道，是 Streamlit 多文件应用的标准做法。
# 路径兜底：用 `.venv/Scripts/streamlit.exe run ui/app.py` 这种方式起时，
# 上面那条理由不成立，所以自己把项目根塞进去。Streamlit 多文件应用的标准做法。
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from ui.client import DEFAULT_TIMEOUT, ResearchAPIError, post_research  # noqa: E402

# [手敲] 第 8 天块 1：页面骨架
# --------------------------------------------------------------------
# set_page_config 必须是第一个 st 调用，否则 Streamlit 报
# "set_page_config must be the first Streamlit command"。
# layout="wide" 是因为报告正文比较长，窄栏会一直横向滚动。
st.set_page_config(page_title="Research Agent", page_icon="🔍", layout="wide")
st.title("Research Agent")
st.caption("输入一个问题，等 30～70 秒，得到带来源引用的研究报告。")

# [手敲] 第 8 天块 2：输入 + 按钮
# --------------------------------------------------------------------
# max_chars=200 与后端 Field(max_length=200) 对齐：
# 让用户当场看到上限，而不是填完 300 字、等一次往返再吃 422。
#
# disabled=not question.strip()：空输入时按钮点不动，省掉一次无意义的请求。
# 但注意它只管「有没有字」，管不了「至少 2 个字」——下界还是靠后端 422，
# 那种情况由 client 翻成「研究问题太短了（至少 2 个字）」。
# max_chars 与后端的 Field(max_length=200) 对齐：让用户当场看到上限，
# 而不是填完 300 字、等一次往返再吃 422。
question = st.text_area(
    "研究问题",
    placeholder="例：2026 年诺贝尔物理学奖颁给了谁，主要贡献是什么",
    height=100,
    max_chars=200,
)
run = st.button("开始研究", type="primary", disabled=not question.strip())

# [手敲] 第 8 天块 3：执行状态
# --------------------------------------------------------------------
# st.status(label, *, expanded=False, state="running") 实测签名（1.66.1），
# state 只有三值："running" / "complete" / "error"。容器内照常 st.write。
#
# 为什么必须给状态反馈：一次真实研究 36～96 秒。不给反馈，
# 用户会以为界面卡死，然后连点三次按钮，等于烧三倍的钱。
#
# 只有一个 except，因为状态码 → 中文文案的映射全在 ui/client.py 里。
# 失败时清掉上一轮结果，避免「旧报告 + 新报错」被误读成本次成功。
if run:
    with st.status("正在研究…", expanded=True) as status:
        st.write(f"已提交，等待服务端返回（通常 30～70 秒，最多等 {DEFAULT_TIMEOUT} 秒）")

        try:
            result = post_research(question.strip())
        except ResearchAPIError as error:
            # 文案在 client 里已经翻译好了，这里只管显示；不出现 traceback。
            # 顺手把上一轮的结果清掉，否则「旧报告 + 新报错」会被误读成本次成功。
            st.session_state.pop("result", None)
            status.update(label="失败", state="error", expanded=True)
            st.error(error.detail)
        else:
            st.session_state["result"] = result
            status.update(
                label=f"完成，用时 {result['elapsed_seconds']} 秒",
                state="complete",
                expanded=False,
            )

# [手敲] 第 8 天块 4：报告
# --------------------------------------------------------------------
# 结果存 session_state 而不是局部变量：Streamlit 每次交互（包括点开
# expander）都会整份脚本重跑，不存的话展开一下结果就没了。
#
# st.markdown 直接吃后端渲染好的 markdown：
# [n] 在 report_to_markdown 里已经变成 [n](url)，前端不再重拼，
# 不然「引用编号 ↔ 来源」这套规则就要维护两份。
# 结果存 session_state 而不是局部变量：Streamlit 每次交互（包括点开
# expander）都会整份脚本重跑，不存的话展开一下结果就没了，得再等 40 秒。
saved = st.session_state.get("result")

if saved:
    report = saved["report"]

    st.divider()
    st.subheader(report["title"])
    st.markdown(saved["markdown"])          # [n] 后端已变成可点链接，前端不重拼
    st.caption(
        f"{saved['steps']} 轮模型调用 · {saved['tool_calls']} 次工具 · "
        f"{saved['elapsed_seconds']} 秒"
    )

# [手敲] 第 8 天块 5：来源
# --------------------------------------------------------------------
# 单独放一个 expander，默认收起：报告正文已经有 [n] 可点，
# 来源列表是「对账用」的，占着版面反而碍事。
# 序号用 source["index"] 而不是 enumerate 的序号——
# 后端可能只回填了 [1][3]，编号必须与正文一致，不能用位置重编。
if saved:
    sources = saved["report"].get("sources") or []

    if sources:
        with st.expander(f"来源（{len(sources)} 条）", expanded=False):
            for source in sources:
                st.markdown(f"{source['index']}. [{source['title']}]({source['url']})")
    else:
        st.info("这次没有可用的来源。")
