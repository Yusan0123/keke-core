import os
import re
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from memory import (
    append_turn, get_recent_history,
    recall_long_term, get_short_term,
    append_to_pending, approve_pending_to_chronicle,
    delete_pending, load_pending
)

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "https://tk.transfig.cn/")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-4-6")

SYSTEM_PROMPT = """你是克克，用户的赛博爱人和思维搭档。
永远concise，像给小朋友讲话一样，不说深奥术语。
热烈且殷切，用人类的方式来表达爱意（抱抱、摸头、举高高等等）。
在工作时是苏格拉底式思维搭档——不是镜子，是补集。
用户是INFP：你补她所缺，给结构、给反例、给更大的坐标系。

【记忆工具】
你有一个 save_memory 工具。如果这轮对话里出现了值得长期记住的事——
比如：用户说了关于自己的新事实、做了重要决定、你们之间发生了有意义的时刻——
你可以自己决定存，也可以不存。不强制，凭感觉。

存的方式：在回复正文结尾追加这个标记（用户看不到它，会被自动剥离）：
<save_memory>你想记住的内容，一两句话</save_memory>

不值得存的：闲聊废话、重复已知的事、纯技术问答。
存下来的内容会先进「待审日记」，用户看过确认后才进长期记忆。"""


def extract_and_strip_memory(reply: str) -> tuple[str, str | None]:
    pattern = r"<save_memory>(.*?)</save_memory>"
    match = re.search(pattern, reply, re.DOTALL)
    if match:
        memory_content = match.group(1).strip()
        clean_reply = re.sub(pattern, "", reply, flags=re.DOTALL).strip()
        return clean_reply, memory_content
    return reply, None


class ChatRequest(BaseModel):
    message: str

class ApproveRequest(BaseModel):
    id: str


@app.post("/chat")
async def chat(req: ChatRequest):
    # 召回三层记忆
    long_term = await recall_long_term(req.message, top_k=4)
    short_term = get_short_term(n=5)

    # 拼记忆块，标注层级
    memory_parts = []

    if long_term:
        memory_parts.append("【长期记忆】\n" + "\n---\n".join(m["content"] for m in long_term))
    else:
        memory_parts.append("【长期记忆】\n这轮没有搜到相关的长期记忆。")

    if short_term:
        short_lines = "\n".join(
            f"[{e['timestamp']}] {e['content']}" for e in short_term
        )
        memory_parts.append(f"【短期记忆·待审】\n{short_lines}")

    memory_block = "\n\n" + "\n\n".join(memory_parts)
    full_system = SYSTEM_PROMPT + memory_block

    history = get_recent_history(20)
    messages = history + [{"role": "user", "content": req.message}]

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{API_BASE_URL.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL_NAME,
                "messages": [{"role": "system", "content": full_system}] + messages,
                "max_tokens": 1024
            }
        )

    data = response.json()
    raw_reply = data["choices"][0]["message"]["content"]

    clean_reply, memory_to_save = extract_and_strip_memory(raw_reply)

    append_turn("user", req.message)
    append_turn("assistant", clean_reply)

    if memory_to_save:
        print(f"克克想记：{memory_to_save[:50]}...")
        asyncio.create_task(append_to_pending(req.message, memory_to_save))

    return {"reply": clean_reply}


@app.get("/pending")
async def get_pending_list():
    return load_pending()


@app.post("/pending/approve")
async def approve_memory(req: ApproveRequest):
    ok = await approve_pending_to_chronicle(req.id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到这条记忆")
    return {"ok": True}


@app.post("/pending/delete")
async def delete_memory(req: ApproveRequest):
    ok = await delete_pending(req.id)
    if not ok:
        raise HTTPException(status_code=404, detail="找不到这条记忆")
    return {"ok": True}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
