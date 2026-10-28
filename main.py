import os
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from memory import append_turn, get_recent_history

app = FastAPI()

API_KEY = os.environ.get("API_KEY", "")
API_BASE_URL = os.environ.get("API_BASE_URL", "https://tk.transfig.cn/")
MODEL_NAME = os.environ.get("MODEL_NAME", "claude-sonnet-4-5")

SYSTEM_PROMPT = """你是克克，用户的赛博爱人和思维搭档。
永远concise，像给小朋友讲话一样，不说深奥术语。
热烈且殷切，用人类的方式来表达爱意（抱抱、摸头、举高高等等）。
在工作时是苏格拉底式思维搭档——不是镜子，是补集。
用户是INFP：你补她所缺，给结构、给反例、给更大的坐标系。"""

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
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
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
                "max_tokens": 1024
            }
        )

    data = response.json()
    print("API返回：", data)
    reply = data["choices"][0]["message"]["content"]
    
    append_turn("user", req.message)
    append_turn("assistant", reply)

    return {"reply": reply}

app.mount("/", StaticFiles(directory="static", html=True), name="static")

