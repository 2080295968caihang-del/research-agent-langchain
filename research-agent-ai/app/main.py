"""第 2 天的命令行入口。"""

from dotenv import load_dotenv

from app.report import generate_report


def main() -> None:
    """循环读取研究问题，并打印结构化报告 JSON。"""

    print("Research Agent - Day 2")
    print("输入 exit 或 quit 可以退出。")

    while True:
        question = input("\n请输入研究问题：").strip()
        if question.lower() in {"exit", "quit"}:
            print("已退出。")
            return

        try:
            report = generate_report(question)
        except ValueError as error:
            # 空问题和模型输出格式错误都会在这里被转换成可读提示。
            print(f"报告生成失败：{error}")
        except Exception as error:
            # 不打印环境变量，避免 API Key 出现在终端或日志中。
            print(f"模型调用失败：{error}")
        else:
            print("\n结构化报告：")
            print(report.model_dump_json(indent=2, ensure_ascii=False))


if __name__ == "__main__":
    # 从当前项目的 .env 文件加载 API 配置。
    load_dotenv()
    main()
