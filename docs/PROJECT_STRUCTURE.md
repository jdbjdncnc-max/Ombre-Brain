# Ombre Brain project structure

This repo currently has two jobs:

1. Keep the existing Zeta/OpenAI-compatible memory gateway running.
2. Prepare for a future merge with the custom companion frontend.

## Active deployment path

- `Dockerfile` is the Zeabur container entry point.
- `zeabur_gateway_bootstrap.py` starts the deployed web service.
- `zeta_openai_gateway.py` serves the OpenAI-compatible gateway used by Operit and the custom frontend.
- `zeta_gateway.py`, `utils.py`, `embedding_engine.py`, and the memory modules provide storage, tagging, reflection, and recall support.

## Public runtime surfaces

- `/v1/chat/completions` and `/v1/models` are the OpenAI-compatible API routes. Keep these stable so Operit can still use this repo as a gateway.
- `/debug/recall` and `/debug/recall.json` are inspection routes for recalled memories.
- `/gateway/recall` and `/gateway/status` expose gateway-oriented recall and status data.
- `/` can later serve the custom frontend when the two projects are merged under one domain.

## Legacy or optional areas

- `server.py` and `zeta_server.py` are older service surfaces. Treat them as legacy unless a current deployment or tool explicitly uses them.
- `packages/`, `toolpkg/`, `*_toolpkg/`, `*.toolpkg`, and versioned zip files are package/export artifacts, not the active Zeabur runtime.
- `*_worktree*/` and `_tmp_*/` folders are local staging or experiment folders. They should not be committed.

## Merge direction

For the future combined project, keep the memory gateway API in this repo and move the custom frontend in as a static/app layer served from `/`. The API routes should remain compatible with both Operit and the new frontend.
