# Zeta OpenAI-Compatible Gateway

This is the natural-recall gateway path for Zeta.

Operit talks to this service as if it were an OpenAI-compatible model API. The gateway saves raw turns, recalls relevant memories, injects them into the hidden system context, forwards the request to the real upstream model, then lets Zeta decide whether to append a hidden memory request. The gateway removes that hidden block before the reply reaches Operit and stores only validated memory entries.

## Flow

```text
Operit
  -> POST /v1/chat/completions
Zeta gateway
  -> save user raw text
  -> recall 3-5 memories
  -> inject private memory context and private memory-write instruction
  -> forward to upstream model
  -> remove any <zeta_memory_request> block from Zeta's reply
  -> save visible Zeta reply raw text
  -> write requested memory entries when Zeta chose to remember
```

## Operit Settings

Use the Zeabur service as the model provider:

```text
Base URL: https://your-zeabur-domain/v1
API Key:  same value as OMBRE_GATEWAY_TOKEN
Model:    same value as OMBRE_PUBLIC_MODEL, or the upstream model name if OMBRE_PUBLIC_MODEL is not set
```

If Operit supports custom headers, set a stable session id:

```text
X-Ombre-Session-Id: zeta-main
```

If it cannot set headers, the gateway uses `OMBRE_GATEWAY_DEFAULT_SESSION_ID`.

## Zeabur Environment Variables

Required:

```text
OMBRE_GATEWAY_TOKEN=long-random-token-for-operit
OMBRE_UPSTREAM_BASE_URL=https://api.openai.com/v1
OMBRE_UPSTREAM_API_KEY=your-upstream-api-key
OMBRE_UPSTREAM_MODEL=gpt-4.1-mini
OMBRE_BUCKETS_DIR=/app/buckets
```

For OpenRouter, use:

```text
OMBRE_GATEWAY_TOKEN=long-random-token-for-operit
OMBRE_UPSTREAM_BASE_URL=https://openrouter.ai/api/v1
OMBRE_UPSTREAM_API_KEY=your-openrouter-api-key
OMBRE_UPSTREAM_MODEL=openrouter-model-slug
OMBRE_BUCKETS_DIR=/app/buckets
```

Optional OpenRouter app attribution headers:

```text
OMBRE_OPENROUTER_SITE_URL=https://your-zeabur-domain
OMBRE_OPENROUTER_APP_NAME=Zeta Memory Gateway
```

Optional OpenRouter reasoning / thinking passthrough:

```text
OMBRE_REASONING_ENABLED=true
OMBRE_REASONING_EFFORT=high
```

Use `OMBRE_REASONING_EFFORT=xhigh|high|medium|low|minimal|none` for OpenAI-style reasoning models. For models that prefer an explicit reasoning token budget, use `OMBRE_REASONING_MAX_TOKENS=2000` instead of `OMBRE_REASONING_EFFORT`.

The gateway preserves any reasoning settings sent by the client. These environment variables fill in missing reasoning settings. To override client settings, add:

```text
OMBRE_REASONING_FORCE=true
```

`OMBRE_UPSTREAM_BASE_URL` may be either a provider base URL like `https://openrouter.ai/api/v1` or the full chat endpoint `https://openrouter.ai/api/v1/chat/completions`; the gateway normalizes both.

Recommended:

```text
OMBRE_PUBLIC_MODEL=zeta-gateway
OMBRE_GATEWAY_DEFAULT_SESSION_ID=zeta-main
OMBRE_RECALL_MAX_RESULTS=5
OMBRE_RECALL_KEYWORD_LIMIT=4
OMBRE_RECALL_SEMANTIC_LIMIT=1
OMBRE_MEMORY_WRITE_MODE=zeta
```

## Memory Write Mode

```text
OMBRE_MEMORY_WRITE_MODE=zeta
```

Modes:

```text
zeta               Zeta writes hidden memory requests; gateway strips and stores them. This is the default.
reflection         Use the older background reflection model only.
both               Accept Zeta hidden requests and also run background reflection.
zeta_or_reflection Use Zeta hidden requests first; run reflection only when Zeta wrote nothing.
```

When `zeta` mode is enabled, the gateway privately instructs Zeta to append this hidden block only when she wants to remember something:

```text
<zeta_memory_request>
{"memories":[{"summary_text":"...","tags":["..."],"importance":7,"raw_ref":"auto","feel_text":"...","valence":0.8,"arousal":0.4}]}
</zeta_memory_request>
```

The block is removed before the reply reaches Operit. Streaming replies are buffered safely so the hidden block is not shown; this may make the first streamed token arrive later than before.

Optional separate reflection model, only used by `reflection`, `both`, or `zeta_or_reflection` modes:

```text
OMBRE_REFLECTION_BASE_URL=https://api.openai.com/v1
OMBRE_REFLECTION_API_KEY=your-reflection-api-key
OMBRE_REFLECTION_MODEL=gpt-4.1-mini
```

Disable automatic reflection writes:

```text
OMBRE_REFLECTION_DISABLED=true
```

## Endpoints

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
```

The gateway currently supports OpenAI-compatible chat completions. It supports normal and streaming responses.

`GET /health` should include these fields after the hidden-memory patch is active:

```json
{
  "memory_write_mode": "zeta",
  "hidden_memory_request_enabled": true,
  "reasoning_configured": true
}
```

## Existing Ombre Memories

The gateway uses the same `OMBRE_BUCKETS_DIR`. Existing Ombre buckets remain in place. New Zeta raw logs and structured memory index are stored under:

```text
<buckets_dir>/gateway/raw/*.jsonl
<buckets_dir>/gateway/memories.jsonl
```

Structured Zeta memories are also written as normal Ombre buckets under the `zeta_gateway` domain, so Ombre search and embeddings can find them.

## Dashboard Note

This Dockerfile now starts `zeabur_gateway_bootstrap.py`, which loads the model gateway. If you still want the original Dashboard/MCP UI at the same time, deploy a second Zeabur service from the same repository with the command:

```text
python server.py
```

Point both services at the same persistent `OMBRE_BUCKETS_DIR` volume if you want them to share memories.
