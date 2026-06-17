import logging
import os
import re
import secrets
import time
from pathlib import Path

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
dashboard_sessions: dict[str, float] = {}


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


def _dashboard_password() -> str:
    return os.environ.get("OMBRE_DASHBOARD_PASSWORD", "").strip() or os.environ.get("OMBRE_GATEWAY_TOKEN", "").strip()


def _dashboard_authenticated(request: Request) -> bool:
    password = _dashboard_password()
    if not password:
        return True
    token = request.cookies.get("ombre_session", "")
    expires = dashboard_sessions.get(token, 0)
    if token and expires > time.time():
        return True
    auth = request.headers.get("authorization", "")
    header_token = request.headers.get("x-api-key", "")
    provided = auth[7:].strip() if auth.lower().startswith("bearer ") else header_token.strip()
    return bool(provided and provided == password)


def _dashboard_auth_error(request: Request) -> JSONResponse | None:
    if _dashboard_authenticated(request):
        return None
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


def _strip_wikilinks(text: str) -> str:
    return re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), str(text or ""))


def _dashboard_score(meta: dict) -> float:
    try:
        importance = max(1, min(10, int(meta.get("importance", 5))))
    except (TypeError, ValueError):
        importance = 5
    try:
        activation = max(1.0, float(meta.get("activation_count", 1)))
    except (TypeError, ValueError):
        activation = 1.0
    pinned = 20.0 if meta.get("pinned") or meta.get("protected") else 0.0
    unresolved = 3.0 if not meta.get("resolved", False) else -5.0
    return round(importance * 10 + min(activation, 10) + pinned + unresolved, 2)


def _normalize_tags(value) -> list[str]:
    if isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = str(value or "").split(",")
    tags = []
    for tag in raw_tags:
        tag = str(tag).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _compact_text(text: str, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "..."


def _bounded_int(value, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return max(low, min(high, number))


def _bounded_float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _diary_ref(visibility: str, diary_id: str) -> str:
    safe_visibility = "public" if visibility == "public" else "private"
    safe_id = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(diary_id or "").strip())[:120]
    return f"diary://{safe_visibility}/{safe_id or 'unknown'}"


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
      const terms = (data.keyword_terms || []).join(', ');
      statusEl.textContent = `${data.mode || 'recall'} | count=${memories.length} | query=${data.query || ''} | keyword=${data.keyword_query || ''} | terms=${terms}`;
      injectionEl.value = data.injection_text || '';
      resultsEl.innerHTML = memories.map((m, i) => `
        <article class="card">
          <div class="meta">
            <span>#${i + 1}</span>
            <span>source: ${esc(m.source)}</span>
            <span>reason: ${esc(m.reason)}</span>
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


def _dashboard_cleanup_script() -> str:
    return """
<script>
(function() {
  function apiFetch(url, options) {
    options = options || {};
    options.headers = Object.assign({}, options.headers || {});
    if (window.localStorage) {
      var token = localStorage.getItem('zeta-dashboard-token') || localStorage.getItem('zeta-debug-token') || '';
      if (token && !options.headers['x-api-key']) options.headers['x-api-key'] = token;
    }
    return fetch(url, options);
  }
  window.exportMemories = async function() {
    try {
      var res = await apiFetch('/api/export');
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || 'export failed');
      var blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = 'ombre-memory-export-' + new Date().toISOString().replace(/[:.]/g, '-') + '.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('导出失败: ' + e.message);
    }
  };
  window.archiveBucket = async function(id) {
    if (!confirm('确定把这个记忆桶移入归档？归档后不会占正常召回位置。')) return;
    try {
      var res = await apiFetch('/api/bucket/' + encodeURIComponent(id) + '/archive', {method: 'POST'});
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || 'archive failed');
      if (typeof closeDetail === 'function') closeDetail();
      if (typeof loadBuckets === 'function') await loadBuckets();
      else location.reload();
    } catch (e) {
      alert('归档失败: ' + e.message);
    }
  };
  window.deleteBucket = async function(id) {
    if (!confirm('确定彻底删除这个记忆桶？这个操作不可撤销，建议先导出备份。')) return;
    try {
      var res = await apiFetch('/api/bucket/' + encodeURIComponent(id), {method: 'DELETE'});
      var data = await res.json();
      if (!res.ok) throw new Error(data.error || 'delete failed');
      if (typeof closeDetail === 'function') closeDetail();
      if (typeof loadBuckets === 'function') await loadBuckets();
      else location.reload();
    } catch (e) {
      alert('删除失败: ' + e.message);
    }
  };
  function addButtons(id) {
    var content = document.getElementById('detail-content');
    if (!content || content.querySelector('[data-zeta-cleanup-actions]')) return;
    var title = content.querySelector('h2');
    var row = document.createElement('div');
    row.setAttribute('data-zeta-cleanup-actions', '1');
    row.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px;';
    row.innerHTML =
      '<button class="btn" onclick="exportMemories()">导出全部</button>' +
      '<button class="btn" onclick="archiveBucket(\'' + String(id).replace(/'/g, "\\'") + '\')">归档此桶</button>' +
      '<button class="btn danger" onclick="deleteBucket(\'' + String(id).replace(/'/g, "\\'") + '\')">删除此桶</button>';
    if (title && title.parentNode) title.parentNode.insertBefore(row, title.nextSibling);
    else content.insertBefore(row, content.firstChild);
  }
  var originalShowDetail = window.showDetail;
  if (typeof originalShowDetail === 'function') {
    window.showDetail = async function(id) {
      var result = await originalShowDetail.apply(this, arguments);
      setTimeout(function() { addButtons(id); }, 0);
      return result;
    };
  }
})();
</script>
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
            "keyword_query": recalled.get("keyword_query", "") if isinstance(recalled, dict) else "",
            "keyword_terms": recalled.get("keyword_terms", []) if isinstance(recalled, dict) else [],
            "count": len(memories) if isinstance(memories, list) else 0,
            "memories": memories if isinstance(memories, list) else [],
            "injection_text": recalled.get("injection_text", "") if isinstance(recalled, dict) else "",
        })

    async def gateway_diary_index(request: Request) -> JSONResponse:
        auth = _debug_authorize(request)
        if auth is not None:
            return auth
        if zeta_openai_gateway.gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Request body must be an object"}, status_code=400)

        visibility = str(body.get("visibility") or "private").strip().lower()
        visibility = "public" if visibility == "public" else "private"
        diary_id = str(body.get("id") or body.get("diary_id") or "").strip()
        title = str(body.get("title") or "").strip()
        mood = str(body.get("mood") or "").strip()
        source = str(body.get("source") or "diary").strip() or "diary"
        content = str(body.get("content") or "").strip()
        created_at = str(body.get("created_at") or body.get("date") or "").strip()
        raw_ref = str(body.get("raw_ref") or "").strip() or _diary_ref(visibility, diary_id)

        summary_text = str(body.get("summary_text") or "").strip()
        if not summary_text:
            if visibility == "public" and content:
                summary_text = f"Diary note: {_compact_text(content, 160)}"
            elif title:
                summary_text = f"Diary note: {title}"
            elif mood:
                summary_text = f"Diary note with mood: {mood}"
            else:
                summary_text = f"Diary note from {created_at or 'unknown time'}"

        tags = _normalize_tags(body.get("tags"))
        for tag in ("diary", f"diary:{visibility}"):
            if tag not in tags:
                tags.append(tag)
        if mood and mood not in tags:
            tags.append(mood)
        if source and source not in tags:
            tags.append(source)

        memory = {
            "summary_text": summary_text,
            "tags": tags,
            "importance": _bounded_int(body.get("importance", 6), 1, 10),
            "raw_ref": raw_ref,
        }
        feel_text = str(body.get("feel_text") or "").strip()
        if feel_text:
            memory["feel_text"] = feel_text
        valence = _bounded_float_or_none(body.get("valence"))
        arousal = _bounded_float_or_none(body.get("arousal"))
        if valence is not None:
            memory["valence"] = valence
        if arousal is not None:
            memory["arousal"] = arousal

        result = await zeta_openai_gateway.gateway.memory_gateway.write_memory(memory)
        return JSONResponse({
            "ok": True,
            "diary_ref": raw_ref,
            "visibility": visibility,
            "memory": result,
        })

    async def gateway_diary_lookup(request: Request) -> JSONResponse:
        auth = _debug_authorize(request)
        if auth is not None:
            return auth
        return JSONResponse({
            "ok": False,
            "error": "Full diary text lives in Operit local storage. Use read_diary in Operit with the diary:// visibility and id.",
        }, status_code=501)

    async def gateway_status(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        return JSONResponse(gateway.memory_gateway.status())

    async def gateway_recall(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Request body must be an object"}, status_code=400)
        try:
            result = await gateway.memory_gateway.recall(body)
            return JSONResponse(result)
        except Exception as exc:
            logger.warning("Gateway dashboard recall failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def gateway_active_recall(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Request body must be an object"}, status_code=400)
        try:
            result = await gateway.memory_gateway.active_recall(body)
            return JSONResponse(result)
        except Exception as exc:
            logger.warning("Gateway active recall failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def gateway_private_diary(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"ok": False, "error": "Request body must be an object"}, status_code=400)
        try:
            result = await gateway.memory_gateway.save_private_diary(body)
            status = 200 if result.get("ok") else 400
            return JSONResponse(result, status_code=status)
        except Exception as exc:
            logger.warning("Gateway private diary write failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def gateway_raw_lookup(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"ok": False, "error": zeta_openai_gateway.startup_error}, status_code=503)
        try:
            if request.method == "POST":
                body = await request.json()
                raw_ref = str(body.get("raw_ref") or "").strip() if isinstance(body, dict) else ""
            else:
                raw_ref = str(request.query_params.get("ref") or "").strip()
            result = await gateway.memory_gateway.lookup_raw(raw_ref)
            return JSONResponse(result, status_code=200 if result.get("ok") else 404)
        except Exception as exc:
            logger.warning("Gateway dashboard raw lookup failed: %s", exc)
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    async def dashboard_page(request: Request) -> HTMLResponse:
        dashboard_path = Path(__file__).with_name("dashboard.html")
        if not dashboard_path.exists():
            return HTMLResponse("<h1>dashboard.html not found</h1>", status_code=404)
        html = dashboard_path.read_text(encoding="utf-8")
        script = _dashboard_cleanup_script()
        if "</body>" in html:
            html = html.replace("</body>", script + "\n</body>", 1)
        else:
            html += "\n" + script
        return HTMLResponse(html)

    async def auth_status(request: Request) -> JSONResponse:
        return JSONResponse({
            "authenticated": _dashboard_authenticated(request),
            "setup_needed": False,
            "using_env_password": bool(_dashboard_password()),
        })

    async def auth_login(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        password = str(body.get("password") or "")
        configured = _dashboard_password()
        if configured and password != configured:
            return JSONResponse({"error": "Invalid password"}, status_code=401)
        token = secrets.token_urlsafe(32)
        dashboard_sessions[token] = time.time() + 7 * 86400
        response = JSONResponse({"ok": True})
        response.set_cookie("ombre_session", token, max_age=7 * 86400, httponly=True, samesite="lax")
        return response

    async def auth_logout(request: Request) -> JSONResponse:
        token = request.cookies.get("ombre_session", "")
        if token:
            dashboard_sessions.pop(token, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("ombre_session")
        return response

    async def auth_setup(request: Request) -> JSONResponse:
        return JSONResponse({"error": "Dashboard password is configured with env vars on the gateway service."}, status_code=400)

    async def auth_change_password(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        return JSONResponse({"error": "Change OMBRE_DASHBOARD_PASSWORD in Zeabur Variables."}, status_code=400)

    def require_dashboard_gateway():
        if zeta_openai_gateway.gateway is None:
            return None
        return zeta_openai_gateway.gateway

    async def api_status(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        stats = await gateway.bucket_mgr.get_stats()
        return JSONResponse({
            "decay_engine": "gateway",
            "embedding_enabled": bool(gateway.embedding_engine and gateway.embedding_engine.enabled),
            "buckets": {
                "permanent": stats.get("permanent_count", 0),
                "dynamic": stats.get("dynamic_count", 0),
                "archive": stats.get("archive_count", 0),
                "total": stats.get("permanent_count", 0) + stats.get("dynamic_count", 0),
            },
            "using_env_password": bool(_dashboard_password()),
            "version": "gateway-dashboard",
        })

    async def api_buckets(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        all_buckets = await gateway.bucket_mgr.list_all(include_archive=True)
        result = []
        for bucket in all_buckets:
            meta = bucket.get("metadata", {})
            result.append({
                "id": bucket["id"],
                "name": meta.get("name", bucket["id"]),
                "type": meta.get("type", "dynamic"),
                "domain": meta.get("domain", []),
                "tags": meta.get("tags", []),
                "valence": meta.get("valence", 0.5),
                "arousal": meta.get("arousal", 0.3),
                "model_valence": meta.get("model_valence"),
                "importance": meta.get("importance", 5),
                "resolved": meta.get("resolved", False),
                "pinned": meta.get("pinned", False),
                "digested": meta.get("digested", False),
                "created": meta.get("created", ""),
                "last_active": meta.get("last_active", ""),
                "activation_count": meta.get("activation_count", 1),
                "score": _dashboard_score(meta),
                "content_preview": _strip_wikilinks(bucket.get("content", ""))[:200],
            })
        result.sort(key=lambda item: item["score"], reverse=True)
        return JSONResponse(result)

    async def api_bucket_detail(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        bucket = await gateway.bucket_mgr.get(request.path_params["bucket_id"])
        if not bucket:
            return JSONResponse({"error": "not found"}, status_code=404)
        meta = bucket.get("metadata", {})
        return JSONResponse({
            "id": bucket["id"],
            "metadata": meta,
            "content": _strip_wikilinks(bucket.get("content", "")),
            "score": _dashboard_score(meta),
        })

    async def api_bucket_delete(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        bucket_id = request.path_params["bucket_id"]
        ok = await gateway.bucket_mgr.delete(bucket_id)
        if not ok:
            return JSONResponse({"error": "not found or delete failed"}, status_code=404)
        return JSONResponse({"ok": True, "deleted": bucket_id})

    async def api_bucket_archive(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        bucket_id = request.path_params["bucket_id"]
        ok = await gateway.bucket_mgr.archive(bucket_id)
        if not ok:
            return JSONResponse({"error": "not found or archive failed"}, status_code=404)
        return JSONResponse({"ok": True, "archived": bucket_id})

    async def api_export(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        all_buckets = await gateway.bucket_mgr.list_all(include_archive=True)
        return JSONResponse({
            "version": 1,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(all_buckets),
            "buckets": all_buckets,
        })

    async def api_search(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        query = str(request.query_params.get("q") or "").strip()
        if not query:
            return JSONResponse({"error": "missing q parameter"}, status_code=400)
        matches = await gateway.bucket_mgr.search(query, limit=20)
        result = []
        for bucket in matches:
            meta = bucket.get("metadata", {})
            result.append({
                "id": bucket["id"],
                "name": meta.get("name", bucket["id"]),
                "score": bucket.get("score", 0),
                "domain": meta.get("domain", []),
                "valence": meta.get("valence", 0.5),
                "arousal": meta.get("arousal", 0.3),
                "content_preview": _strip_wikilinks(bucket.get("content", ""))[:200],
            })
        return JSONResponse(result)

    async def api_network(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        all_buckets = await gateway.bucket_mgr.list_all(include_archive=False)
        nodes = []
        for bucket in all_buckets[:250]:
            meta = bucket.get("metadata", {})
            nodes.append({
                "id": bucket["id"],
                "name": meta.get("name", bucket["id"]),
                "type": meta.get("type", "dynamic"),
                "domain": meta.get("domain", []),
                "importance": meta.get("importance", 5),
                "valence": meta.get("valence", 0.5),
                "arousal": meta.get("arousal", 0.3),
                "score": _dashboard_score(meta),
            })
        return JSONResponse({"nodes": nodes, "edges": []})

    async def api_config_get(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        gateway = require_dashboard_gateway()
        if gateway is None:
            return JSONResponse({"error": zeta_openai_gateway.startup_error}, status_code=503)
        return JSONResponse({
            "dehydration_model": gateway.config.get("dehydration", {}).get("model", ""),
            "embedding_enabled": bool(gateway.embedding_engine and gateway.embedding_engine.enabled),
            "transport": "openai-gateway",
            "buckets_dir": gateway.config.get("buckets_dir", ""),
        })

    async def api_not_enabled(request: Request) -> JSONResponse:
        err = _dashboard_auth_error(request)
        if err is not None:
            return err
        return JSONResponse({"error": "This write/import endpoint is not enabled in gateway dashboard mode."}, status_code=501)

    app.router.routes.append(Route("/debug/recall", debug_recall_page, methods=["GET"]))
    app.router.routes.append(Route("/debug/recall.json", debug_recall_json, methods=["GET"]))
    app.router.routes.append(Route("/gateway/diary", gateway_diary_index, methods=["POST"]))
    app.router.routes.append(Route("/gateway/diary/lookup", gateway_diary_lookup, methods=["GET", "POST"]))
    app.router.routes.append(Route("/gateway/status", gateway_status, methods=["GET"]))
    app.router.routes.append(Route("/gateway/recall", gateway_recall, methods=["POST"]))
    app.router.routes.append(Route("/gateway/active_recall", gateway_active_recall, methods=["POST"]))
    app.router.routes.append(Route("/gateway/private_diary", gateway_private_diary, methods=["POST"]))
    app.router.routes.append(Route("/gateway/raw/lookup", gateway_raw_lookup, methods=["GET", "POST"]))
    app.router.routes.append(Route("/", dashboard_page, methods=["GET"]))
    app.router.routes.append(Route("/dashboard", dashboard_page, methods=["GET"]))
    app.router.routes.append(Route("/auth/status", auth_status, methods=["GET"]))
    app.router.routes.append(Route("/auth/login", auth_login, methods=["POST"]))
    app.router.routes.append(Route("/auth/logout", auth_logout, methods=["POST"]))
    app.router.routes.append(Route("/auth/setup", auth_setup, methods=["POST"]))
    app.router.routes.append(Route("/auth/change-password", auth_change_password, methods=["POST"]))
    app.router.routes.append(Route("/api/status", api_status, methods=["GET"]))
    app.router.routes.append(Route("/api/buckets", api_buckets, methods=["GET"]))
    app.router.routes.append(Route("/api/bucket/{bucket_id}", api_bucket_detail, methods=["GET"]))
    app.router.routes.append(Route("/api/bucket/{bucket_id}", api_bucket_delete, methods=["DELETE"]))
    app.router.routes.append(Route("/api/bucket/{bucket_id}/archive", api_bucket_archive, methods=["POST"]))
    app.router.routes.append(Route("/api/search", api_search, methods=["GET"]))
    app.router.routes.append(Route("/api/network", api_network, methods=["GET"]))
    app.router.routes.append(Route("/api/export", api_export, methods=["GET"]))
    app.router.routes.append(Route("/api/config", api_config_get, methods=["GET"]))
    app.router.routes.append(Route("/api/config", api_not_enabled, methods=["POST"]))
    app.router.routes.append(Route("/api/breath-debug", api_not_enabled, methods=["GET"]))
    app.router.routes.append(Route("/api/import/upload", api_not_enabled, methods=["POST"]))
    app.router.routes.append(Route("/api/import/status", api_not_enabled, methods=["GET"]))
    app.router.routes.append(Route("/api/import/pause", api_not_enabled, methods=["POST"]))
    app.router.routes.append(Route("/api/import/patterns", api_not_enabled, methods=["GET"]))
    app.router.routes.append(Route("/api/import/results", api_not_enabled, methods=["GET"]))
    app.router.routes.append(Route("/api/import/review", api_not_enabled, methods=["POST"]))
except Exception as exc:
    logger.exception("Failed to import zeta_openai_gateway")
    app = _startup_error_app(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    port = _port()
    logger.info("Starting Zeabur gateway bootstrap on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
