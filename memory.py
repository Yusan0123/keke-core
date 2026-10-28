import json
import os
import math
import uuid
import base64
from datetime import datetime
import httpx

MEMORY_FILE = "memory_log.json"
CHRONICLE_FILE = "chronicle.json"
PENDING_FILE = "pending.json"

EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "https://aihubmix.com")
EMBED_MODEL = "BAAI/bge-m3"

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Yusan0123/keke-core")
GITHUB_BRANCH = "main"

# ── 基础对话历史（on top of mind）────────────────────

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

def get_short_term(n: int = 5) -> list:
    """取最近n条pending（short term记忆，直接返回，不做向量召回）"""
    pending = load_pending()
    recent = pending[-n:] if len(pending) > n else pending
    return recent

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

# ── 长期记忆 chronicle（long term）──────────────────

def load_chronicle() -> list:
    if not os.path.exists(CHRONICLE_FILE):
        return []
    with open(CHRONICLE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

async def recall_long_term(query: str, top_k: int = 4) -> list:
    """按相关度召回长期记忆"""
    chronicle = load_chronicle()
    if not chronicle:
        return []

    try:
        query_vec = await get_embedding(query)
    except Exception:
        return []

    scored = []
    for entry in chronicle:
        if "embedding" not in entry:
            continue
        sim = cosine_similarity(query_vec, entry["embedding"])
        scored.append((sim, entry))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for sim, e in scored[:top_k] if sim > 0.3]

async def approve_pending_to_chronicle(entry_id: str) -> bool:
    """把pending里的一条审批进chronicle（向量化后存入）"""
    pending = load_pending()
    target = next((e for e in pending if e["id"] == entry_id), None)
    if not target:
        return False

    text = target["content"]
    try:
        embedding = await get_embedding(text)
    except Exception as e:
        print(f"向量化失败：{e}")
        return False

    chronicle = load_chronicle()
    chronicle.append({
        "content": text,
        "timestamp": target["timestamp"],
        "source": "approved_from_pending",
        "embedding": embedding
    })

    with open(CHRONICLE_FILE, "w", encoding="utf-8") as f:
        json.dump(chronicle, f, ensure_ascii=False)

    # 从pending移除
    pending = [e for e in pending if e["id"] != entry_id]
    _save_pending(pending)

    # 两个文件都同步GitHub
    await _push_file_to_github(CHRONICLE_FILE, chronicle, f"approve: memory {entry_id[:8]}")
    await _push_file_to_github(PENDING_FILE, pending, f"approve: remove pending {entry_id[:8]}")
    return True

# ── 待审队列 pending（short term）────────────────────

def load_pending() -> list:
    if not os.path.exists(PENDING_FILE):
        return []
    with open(PENDING_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_pending(pending: list):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)

async def append_to_pending(trigger: str, content: str):
    """克克主动存的记忆先进待审队列"""
    pending = load_pending()
    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "trigger": trigger[:200],   # 触发这条记忆的用户原话
        "content": content,
        "status": "pending"
    }
    pending.append(entry)
    _save_pending(pending)
    await _push_file_to_github(PENDING_FILE, pending, f"keke: new memory note {entry['id'][:8]}")
    print(f"克克记了一条待审记忆：{content[:50]}")

async def delete_pending(entry_id: str) -> bool:
    """删除一条pending条目"""
    pending = load_pending()
    new_pending = [e for e in pending if e["id"] != entry_id]
    if len(new_pending) == len(pending):
        return False
    _save_pending(new_pending)
    await _push_file_to_github(PENDING_FILE, new_pending, f"delete: pending {entry_id[:8]}")
    return True

# ── GitHub 同步工具 ──────────────────────────────────

async def _push_file_to_github(filename: str, data: list, commit_msg: str):
    """通用：把一个json文件推送到GitHub"""
    if not GITHUB_TOKEN:
        print("GITHUB_TOKEN未配置，跳过GitHub同步")
        return

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

    content_bytes = json.dumps(data, ensure_ascii=False).encode("utf-8")
    content_b64 = base64.b64encode(content_bytes).decode("utf-8")

    async with httpx.AsyncClient(timeout=30) as client:
        get_resp = await client.get(api_url, headers=headers)
        sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

        body = {
            "message": commit_msg,
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if sha:
            body["sha"] = sha

        put_resp = await client.put(api_url, headers=headers, json=body)
        if put_resp.status_code in (200, 201):
            print(f"{filename} 已同步到GitHub")
        else:
            print(f"推送 {filename} 失败：{put_resp.status_code} {put_resp.text[:200]}")
