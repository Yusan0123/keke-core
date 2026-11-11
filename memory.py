import json
import os
from datetime import datetime

import httpx
from fastapi import HTTPException

MEMORY_FILE = "memory_log.json"
MEMORY_SERVICE_URL = os.environ.get("MEMORY_SERVICE_URL", "https://lumi-memory-museum.yusan-159357.workers.dev").rstrip("/")
MEMORY_API_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")


async def request_memory(method, path, payload=None, params=None):
    if not MEMORY_API_TOKEN:
        raise HTTPException(status_code=503, detail="记忆服务鉴权尚未配置")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.request(method, MEMORY_SERVICE_URL + path,
                headers={"X-Memory-Token": MEMORY_API_TOKEN}, json=payload, params=params)
    except httpx.RequestError as error:
        raise HTTPException(status_code=503, detail="记忆服务暂时不可用，请稍后重试") from error
    if response.is_error:
        try:
            detail = response.json().get("detail", "记忆服务请求失败")
        except (ValueError, AttributeError):
            detail = "记忆服务请求失败"
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


def load_memory() -> list:
    if not os.path.exists(MEMORY_FILE):
        return []
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_memory(messages: list):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

def append_turn(role: str, content: str):
    history = load_memory()
    history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })
    if len(history) > 100:
        history = history[-100:]
    save_memory(history)

def get_recent_history(n: int = 20) -> list:
    history = load_memory()
    recent = history[-n:] if len(history) > n else history
    return [{"role": h["role"], "content": h["content"]} for h in recent]


async def recall_long_term(query: str, top_k: int = 4) -> list:
    return (await request_memory("GET", "/memory/search", params={"q": query, "top_k": top_k}))["results"]


async def load_pending() -> list:
    return await request_memory("GET", "/pending")


async def get_short_term(n: int = 5) -> list:
    return (await load_pending())[-n:]


async def append_to_pending(trigger: str, content: str):
    return await request_memory("POST", "/memory/save", payload={"trigger": trigger, "content": content})


async def approve_pending_to_chronicle(entry_id: str) -> bool:
    try:
        await request_memory("POST", "/pending/approve", payload={"id": entry_id})
    except HTTPException as error:
        if error.status_code == 404:
            return False
        raise
    return True


async def delete_pending(entry_id: str) -> bool:
    try:
        await request_memory("POST", "/pending/delete", payload={"id": entry_id})
    except HTTPException as error:
        if error.status_code == 404:
            return False
        raise
    return True


async def ingest_to_chronicle(content: str, date: str = "", tag: str = "", source: str = "ChatGPT") -> dict:
    return await request_memory("POST", "/memory/ingest", payload={"content": content, "date": date, "tag": tag, "source": source})


async def forget_from_chronicle(entry_id: str) -> int:
    try:
        result = await request_memory("POST", "/memory/forget", payload={"id": entry_id})
    except HTTPException as error:
        if error.status_code == 404:
            return 0
        raise
    return result["removed"]
