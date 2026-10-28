import json
import os
from datetime import datetime

MEMORY_FILE = "memory_log.json"

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
    # 只保留最近100条，防止太长
    if len(history) > 100:
        history = history[-100:]
    save_memory(history)

def get_recent_history(n: int = 20) -> list:
    """返回最近n条，格式化成Claude能读的messages列表"""
    history = load_memory()
    recent = history[-n:] if len(history) > n else history
    return [{"role": h["role"], "content": h["content"]} for h in recent]
