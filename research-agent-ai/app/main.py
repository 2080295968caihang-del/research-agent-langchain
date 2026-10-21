"""第 1 天的命令行入口。"""

from dotenv import load_dotenv

from app.agent import ask_model


def main() -> None:
    """循环读取问题并显示模型回复。"""

    print("Research Agent - Day 1")
    print("输入 exit 或 quit 可以退出。")

    while True:
        question = input("\n请输入问题：").strip()
        if question.lower() in {"exit", "quit"}:
            print("已退出。")
            return

        try:
            answer = ask_model(question)
        except ValueError as error:
            print(f"输入错误：{error}")
        except Exception as error:
            # 不打印环境变量，避免 API Key 出现在终端或日志中。
            print(f"调用失败：{error}")
        else:
            print("\n模型回复：")
            print(answer)


if __name__ == "__main__":
    load_dotenv()
    main()
