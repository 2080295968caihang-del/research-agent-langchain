"""界面层。

刻意与 `app/` 分开：`app/` 是后端包（能被 `python -m app.api` 导入），
界面一旦混进去，`pytest` 收集测试时会顺带 import streamlit。
"""
