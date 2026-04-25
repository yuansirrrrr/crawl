from fastapi import FastAPI
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from yuan.agent import build_agent

app = FastAPI(title="Yuan", description="搜索抖音和微信公众号内容")

_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """发送消息给 AI 助手，让它自动搜索并返回结果。"""
    agent = get_agent()
    result = await agent.ainvoke({"messages": [HumanMessage(content=req.message)]})
    reply = result["messages"][-1].content
    return ChatResponse(reply=reply)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
