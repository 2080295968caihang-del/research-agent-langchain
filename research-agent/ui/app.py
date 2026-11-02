"""day8：Streamlit 界面。

启动（工作目录 = 项目根）：
    python -m streamlit run ui/app.py

为什么必须用 `python -m`：streamlit 执行脚本时只把**脚本所在目录**（`ui/`）
插进 `sys.path[0]`，包 `ui` 本身不可见，`from ui.client import ...` 会
ModuleNotFoundError。`python -m streamlit` 的 `sys.path[0]` 是当前目录，稳。
"""

# ------------------------------------------------------------------ 块 0
# 路径兜底：用 `.venv/Scripts/streamlit.exe run ui/app.py` 这种方式起时，
# 上面那条理由不成立，所以自己把项目根塞进去。Streamlit 多文件应用的标准做法。
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

from ui.client import DEFAULT_TIMEOUT, ResearchAPIError, post_research  # noqa: E402

# ------------------------------------------------------------------ 块 1
st.set_page_config(page_title="Research Agent", page_icon="🔍", layout="wide")
st.title("Research Agent")
st.caption("输入一个问题，等 30～70 秒，得到带来源引用的研究报告。")

# ------------------------------------------------------------------ 块 2
# max_chars 与后端的 Field(max_length=200) 对齐：让用户当场看到上限，
# 而不是填完 300 字、等一次往返再吃 422。
question = st.text_area(
    "研究问题",
    placeholder="例：2026 年诺贝尔物理学奖颁给了谁，主要贡献是什么",
    height=100,
    max_chars=200,
)
run = st.button("开始研究", type="primary", disabled=not question.strip())

# ------------------------------------------------------------------ 块 3
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

# ------------------------------------------------------------------ 块 4
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

# ------------------------------------------------------------------ 块 5
if saved:
    sources = saved["report"].get("sources") or []

    if sources:
        with st.expander(f"来源（{len(sources)} 条）", expanded=False):
            for source in sources:
                st.markdown(f"{source['index']}. [{source['title']}]({source['url']})")
    else:
        st.info("这次没有可用的来源。")
