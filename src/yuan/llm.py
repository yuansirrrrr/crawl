from langchain_openai import ChatOpenAI

from yuan.config import settings


def get_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model="deepseek-chat",
        api_key=settings.DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1",
        temperature=0.3,
    )
