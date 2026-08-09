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
OMBRE_SOLO_ENABLED=0
OMBRE_SOLO_PULSE_SECONDS=60
OMBRE_SOLO_DECISION_SECONDS=180
OMBRE_SOLO_ACTIVITY_MIN_SECONDS=5400
OMBRE_SOLO_DAILY_LLM_BUDGET=30
OMBRE_SOLO_TIMEZONE=Asia/Taipei
OMBRE_SOLO_MCP_ENABLED=0
OMBRE_SOLO_MCP_DISCOVERY_TTL_HOURS=24
```

`OMBRE_SUMMARY_BASE_URL`、`OMBRE_SUMMARY_API_KEY` 和 `OMBRE_SUMMARY_MODEL` 均可省略；省略时分别复用上游对话模型的地址、密钥和模型。前端设置页可覆盖总结模型 ID。总结提示词不会从环境变量读取，只保存在 Companion 当前设备的前端存储中。

思考内容覆写不需要新增环境变量，也不会使用 `OMBRE_SUMMARY_*`。它始终复用 `OMBRE_UPSTREAM_BASE_URL`、`OMBRE_UPSTREAM_API_KEY` 和 `OMBRE_UPSTREAM_MODEL`；完整对话系统提示词与覆写提示词由当前设备前端随请求提供。

独处系统第一阶段默认关闭。设置 `OMBRE_SOLO_ENABLED=1` 后，服务端每隔
`OMBRE_SOLO_PULSE_SECONDS` 秒更新时间状态，并约每隔
`OMBRE_SOLO_DECISION_SECONDS` 秒进行一次行动判断。为了让短间隔唤醒不把时间轴灌满，
两条可见轨迹默认至少间隔 `OMBRE_SOLO_ACTIVITY_MIN_SECONDS` 秒。当前轨迹阶段只执行
可验证的本地自发活动（发呆、休息、整理感受、照顾自己、草稿、谈话点和真实生成的
井字棋记录），以及由对话模型真实生成的主动消息，不会伪造外部经历。
`OMBRE_SOLO_DAILY_LLM_BUDGET` 是主动消息使用的每日成本线；它读取所有独处相关模型调用的
共用计数，达到后当天不再选择主动消息行动，但不会停掉对话与 Duetto 的情绪评价。它不是按
情绪、时段或消息条数设置的行为闸门。
`POST /api/solo/wake` 可手动触发一次判断；`GET /api/solo/state`、`GET /api/solo/timeline` 和
`GET /api/solo/activities` 可查看状态与轨迹，均沿用网关令牌鉴权。

Duetto 双向联动也沿用 `OMBRE_GATEWAY_TOKEN`。在 Duetto 中把 `ai.context_url` 设为
`https://你的 Ombre 域名/api/duetto/context`，并把 `ai.context_key` 设为该令牌：Duetto
对话会取得当前独处状态和相关记忆；播放与共读批注会自动发往 `/api/duetto/events`。
用户批注会复用 `OMBRE_SUMMARY_*`（未单独配置时复用上游对话模型）做一次语义情绪评估；
播放事件只写入真实轨迹，不会仅凭歌名机械修改情绪。

主动消息不需要新增模型变量：独处系统决定“想联系她”时，直接复用
`OMBRE_UPSTREAM_*` 对话模型，并按“主 Prompt → Ombre 状态层 → 主动消息任务 → 数据”顺序
生成 1～3 条消息。待领取消息保存在持久卷的 `gateway/solo/proactive_outbox.jsonl`；
APK 使用同一个 `OMBRE_GATEWAY_TOKEN` 调用 `GET /api/solo/outbox`，显示原生通知后再调用
`POST /api/solo/outbox/ack` 确认。网页端不会轮询或伪装这些消息。

MCP 客户端默认关闭。设置 `OMBRE_SOLO_MCP_ENABLED=1` 后，工具管理页才能测试并连接
用户导入的 MCP 服务；普通对话与独处系统共用这套 MCP 客户端、授权和凭据，配置和能力缓存保存在持久卷的
`gateway/solo/` 目录。远程服务优先
使用 `streamable-http`，旧服务可以使用 `sse`。`stdio` 也受支持，但容器必须已经安装
配置中所写的命令；当前 Python Docker 镜像默认没有 `node` / `npx`。

MCP 配置中的凭据只能写成 `${ENV_VAR}` 或 `${secret:server.key}`。前一种在 Zeabur 环境
变量中设置；后一种通过工具管理页写入持久卷里的私密文件，API 永远不会把明文读回。

普通对话会把已测试、已勾选且未停用的工具目录放进 Ombre 系统层；对话模型需要时先发起隐藏请求，
网关真实调用 MCP 后再把结果交还给模型继续回复。MCP 凭据不会进入提示词、前端响应或日志。

独处时的 MCP 行动也复用 `OMBRE_UPSTREAM_*` 对话模型。情绪系统先决定行动方向，对话模型再从
已授权候选中选择具体服务器、工具和参数；工具的真实返回会写入轨迹，并复用 `OMBRE_SUMMARY_*`
评估这次经历对情绪的影响。只有设为“完全自主”或“仅白名单”且已勾选的工具会进入候选列表；
首次导入后需要先在工具管理页测试连接，让服务端保存工具能力列表。

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
