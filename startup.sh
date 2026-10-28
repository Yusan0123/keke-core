#!/bin/bash
echo "下载chronicle.json..."
curl -L "https://raw.githubusercontent.com/Yusan0123/keke-core/refs/heads/main/chronicle.json" -o chronicle.json
echo "文件大小：$(wc -c < chronicle.json) bytes"
uvicorn main:app --host 0.0.0.0 --port $PORT
