"""第 6 天的命令行入口：Agent 检索 -> 带引用的结构化报告 -> Markdown 输出。

第 2 天这里是「问题 -> 模型 -> JSON」；
第 6 天变成「问题 -> Agent 检索 -> 资料编号 -> 报告 -> 引用回填」。
"""

from dotenv import load_dotenv

from app.agent import AgentLoopError, run_agent
from app.report import generate_report, report_to_markdown


def main() -> None:
    """循环读取研究问题，跑一轮完整的研究流程。"""

    print("Research Agent - Day 6")
    print("输入 exit 或 quit 可以退出。")

    while True:
        question = input("\n请输入研究问题：").strip()
        if question.lower() in {"exit", "quit"}:
            print("已退出。")
            return

        # ---------- 第一步：Agent 带着搜索工具把资料找回来 ----------
        try:
            run = run_agent(question)
        except ValueError as error:
            print(f"输入有误：{error}")
            continue
        except AgentLoopError as error:
            # 模型一直在要工具没收敛，属于我们自己的边界问题，不是网络问题。
            print(f"Agent 未能收敛：{error}")
            continue
        except Exception as error:
            # 不打印环境变量，避免 API Key 出现在终端或日志中。
            print(f"检索失败：{error}")
            continue

        # ---------- 第二步：把真实来源交给报告层，重新编号并回填 ----------
        try:
            report = generate_report(question, sources=run.sources, verify_links=True)
        except ValueError as error:
            print(f"报告生成失败：{error}")
            continue
        except Exception as error:
            print(f"报告生成失败（调用异常）：{error}")
            continue

        print("\n" + report_to_markdown(report))
        print(
            f"\n统计：{run.steps} 步 / {run.tool_call_count} 次工具调用 / "
            f"{len(run.sources)} 条检索结果 / {len(report.sources)} 条被引用"
        )


if __name__ == "__main__":
    # 从当前项目的 .env 文件加载 API 配置。
    load_dotenv()
    main()
