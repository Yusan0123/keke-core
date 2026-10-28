import json
import os
import math
from datetime import datetime
import httpx

MEMORY_FILE = "memory_log.json"
CHRONICLE_FILE = "chronicle.json"

EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "https://aihubmix.com")
EMBED_MODEL = "BAAI/bge-m3"

# ── 基础对话历史 ──────────────────────────────────────

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

# ── 向量工具 ──────────────────────────────────────────

async def get_embedding(text: str) -> list:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{EMBED_API_BASE.rstrip('/')}/v1/embeddings",
            headers={
                "Authorization": f"Bearer {EMBED_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": EMBED_MODEL,
                "input": text[:512]
            }
        )
    return response.json()["data"][0]["embedding"]

def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

# ── 编年史索引 ────────────────────────────────────────

def load_chronicle() -> list:
    if not os.path.exists(CHRONICLE_FILE):
        return []
    with open(CHRONICLE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

async def recall_memories(query: str, top_k: int = 4) -> list:
    """按相关度召回最匹配的记忆条目"""
    chronicle = load_chronicle()
    if not chronicle:
        return []

    # 没有向量的条目跳过
    scored = []
    try:
        query_vec = await get_embedding(query)
    except Exception:
        return []

    for entry in chronicle:
        if "embedding" not in entry:
            continue
        sim = cosine_similarity(query_vec, entry["embedding"])
        scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:top_k]

    # 过滤掉相关度太低的（门槛0.3）
    results = [e for sim, e in top if sim > 0.3]
    return results
