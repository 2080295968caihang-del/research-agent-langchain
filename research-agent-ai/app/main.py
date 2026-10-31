"""第 6 天的命令行入口：Agent 检索 -> 带引用的结构化报告 -> Markdown 输出。

第 2 天这里是「问题 -> 模型 -> JSON」；
第 6 天变成「问题 -> Agent 检索 -> 资料编号 -> 报告 -> 引用回填」。

这两段之间没有共享状态：第一段只交出 run.sources（真实检索结果），
第二段拿它去编号、回填、校验。想看懂整条链路，从这个文件读最快。

════════════════════════════════════════════════════════════════════
给只学过基础语法的你：本文件出现的 4 个新语法
════════════════════════════════════════════════════════════════════
1. while True + return     死循环靠 return 退出（在函数里 return 和 break 都能跳出去，
                           但 return 的位置更适合在退出前做事）。
2. x in {a, b}             集合成员判断是 O(1) 哈希查找，列表是 O(n) 逐个比较。
3. except 的顺序           子类必须写在父类前面。AgentLoopError 是 RuntimeError 的子类，
                           Exception 是所有异常的基类，写反了子类分支永远走不到。
4. if __name__ == "__main__"
                           模块守卫：直接运行该文件时 __name__ 是 "__main__"，
                           被 import 时是模块名。有了它，测试可以 import 这个文件
                           而不会启动交互循环。
"""

from dotenv import load_dotenv

from app.agent import AgentLoopError, run_agent
from app.report import generate_report, report_to_markdown


def main() -> None:
    """循环读取研究问题，跑一轮完整的研究流程。

    语法（6 个点，这个文件是读整条链路最省力的入口）：
    · while True + return：退出靠 return 而不是 break。这里在函数里，两者等价；
      但如果后面还要写「退出前落日志」，return 前的位置比 break 之后更清晰。
    · question.lower() in {"exit", "quit"} 用集合而不是列表：
      集合成员判断是 O(1) 哈希查找，列表是 O(n) 逐个比较。元素少时无所谓，
      但「用集合表达一组可选值」是更准确的写法。
    · 三个 except 的顺序不能调换：AgentLoopError 是 RuntimeError 的子类，
      Exception 是所有异常的基类。子类必须写在父类前面，否则永远轮不到子类分支。
    · continue 表示「本轮放弃、回到 while 顶部重新读问题」，不是退出程序。
    · if __name__ == "__main__": 是模块守卫：直接 python -m app.main 运行时
      该模块的 __name__ 是 "__main__"，被 import 时是模块名，main() 不会自动跑。
      这一条让测试可以 import 这个文件而不启动交互循环。
    · load_dotenv() 放在守卫里面而不是模块顶层：import 时不读 .env，
      让「只是导入一下」的场景不依赖环境文件存在。
    """

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
    import os

    from app.logging_setup import setup_logging

    # 从当前项目的 .env 文件加载 API 配置。
    load_dotenv()
    # 日志初始化放在 load_dotenv 之后：它要读 LOG_LEVEL / LOG_FILE。
    # 命令行入口也接上日志，是为了让「一次研究跑到哪一步」能落盘，
    # 否则只能靠 print，重定向都做不到时间戳。
    setup_logging(log_file=os.getenv("LOG_FILE"))
    main()
