import asyncio
import threading

import httpx
import uvicorn
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

import server
from zeta_gateway import ZetaMemoryGateway


zeta_gateway = ZetaMemoryGateway(server.config, server.bucket_mgr, server.embedding_engine)


@server.mcp.custom_route("/gateway/status", methods=["GET"])
async def gateway_status(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    return JSONResponse(zeta_gateway.status())


@server.mcp.custom_route("/gateway/raw", methods=["POST"])
async def gateway_save_raw(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        body = await request.json()
        result = await zeta_gateway.save_raw(body)
        return JSONResponse(result)
    except Exception as e:
        server.logger.warning(f"Zeta gateway raw save failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.mcp.custom_route("/gateway/memory", methods=["POST"])
async def gateway_write_memory(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        body = await request.json()
        result = await zeta_gateway.write_memory(body)
        return JSONResponse(result)
    except Exception as e:
        server.logger.warning(f"Zeta gateway memory write failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.mcp.custom_route("/gateway/recall", methods=["POST"])
async def gateway_recall(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        body = await request.json()
        result = await zeta_gateway.recall(body)
        return JSONResponse(result)
    except Exception as e:
        server.logger.warning(f"Zeta gateway recall failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.mcp.custom_route("/gateway/active_recall", methods=["POST"])
async def gateway_active_recall(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        body = await request.json()
        result = await zeta_gateway.active_recall(body)
        return JSONResponse(result)
    except Exception as e:
        server.logger.warning(f"Zeta gateway active recall failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.mcp.custom_route("/gateway/private_diary", methods=["POST"])
async def gateway_private_diary(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        body = await request.json()
        result = await zeta_gateway.save_private_diary(body)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except Exception as e:
        server.logger.warning(f"Zeta gateway private diary failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.mcp.custom_route("/gateway/raw/lookup", methods=["GET", "POST"])
async def gateway_lookup_raw(request):
    auth_error = zeta_gateway.require_auth(request)
    if auth_error is not None:
        return auth_error
    try:
        if request.method == "POST":
            body = await request.json()
            raw_ref = body.get("raw_ref", "")
        else:
            raw_ref = request.query_params.get("ref", "")
        result = await zeta_gateway.lookup_raw(raw_ref)
        return JSONResponse(result, status_code=200 if result.get("ok") else 404)
    except Exception as e:
        server.logger.warning(f"Zeta gateway raw lookup failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


def run():
    transport = server.config.get("transport", "stdio")
    server.logger.info(f"Ombre Brain with Zeta gateway starting | transport: {transport}")

    if transport in ("sse", "streamable-http"):
        async def _keepalive_loop():
            await asyncio.sleep(10)
            async with httpx.AsyncClient() as client:
                while True:
                    try:
                        await client.get(f"http://localhost:{server.OMBRE_PORT}/health", timeout=5)
                        server.logger.debug("Keepalive ping OK")
                    except Exception as e:
                        server.logger.warning(f"Keepalive ping failed: {e}")
                    await asyncio.sleep(60)

        def _start_keepalive():
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_keepalive_loop())

        threading.Thread(target=_start_keepalive, daemon=True).start()

        if transport == "streamable-http":
            app = server.mcp.streamable_http_app()
        else:
            app = server.mcp.sse_app()
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )
        uvicorn.run(app, host="0.0.0.0", port=server.OMBRE_PORT)
    else:
        server.mcp.run(transport=transport)


if __name__ == "__main__":
    run()
