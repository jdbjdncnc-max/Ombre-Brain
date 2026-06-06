import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ombre_brain.zeabur_bootstrap")


def _port() -> int:
    for name in ("OMBRE_PORT", "PORT"):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        try:
            return int(value)
        except ValueError:
            logger.warning("Ignoring invalid %s=%r", name, value)
    return 8000


def _startup_error_app(error: str) -> Starlette:
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "startup_error",
                "gateway": "zeta_openai",
                "error": error,
                "hint": "The Zeabur web server is alive, but the memory gateway failed during import. Check this JSON and Zeabur logs.",
            },
            status_code=503,
        )

    async def unavailable(request: Request) -> JSONResponse:
        return JSONResponse(
            {"error": {"message": f"Gateway startup failed: {error}", "type": "server_error"}},
            status_code=503,
        )

    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/v1/models", unavailable, methods=["GET"]),
        Route("/v1/chat/completions", unavailable, methods=["POST"]),
    ])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    return app


def _debug_authorize(request: Request) -> JSONResponse | None:
    token = os.environ.get("OMBRE_GATEWAY_TOKEN", "").strip()
    if not token:
        return None
    auth = request.headers.get("authorization", "")
    header_token = request.headers.get("x-api-key", "")
    provided = auth[7:].strip() if auth.lower().startswith("bearer ") else header_token.strip()
    if provided == token:
        return None
    return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)


def _recall_debug_page() -> str:
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Zeta Recall Debug</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; padding: 24px; background: #101114; color: #f4f1eb; }
    main { max-width: 980px; margin: 0 auto; }
    h1 { font-size: 24px; margin: 0 0 18px; }
    .bar { display: grid; gap: 10px; grid-template-columns: 1.4fr 1.8fr auto auto; align-items: end; }
    label { display: grid; gap: 6px; color: #c9c3b7; font-size: 13px; }
    input, button, textarea { border: 1px solid #3b3d45; border-radius: 6px; background: #181a20; color: #f4f1eb; font: inherit; }
    input { padding: 10px 12px; min-width: 0; }
    button { padding: 10px 14px; cursor: pointer; background: #2f6f6a; border-color: #4c918b; }
    button.secondary { background: #272a32; border-color: #454956; }
    .status { margin: 16px 0; color: #b9c7c4; min-height: 22px; }
    .grid { display: grid; gap: 12px; }
    .card { border: 1px solid #323540; border-radius: 8px; background: #17191f; padding: 14px; }
    .meta { color: #b8b0a3; font-size: 13px; display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 8px; }
    .summary { white-space: pre-wrap; line-height: 1.5; }
    .raw { margin-top: 18px; }
    textarea { width: 100%; min-height: 180px; padding: 12px; box-sizing: border-box; line-height: 1.45; }
    @media (max-width: 760px) { .bar { grid-template-columns: 1fr; } body { padding: 16px; } }
  </style>
</head>
<body>
  <main>
    <h1>Zeta Recall Viewer</h1>
    <div class="bar">
      <label>Gateway token <input id="token" type="password" autocomplete="current-password"></label>
      <label>Query / current message <input id="query" placeholder="e.g. bus"></label>
      <button id="run">Run recall</button>
      <button class="secondary" id="last">Latest recall</button>
    </div>
    <div class="status" id="status"></div>
    <div class="grid" id="results"></div>
    <div class="raw">
      <label>Injected memory text <textarea id="injection" readonly></textarea></label>
    </div>
  </main>
  <script>
    const tokenInput = document.getElementById('token');
    const queryInput = document.getElementById('query');
    const statusEl = document.getElementById('status');
    const resultsEl = document.getElementById('results');
    const injectionEl = document.getElementById('injection');
    tokenInput.value = localStorage.getItem('zeta-debug-token') || '';
    tokenInput.addEventListener('change', () => localStorage.setItem('zeta-debug-token', tokenInput.value));

    function esc(s) {
      return String(s ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    }
    function render(data) {
      const memories = data.memories || [];
      statusEl.textContent = `${data.mode || 'recall'} | count=${memories.length} | query=${data.query || ''}`;
      injectionEl.value = data.injection_text || '';
      resultsEl.innerHTML = memories.map((m, i) => `
        <article class="card">
          <div class="meta">
            <span>#${i + 1}</span>
            <span>source: ${esc(m.source)}</span>
            <span>score: ${esc(m.score)}</span>
            <span>importance: ${esc(m.importance)}</span>
            <span>raw_ref: ${esc(m.raw_ref)}</span>
          </div>
          <div class="summary">${esc(m.summary_text)}</div>
          ${m.tags && m.tags.length ? `<div class="meta">tags: ${esc(m.tags.join(', '))}</div>` : ''}
          ${m.feel_text ? `<div class="summary">Zeta feel: ${esc(m.feel_text)}</div>` : ''}
        </article>
      `).join('');
    }
    async function load(mode) {
      localStorage.setItem('zeta-debug-token', tokenInput.value);
      statusEl.textContent = 'Loading...';
      resultsEl.innerHTML = '';
      injectionEl.value = '';
      const params = new URLSearchParams();
      if (mode === 'query') params.set('q', queryInput.value);
      const resp = await fetch('/debug/recall.json?' + params.toString(), {
        headers: {'x-api-key': tokenInput.value}
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || 'Request failed');
      render(data);
    }
    document.getElementById('run').onclick = () => load('query').catch(e => statusEl.textContent = e.message);
    document.getElementById('last').onclick = () => load('last').catch(e => statusEl.textContent = e.message);
    queryInput.addEventListener('keydown', e => { if (e.key === 'Enter') document.getElementById('run').click(); });
  </script>
</body>
</html>
""".strip()


try:
    import zeta_openai_gateway
    from zeta_hidden_memory_patch import apply_hidden_memory_patch

    apply_hidden_memory_patch(zeta_openai_gateway)
    app = zeta_openai_gateway.app

    async def debug_recall_page(request: Request) -> HTMLResponse:
        return HTMLResponse(_recall_debug_page())

    async def debug_recall_json(request: Request) -> JSONResponse:
        auth = _debug_authorize(request)
        if auth is not None:
            return auth
        if zeta_openai_gateway.gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)

        gateway = zeta_openai_gateway.gateway
        query = str(request.query_params.get("q") or "").strip()
        if not query:
            snapshot = getattr(gateway, "last_recall_debug", None)
            if not snapshot:
                return JSONResponse({"ok": True, "mode": "last", "count": 0, "memories": [], "injection_text": ""})
            return JSONResponse({"ok": True, "mode": "last", **snapshot})

        recalled = await gateway.memory_gateway.recall({
            "current_text": query,
            "recent_context": "",
            "max_results": gateway.recall_max_results,
            "keyword_limit": gateway.keyword_limit,
            "semantic_limit": gateway.semantic_limit,
        })
        memories = recalled.get("memories", []) if isinstance(recalled, dict) else []
        return JSONResponse({
            "ok": True,
            "mode": "query",
            "query": recalled.get("query", query) if isinstance(recalled, dict) else query,
            "count": len(memories) if isinstance(memories, list) else 0,
            "memories": memories if isinstance(memories, list) else [],
            "injection_text": recalled.get("injection_text", "") if isinstance(recalled, dict) else "",
        })

    app.router.routes.append(Route("/debug/recall", debug_recall_page, methods=["GET"]))
    app.router.routes.append(Route("/debug/recall.json", debug_recall_json, methods=["GET"]))
except Exception as exc:
    logger.exception("Failed to import zeta_openai_gateway")
    app = _startup_error_app(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    port = _port()
    logger.info("Starting Zeabur gateway bootstrap on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
