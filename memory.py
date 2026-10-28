import json
import os
import math
import base64
from datetime import datetime
import httpx

MEMORY_FILE = "memory_log.json"
CHRONICLE_FILE = "chronicle.json"

EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "https://aihubmix.com")
EMBED_MODEL = "BAAI/bge-m3"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Yusan0123/keke-core")
GITHUB_FILE_PATH = "chronicle.json"
GITHUB_BRANCH = "main"

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

    results = [e for sim, e in top if sim > 0.3]
    return results

# ── 自动更新记忆到 GitHub ─────────────────────────────

async def append_to_chronicle(user_msg: str, assistant_reply: str):
    """把新对话向量化后追加进chronicle.json并推送到GitHub"""
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN未配置，跳过记忆更新")
        return

    # 拼成一条记忆
    text = f"用户：{user_msg}\n克克：{assistant_reply}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    try:
        embedding = await get_embedding(text)
    except Exception as e:
        print(f"向量化失败，跳过记忆更新：{e}")
        return

    new_entry = {
        "content": text,
        "timestamp": timestamp,
        "embedding": embedding
    }

    # 读本地chronicle，追加
    chronicle = load_chronicle()
    chronicle.append(new_entry)

    # 写回本地
    with open(CHRONICLE_FILE, "w", encoding="utf-8") as f:
        json.dump(chronicle, f, ensure_ascii=False)

    # 推送到GitHub
    await _push_chronicle_to_github(chronicle)

async def _push_chronicle_to_github(chronicle: list):
    """把chronicle.json推送到GitHub"""
    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    content_bytes = json.dumps(chronicle, ensure_ascii=False).encode("utf-8")
    content_b64 = base64.b64encode(content_bytes).decode("utf-8")

    async with httpx.AsyncClient(timeout=30) as client:
        # 先获取当前文件的sha（GitHub要求更新时带sha）
        get_resp = await client.get(api_url, headers=headers)
        if get_resp.status_code != 200:
            print(f"获取GitHub文件sha失败：{get_resp.status_code}")
            return
        sha = get_resp.json()["sha"]

        # 推送更新
        put_resp = await client.put(
            api_url,
            headers=headers,
            json={
                "message": f"auto: update chronicle {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                "content": content_b64,
                "sha": sha,
                "branch": GITHUB_BRANCH
            }
        )
        if put_resp.status_code in (200, 201):
            print("chronicle.json 已同步到GitHub")
        else:
            print(f"推送GitHub失败：{put_resp.status_code} {put_resp.text[:200]}")
