# Zeta OpenAI-Compatible Gateway

This is the natural-recall gateway path for Zeta.

Operit talks to this service as if it were an OpenAI-compatible model API. The gateway saves raw turns, recalls relevant memories, injects them into the hidden system context, forwards the request to the real upstream model, then reflects after the reply and writes long-term memories when appropriate.

## Flow

```text
Operit
  -> POST /v1/chat/completions
Zeta gateway
  -> save user raw text
  -> recall 3-5 memories
  -> inject private memory context
  -> forward to upstream model
  -> save Zeta reply raw text
  -> background reflection
  -> write memory entries if the exchange is worth remembering
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

Recommended:

```text
OMBRE_PUBLIC_MODEL=zeta-gateway
OMBRE_GATEWAY_DEFAULT_SESSION_ID=zeta-main
OMBRE_RECALL_MAX_RESULTS=5
OMBRE_RECALL_KEYWORD_LIMIT=4
OMBRE_RECALL_SEMANTIC_LIMIT=1
```

Optional separate reflection model:

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

## Existing Ombre Memories

The gateway uses the same `OMBRE_BUCKETS_DIR`. Existing Ombre buckets remain in place. New Zeta raw logs and structured memory index are stored under:

```text
<buckets_dir>/gateway/raw/*.jsonl
<buckets_dir>/gateway/memories.jsonl
```

Structured Zeta memories are also written as normal Ombre buckets under the `zeta_gateway` domain, so Ombre search and embeddings can find them.

## Dashboard Note

This Dockerfile now starts `zeta_openai_gateway.py`, which is the model gateway. If you still want the original Dashboard/MCP UI at the same time, deploy a second Zeabur service from the same repository with the command:

```text
python server.py
```

Point both services at the same persistent `OMBRE_BUCKETS_DIR` volume if you want them to share memories.
