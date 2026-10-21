"""应用入口。

第 1 天请在这里照着参考目录的 app/main.py 手动输入程序入口。
"""
from dotenv import load_dotenv
from app.agent import ask_model

def main() -> None:
    """程序入口。"""
    print("欢迎使用 Reseach Agent")
    print("输入exit或者quit退出程序")

    while True:
        question = input("\n请输入问题：").strip()
        if question.lower() in ["exit", "quit"]:
           print("程序已退出")
           return

        try:
            answer = ask_model(question)
        except ValueError as error:
            print(f"输入错误:{error}")
        except Exception as error:
            print(f"调用失败:{error}")
        else:
            print("\n模型回复：")
            print(answer)


if __name__ == "__main__":
    load_dotenv()
    main()