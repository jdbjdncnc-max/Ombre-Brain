import logging
import os

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
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


try:
    from zeta_openai_gateway import app
except Exception as exc:
    logger.exception("Failed to import zeta_openai_gateway")
    app = _startup_error_app(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    port = _port()
    logger.info("Starting Zeabur gateway bootstrap on port %s", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
