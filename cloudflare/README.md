记忆博物馆的持久数据保存在 Cloudflare D1，检索索引保存在 Vectorize。Workers AI 的 BGE-M3 负责生成新向量及查询向量。Render 保留聊天页面和原有接口，长期记忆与待审日记的请求由 Cloudflare 处理。

Cloudflare 服务地址为 https://lumi-memory-museum.yusan-159357.workers.dev，直接 MCP 入口为该地址下的 `/mcp`。原有 Render 接入地址在转发版本上线后继续使用。鉴权沿用原来的 `X-Memory-Token` 或 MCP Bearer 密钥，秘密值保存在 Cloudflare 的加密配置中。

新记忆先写入 D1，再建立检索索引。向量服务或索引暂时失败时，原文仍保留，服务每两分钟重试待处理任务。重复请求会复用已保存的记录，跨分钟重试也保留首次保存的日期。查询结果从 D1 读取原文，已移除的记录不会因旧索引尚未清理而再次显示。

2026年11月11日迁移了967条长期记忆。迁移保留原文、出处、日期、标签及原始向量。18条旧记录缺少编号，迁移为它们分配了稳定唯一编号，原始记录的全部非向量字段保存在 `original_json`，原始向量另列保存。

迁移前后的完整编年史备份保留在仓库的 `backups/2026-11-11_before_mcp` 与 `backups/2026-11-11_after_mcp_ae7c7e5b`。Cloudflare 数据库独立于 Worker 与 Render 的代码版本。迁移核对记录保存在本地 `.migration`，该目录不提交到仓库。

部署需要使用本账户的 Wrangler 登录。数据库与索引名称均为 `lumi-memory-museum`，密钥通过 `wrangler secret put MEMORY_API_TOKEN` 配置。更新代码使用 `wrangler deploy`，不需要重新导入编年史。

本地检查使用 Node.js 22.13 或以上版本和现有 Render Python 依赖。

```sh
node --test test/*.test.js
python -m unittest discover -s test -p 'test_render_proxy.py'
```

Render 对应文件为仓库根目录的 `main.py`、`memory.py`、`startup.sh`。`MEMORY_SERVICE_URL` 可覆盖 Cloudflare 地址，默认值已指向本服务。
