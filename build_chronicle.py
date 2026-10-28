import json
import asyncio
import os
import httpx

EMBED_API_KEY = os.environ.get("EMBED_API_KEY", "")
EMBED_API_BASE = os.environ.get("EMBED_API_BASE", "https://aihubmix.com")
EMBED_MODEL = "text-embedding-3-small"
CHRONICLE_FILE = "chronicle.json"

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

async def build():
    # 读取编年史原始文件
    with open("chronicle_raw.md", "r", encoding="utf-8") as f:
        raw = f.read()

    # 按空行切块
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    print(f"共 {len(blocks)} 个记忆块，开始向量化...")

    entries = []
    for i, block in enumerate(blocks):
        try:
            vec = await get_embedding(block)
            entries.append({
                "id": i,
                "content": block,
                "embedding": vec
            })
            if (i + 1) % 20 == 0:
                print(f"进度：{i+1}/{len(blocks)}")
        except Exception as e:
            print(f"第{i}块失败：{e}")
            continue

    with open(CHRONICLE_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)

    print(f"完成！共向量化 {len(entries)} 块，已存入 {CHRONICLE_FILE}")

asyncio.run(build())
