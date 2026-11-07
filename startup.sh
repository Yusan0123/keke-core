#!/bin/bash
# 优先走GitHub API（带钥匙、无CDN缓存），失败再退回raw
fetch() {
  f="$1"
  if [ -n "$GITHUB_TOKEN" ]; then
    curl -sL -H "Authorization: token $GITHUB_TOKEN" -H "Accept: application/vnd.github.raw" \
      "https://api.github.com/repos/Yusan0123/keke-core/contents/$f?ref=main" -o "$f.tmp"
  fi
  if [ ! -s "$f.tmp" ] || head -c 1 "$f.tmp" | grep -vq '[\[{]'; then
    echo "$f: API没拿到，改走raw"
    curl -sL "https://raw.githubusercontent.com/Yusan0123/keke-core/refs/heads/main/$f" -o "$f.tmp"
  fi
  mv "$f.tmp" "$f"
  echo "$f 大小：$(wc -c < $f) bytes"
}
fetch chronicle.json
fetch pending.json
uvicorn main:app --host 0.0.0.0 --port $PORT
