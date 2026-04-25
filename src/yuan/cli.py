import asyncio
import logging

import typer

from yuan.config import settings

app = typer.Typer(name="yuan", help="与 AI 助手对话，搜索抖音和微信公众号内容")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@app.command()
def chat():
    """与 AI 助手对话，让它自动搜索抖音和微信内容。"""
    if not settings.DEEPSEEK_API_KEY:
        typer.secho("错误: 未设置 DEEPSEEK_API_KEY 环境变量", fg=typer.colors.RED)
        typer.echo("请在 .env 文件中设置: DEEPSEEK_API_KEY=sk-...")
        raise typer.Exit(1)

    from yuan.agent import build_agent
    from langchain_core.messages import HumanMessage

    agent = build_agent()
    history = []

    typer.secho("Yuan 助手已启动，输入话题开始搜索（输入 exit 退出）\n", fg=typer.colors.GREEN, bold=True)

    while True:
        try:
            user_input = typer.prompt("你")
        except (EOFError, KeyboardInterrupt):
            break

        if user_input.strip().lower() in ("exit", "quit", "退出"):
            break

        history.append(HumanMessage(content=user_input))

        try:
            result = asyncio.run(agent.ainvoke({"messages": history}))
            messages = result["messages"]
            history = messages
            reply = messages[-1].content
            typer.secho(f"\n助手: {reply}\n", fg=typer.colors.CYAN)
        except Exception as e:
            typer.secho(f"错误: {e}", fg=typer.colors.RED)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload", help="开发模式：文件变更自动重载"),
):
    """启动 FastAPI 服务器。"""
    import uvicorn

    typer.secho(f"启动服务器: http://{host}:{port}", fg=typer.colors.GREEN)
    uvicorn.run("yuan.main:app", host=host, port=port, reload=reload, log_level="info")


if __name__ == "__main__":
    app()

