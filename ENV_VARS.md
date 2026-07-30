# 环境变量参考

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `OMBRE_API_KEY` | 是 | — | Gemini / OpenAI-compatible API Key，用于脱水(dehydration)和向量嵌入 |
| `OMBRE_BASE_URL` | 否 | `https://generativelanguage.googleapis.com/v1beta/openai/` | API Base URL（可替换为代理或兼容接口） |
| `OMBRE_TRANSPORT` | 否 | `stdio` | MCP 传输模式：`stdio` / `sse` / `streamable-http` |
| `OMBRE_PORT` | 否 | `8000` | HTTP/SSE 模式监听端口（仅 `sse` / `streamable-http` 生效） |
| `OMBRE_BUCKETS_DIR` | 否 | `./buckets` | 记忆桶文件存放目录（绑定 Docker Volume 时务必设置） |
| `OMBRE_HOOK_URL` | 否 | — | Breath/Dream Webhook 推送地址（POST JSON），留空则不推送 |
| `OMBRE_HOOK_SKIP` | 否 | `false` | 设为 `true`/`1`/`yes` 跳过 Webhook 推送（即使 `OMBRE_HOOK_URL` 已设置） |
| `OMBRE_DASHBOARD_PASSWORD` | 否 | — | 预设 Dashboard 访问密码；设置后覆盖文件存储的密码，首次访问不弹设置向导 |
| `OMBRE_DEHYDRATION_MODEL` | 否 | `deepseek-chat` | 脱水/打标/合并/拆分用的 LLM 模型名（覆盖 `dehydration.model`） |
| `OMBRE_DEHYDRATION_BASE_URL` | 否 | `https://api.deepseek.com/v1` | 脱水模型的 API Base URL（覆盖 `dehydration.base_url`） |
| `OMBRE_MODEL` | 否 | — | `OMBRE_DEHYDRATION_MODEL` 的别名（前者优先） |
| `OMBRE_EMBEDDING_MODEL` | 否 | `gemini-embedding-001` | 向量嵌入模型名（覆盖 `embedding.model`） |
| `OMBRE_EMBEDDING_BASE_URL` | 否 | — | 向量嵌入的 API Base URL（覆盖 `embedding.base_url`；留空则复用脱水配置） |

## 说明

- `OMBRE_API_KEY` 也可在 `config.yaml` 的 `dehydration.api_key` / `embedding.api_key` 中设置，但**强烈建议**通过环境变量传入，避免密钥写入文件。
- `OMBRE_DASHBOARD_PASSWORD` 设置后，Dashboard 的"修改密码"功能将被禁用（显示提示，建议直接修改环境变量）。未设置则密码存储在 `{buckets_dir}/.dashboard_auth.json`（SHA-256 + salt）。

## Webhook 推送格式 (`OMBRE_HOOK_URL`)

设置 `OMBRE_HOOK_URL` 后，Ombre Brain 会在以下事件发生时**异步**（fire-and-forget，5 秒超时）`POST` JSON 到该 URL：

| 事件名 (`event`) | 触发时机 | `payload` 字段 |
|------------------|----------|----------------|
| `breath` | MCP 工具 `breath()` 返回时 | `mode` (`ok`/`empty`), `matches`, `chars` |
| `dream` | MCP 工具 `dream()` 返回时 | `recent`, `chars` |
| `breath_hook` | HTTP `GET /breath-hook` 命中（SessionStart 钩子） | `surfaced`, `chars` |
| `dream_hook` | HTTP `GET /dream-hook` 命中 | `surfaced`, `chars` |

请求体结构（JSON）：

## Zeta gateway Zeabur variables

Use these for the OpenAI-compatible memory gateway service:

```text
OMBRE_GATEWAY_TOKEN=long-random-token
OMBRE_UPSTREAM_BASE_URL=https://openrouter.ai/api/v1
OMBRE_UPSTREAM_API_KEY=your-openrouter-key
OMBRE_UPSTREAM_MODEL=openrouter-model-slug
OMBRE_SUMMARY_BASE_URL=https://openrouter.ai/api/v1
OMBRE_SUMMARY_API_KEY=your-openrouter-key
OMBRE_SUMMARY_MODEL=summary-model-slug
OMBRE_OPENROUTER_SITE_URL=https://your-zeabur-domain
OMBRE_OPENROUTER_APP_NAME=Zeta Memory Gateway
OMBRE_PUBLIC_MODEL=zeta-gateway
OMBRE_BUCKETS_DIR=/app/buckets
OMBRE_DASHBOARD_PASSWORD=your-dashboard-password
```

`OMBRE_SUMMARY_BASE_URL`、`OMBRE_SUMMARY_API_KEY` 和 `OMBRE_SUMMARY_MODEL` 均可省略；省略时分别复用上游对话模型的地址、密钥和模型。前端设置页可覆盖总结模型 ID。总结提示词不会从环境变量读取，只保存在 Companion 当前设备的前端存储中。

Use these for memory quality. `OMBRE_API_KEY` still works as a legacy shared key, but the split variables are clearer:

```text
OMBRE_DEHYDRATION_API_KEY=key-for-tagging-and-memory-reflection
OMBRE_DEHYDRATION_BASE_URL=https://openrouter.ai/api/v1
OMBRE_DEHYDRATION_MODEL=openrouter-model-slug
OMBRE_EMBEDDING_ENABLED=true
OMBRE_EMBEDDING_API_KEY=key-for-embedding
OMBRE_EMBEDDING_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
OMBRE_EMBEDDING_MODEL=gemini-embedding-001
```

Recommended recall defaults after the hybrid recall update:

```text
OMBRE_RECALL_STRATEGY=hybrid
OMBRE_RECALL_MAX_RESULTS=5
OMBRE_RECALL_KEYWORD_LIMIT=2
OMBRE_RECALL_SEMANTIC_LIMIT=4
OMBRE_RECALL_INCLUDE_LEGACY=true
```

```json
{
  "event": "breath",
  "timestamp": 1730000000.123,
  "payload": { "...": "..." }
}
```

Webhook 推送失败仅在服务日志中以 WARNING 级别记录，**不会影响 MCP 工具的正常返回**。
