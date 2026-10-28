#!/bin/bash
if [ ! -f "chronicle.json" ]; then
    echo "下载chronicle.json..."
    curl -L "https://raw.githubusercontent.com/Yusan0123/keke-core/refs/heads/main/chronicle.json" -o chronicle.json
    echo "下载完成！"
fi
uvicorn main:app --host 0.0.0.0 --port $PORT
