"""Configurable MCP client bridge for chat and future solitude actions."""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client


MCP_CATEGORIES = ("forum", "peer", "game", "read", "search", "music", "misc")
MCP_AUTONOMY = ("full", "allowlist", "chat_only", "off")
MCP_TRANSPORTS = ("streamable-http", "sse", "stdio")
MCP_MASK = "••••••"
MCP_RESULT_CHAR_LIMIT = 8192
MCP_IDLE_SECONDS = 600
MCP_CALL_TIMEOUT = 30.0
MCP_JD_SHOPPING_CALL_TIMEOUT = 150.0

_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_PLACEHOLDER_RE = re.compile(r"\$\{([^{}]+)\}")
_SENSITIVE_NAME_RE = re.compile(
    r"(?:authorization|api[-_]?key|token|secret|password|passwd|cookie|credential)",
    flags=re.IGNORECASE,
)
_READ_HINTS = (
    "list", "get", "search", "read", "fetch", "query", "find", "lookup", "view", "inspect", "status",
)
_WRITE_HINTS = (
    "post", "create", "write", "send", "update", "edit", "reply", "comment", "publish", "submit", "move",
    "delete", "remove", "exec", "run", "pay", "transfer",
)
_CATEGORY_HINTS = {
    "forum": ("forum", "thread", "post", "reply", "comment", "community", "discussion"),
    "game": ("game", "match", "board", "move", "chess", "gomoku", "tic-tac-toe", "play"),
    "peer": ("peer", "agent", "model", "companion", "ai friend"),
    "music": ("music", "song", "playlist", "album", "track"),
    "search": ("search", "query", "lookup", "find"),
    "read": ("read", "fetch", "article", "document", "resource", "list", "get"),
}


def mcp_operation_timeout(
    server_name: str,
    operation: str,
    config: Mapping[str, Any] | None = None,
) -> float:
    """Give the browser-backed JD tool more time without slowing other MCPs."""
    url = str((config or {}).get("url") or "").lower()
    is_jd_shopping = (
        str(server_name or "").strip().lower() == "ombre-jd-shopping"
        or "/api/jd-shopping/" in url
    )
    if operation == "call_tool" and is_jd_shopping:
        return MCP_JD_SHOPPING_CALL_TIMEOUT
    return MCP_CALL_TIMEOUT


class McpConfigurationError(ValueError):
    pass


class McpPermissionError(ValueError):
    pass


class McpConnectionError(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any, limit: int = 300) -> str:
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _atomic_write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    if private:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


class _ServerWorker:
    """Own one MCP session in one task so its context exits in the same task."""

    def __init__(self, bridge: "SoloMcpBridge", name: str, config: dict[str, Any]) -> None:
        self.bridge = bridge
        self.name = name
        self.config = config
        self.queue: asyncio.Queue[tuple[str, Any, asyncio.Future[Any]]] = asyncio.Queue()
        self.task = asyncio.create_task(self._run(), name=f"ombre-mcp-{name}")

    async def request(self, operation: str, payload: Any = None, *, timeout: float = MCP_CALL_TIMEOUT) -> Any:
        if self.task.done():
            last_error = str(self.bridge._status.get(self.name, {}).get("lastError") or "")
            raise McpConnectionError(last_error or "MCP connection closed before the request started")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self.queue.put((operation, payload, future))
        if self.task.done() and not future.done():
            last_error = str(self.bridge._status.get(self.name, {}).get("lastError") or "")
            future.set_exception(
                McpConnectionError(last_error or "MCP connection closed before the request started")
            )
        return await asyncio.wait_for(asyncio.shield(future), timeout=max(1.0, timeout) + 8.0)

    async def close(self) -> None:
        if self.task.done():
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self.queue.put(("close", None, future))
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=8.0)
        except (TimeoutError, asyncio.CancelledError):
            self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            session = await asyncio.wait_for(
                self.bridge._open_session(stack, self.config),
                timeout=MCP_CALL_TIMEOUT,
            )
            self.bridge._mark_connected(self.name)
            while True:
                try:
                    operation, payload, future = await asyncio.wait_for(
                        self.queue.get(), timeout=MCP_IDLE_SECONDS
                    )
                except TimeoutError:
                    break
                if operation == "close":
                    if not future.done():
                        future.set_result(True)
                    break
                try:
                    operation_timeout = mcp_operation_timeout(self.name, operation, self.config)
                    if operation == "list_tools":
                        result = await asyncio.wait_for(session.list_tools(), timeout=MCP_CALL_TIMEOUT)
                    elif operation == "call_tool":
                        tool_name = str((payload or {}).get("name") or "")
                        arguments = (payload or {}).get("arguments")
                        result = await asyncio.wait_for(
                            session.call_tool(
                                tool_name,
                                arguments if isinstance(arguments, dict) else {},
                                read_timeout_seconds=operation_timeout,
                            ),
                            timeout=operation_timeout + 2.0,
                        )
                    elif operation == "ping":
                        result = await asyncio.wait_for(session.send_ping(), timeout=10.0)
                    else:
                        raise McpConnectionError(f"Unknown MCP operation: {operation}")
                    if not future.done():
                        future.set_result(result)
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.bridge._mark_failed(self.name, exc)
            while not self.queue.empty():
                _operation, _payload, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(McpConnectionError(_clean_text(exc, 500)))
        finally:
            try:
                await stack.aclose()
            except Exception as exc:
                self.bridge._mark_failed(self.name, exc)
            self.bridge._mark_disconnected(self.name)


class SoloMcpBridge:
    """Store MCP settings, discover capabilities, and execute authorized tools."""

    def __init__(
        self,
        solo_dir: str | Path,
        *,
        enabled: bool = False,
        discovery_ttl_hours: int = 24,
    ) -> None:
        self.solo_dir = Path(solo_dir)
        self.servers_path = self.solo_dir / "mcp_servers.json"
        self.capabilities_path = self.solo_dir / "mcp_capabilities.json"
        self.secrets_path = self.solo_dir / "mcp_secrets.json"
        self.enabled = bool(enabled)
        self.discovery_ttl = timedelta(hours=max(1, min(168, int(discovery_ttl_hours))))
        self._workers: dict[str, _ServerWorker] = {}
        self._workers_lock = asyncio.Lock()
        self._status: dict[str, dict[str, Any]] = {}

    async def close_all(self) -> None:
        async with self._workers_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            await worker.close()

    async def import_servers(self, payload: Any) -> dict[str, Any]:
        imported = self._parse_import(payload)
        document = self._servers_document()
        servers = document["mcpServers"]
        changed: list[str] = []
        for name, raw_config in imported.items():
            safe_name = self._server_name(name)
            existing = servers.get(safe_name) if isinstance(servers.get(safe_name), dict) else {}
            merged = {**deepcopy(existing), **deepcopy(raw_config)}
            servers[safe_name] = self._normalize_server(safe_name, merged)
            changed.append(safe_name)
        document["updatedAt"] = _iso_now()
        _atomic_write_json(self.servers_path, document)
        for name in changed:
            await self._close_server(name)
        return self.public_snapshot()

    async def delete_server(self, name: str) -> dict[str, Any]:
        safe_name = self._server_name(name)
        document = self._servers_document()
        existed = document["mcpServers"].pop(safe_name, None) is not None
        document["updatedAt"] = _iso_now()
        _atomic_write_json(self.servers_path, document)

        capabilities = self._capabilities_document()
        capabilities.pop(safe_name, None)
        _atomic_write_json(self.capabilities_path, capabilities)

        secrets = _read_json(self.secrets_path)
        secrets.pop(safe_name, None)
        _atomic_write_json(self.secrets_path, secrets, private=True)
        await self._close_server(safe_name)
        self._status.pop(safe_name, None)
        return {"ok": True, "deleted": existed, **self.public_snapshot()}

    async def set_secret(self, name: str, payload: Any) -> dict[str, Any]:
        safe_name = self._server_name(name)
        self._require_server(safe_name)
        body = payload if isinstance(payload, dict) else {}
        updates = body.get("secrets") if isinstance(body.get("secrets"), dict) else None
        if updates is None:
            updates = {body.get("key"): body.get("value")}
        secrets = _read_json(self.secrets_path)
        server_secrets = secrets.get(safe_name) if isinstance(secrets.get(safe_name), dict) else {}
        for raw_key, raw_value in updates.items():
            key = str(raw_key or "").strip()
            if not _SECRET_KEY_RE.fullmatch(key):
                raise McpConfigurationError("Secret key must use letters, numbers, dot, dash, or underscore")
            value = str(raw_value or "")
            if not value:
                server_secrets.pop(key, None)
            else:
                server_secrets[key] = value
        if server_secrets:
            secrets[safe_name] = server_secrets
        else:
            secrets.pop(safe_name, None)
        _atomic_write_json(self.secrets_path, secrets, private=True)
        await self._close_server(safe_name)
        return {"ok": True, "server": safe_name, "configured": sorted(server_secrets)}

    async def update_policy(self, name: str, payload: Any) -> dict[str, Any]:
        safe_name = self._server_name(name)
        body = payload if isinstance(payload, dict) else {}
        document = self._servers_document()
        config = self._require_server(safe_name, document)
        autonomy = str(body.get("autonomy") or config.get("autonomy") or "chat_only").strip()
        if autonomy not in MCP_AUTONOMY:
            raise McpConfigurationError(f"autonomy must be one of: {', '.join(MCP_AUTONOMY)}")
        categories = self._normalize_categories(body.get("categories", config.get("categories", [])))
        allowed_tools = body.get("allowedTools", config.get("allowedTools", []))
        if not isinstance(allowed_tools, list):
            raise McpConfigurationError("allowedTools must be an array")
        allowed = sorted({_clean_text(value, 160) for value in allowed_tools if _clean_text(value, 160)})

        capabilities = self._capabilities_document()
        server_capabilities = capabilities.get(safe_name) if isinstance(capabilities.get(safe_name), dict) else {}
        tools = server_capabilities.get("tools") if isinstance(server_capabilities.get("tools"), list) else []
        blocked = {
            str(tool.get("name") or ""): str(tool.get("blockedReason") or "此工具不能自主调用")
            for tool in tools if isinstance(tool, dict) and tool.get("hardBlocked")
        }
        forbidden = [tool for tool in allowed if tool in blocked]
        if forbidden:
            reasons = "；".join(f"{tool}：{blocked[tool]}" for tool in forbidden)
            raise McpPermissionError(reasons)

        config["autonomy"] = autonomy
        config["categories"] = categories
        config["allowedTools"] = allowed
        document["mcpServers"][safe_name] = config
        document["updatedAt"] = _iso_now()
        _atomic_write_json(self.servers_path, document)

        if server_capabilities:
            server_capabilities["categories"] = categories or server_capabilities.get("categories", ["misc"])
            if "categories" in body:
                server_capabilities["categorySource"] = "user"
            for tool in tools:
                if isinstance(tool, dict):
                    tool["allowed"] = str(tool.get("name") or "") in allowed and not bool(tool.get("hardBlocked"))
            capabilities[safe_name] = server_capabilities
            _atomic_write_json(self.capabilities_path, capabilities)
        return self.public_snapshot()

    async def discover(self, name: str, *, force: bool = False) -> dict[str, Any]:
        safe_name = self._server_name(name)
        config = self._require_server(safe_name)
        if not self.enabled:
            raise McpConnectionError("MCP is disabled. Set OMBRE_SOLO_MCP_ENABLED=1 in Zeabur first.")
        if not bool(config.get("enabled", True)) or config.get("autonomy") == "off":
            raise McpConnectionError("This MCP server is disabled")

        capabilities = self._capabilities_document()
        cached = capabilities.get(safe_name) if isinstance(capabilities.get(safe_name), dict) else {}
        if cached and not force and self._cache_is_fresh(cached.get("discoveredAt")):
            return deepcopy(cached)

        result = await self._request(safe_name, "list_tools")
        raw_tools = getattr(result, "tools", [])
        previous = {
            str(item.get("name") or ""): item
            for item in (cached.get("tools") if isinstance(cached.get("tools"), list) else [])
            if isinstance(item, dict)
        }
        configured_allowed = {
            str(value) for value in config.get("allowedTools", []) if str(value).strip()
        }
        tools: list[dict[str, Any]] = []
        category_votes: set[str] = set()
        for raw_tool in raw_tools:
            dumped = raw_tool.model_dump(by_alias=True, exclude_none=True) if hasattr(raw_tool, "model_dump") else raw_tool
            if not isinstance(dumped, dict):
                continue
            tool = self._classify_tool(dumped)
            old = previous.get(tool["name"], {})
            if old:
                tool["allowed"] = bool(old.get("allowed")) and not tool["hardBlocked"]
            elif tool["name"] in configured_allowed:
                tool["allowed"] = not tool["hardBlocked"]
            else:
                tool["allowed"] = tool["kind"] == "read" and not tool["hardBlocked"]
            category_votes.update(tool.pop("categoryHints", []))
            tools.append(tool)

        configured_categories = self._normalize_categories(config.get("categories", []))
        if configured_categories:
            categories = configured_categories
            category_source = "user"
        else:
            categories = [category for category in MCP_CATEGORIES if category in category_votes] or ["misc"]
            category_source = "auto"
        entry = {
            "discoveredAt": _iso_now(),
            "categories": categories,
            "categorySource": category_source,
            "tools": sorted(tools, key=lambda item: (item["kind"] != "read", item["name"])),
        }
        capabilities[safe_name] = entry
        _atomic_write_json(self.capabilities_path, capabilities)
        return deepcopy(entry)

    async def call(
        self,
        name: str,
        tool_name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        autonomous: bool = False,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        safe_name = self._server_name(name)
        config = self._require_server(safe_name)
        if not self.enabled:
            raise McpConnectionError("MCP is disabled")
        if not bool(config.get("enabled", True)) or config.get("autonomy") == "off":
            raise McpPermissionError("This MCP server is disabled")
        capabilities = await self.discover(safe_name)
        tools = {
            str(item.get("name") or ""): item
            for item in capabilities.get("tools", []) if isinstance(item, dict)
        }
        tool = tools.get(str(tool_name or ""))
        if tool is None:
            raise McpPermissionError("The requested MCP tool was not discovered")
        if tool.get("hardBlocked") and not user_confirmed:
            raise McpPermissionError(
                str(tool.get("blockedReason") or "This tool requires explicit user confirmation")
            )
        if autonomous:
            autonomy = str(config.get("autonomy") or "chat_only")
            if autonomy not in {"full", "allowlist"}:
                raise McpPermissionError("This server is only available while the user is present")
            if tool.get("hardBlocked"):
                raise McpPermissionError(str(tool.get("blockedReason") or "This tool cannot run autonomously"))
            if not tool.get("allowed"):
                raise McpPermissionError("This tool has not been authorized for autonomous use")
        clean_arguments = self._validated_arguments(tool.get("inputSchema"), arguments or {})
        result = await self._request(
            safe_name,
            "call_tool",
            {"name": tool_name, "arguments": clean_arguments},
        )
        self._increment_call_count(safe_name)
        return self._serialize_tool_result(result)

    def public_snapshot(self) -> dict[str, Any]:
        document = self._servers_document()
        capabilities = self._capabilities_document()
        secrets = _read_json(self.secrets_path)
        servers: list[dict[str, Any]] = []
        for name, config in sorted(document["mcpServers"].items()):
            if not isinstance(config, dict):
                continue
            cap = capabilities.get(name) if isinstance(capabilities.get(name), dict) else {}
            tools = cap.get("tools") if isinstance(cap.get("tools"), list) else []
            status = self._status.get(name, {})
            servers.append({
                "name": name,
                "enabled": bool(config.get("enabled", True)),
                "transport": str(config.get("transport") or ""),
                "endpoint": self._public_endpoint(config),
                "categories": deepcopy(cap.get("categories") or config.get("categories") or []),
                "categorySource": str(cap.get("categorySource") or ("user" if config.get("categories") else "auto")),
                "autonomy": str(config.get("autonomy") or "chat_only"),
                "allowedTools": deepcopy(config.get("allowedTools") or []),
                "credentialConfigured": bool(secrets.get(name)) or self._contains_placeholder(config),
                "credentialMask": MCP_MASK if (bool(secrets.get(name)) or self._contains_placeholder(config)) else "",
                "status": str(status.get("status") or "idle"),
                "connected": bool(status.get("connected")),
                "lastConnectedAt": str(status.get("lastConnectedAt") or ""),
                "lastError": str(status.get("lastError") or ""),
                "todayCalls": int(status.get("todayCalls") or 0),
                "discoveredAt": str(cap.get("discoveredAt") or ""),
                "toolCount": len(tools),
                "tools": deepcopy(tools),
            })
        return {
            "ok": True,
            "enabled": self.enabled,
            "hint": "" if self.enabled else "请先在 Zeabur 设置 OMBRE_SOLO_MCP_ENABLED=1",
            "servers": servers,
        }

    def chat_catalog(self) -> list[dict[str, Any]]:
        """Return tools explicitly authorized for use while the user is present."""

        return self._authorized_catalog(autonomous=False)

    def autonomous_catalog(self) -> list[dict[str, Any]]:
        """Return only tools that are currently authorized for solitude use."""

        return self._authorized_catalog(autonomous=True)

    def _authorized_catalog(self, *, autonomous: bool) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        document = self._servers_document()
        capabilities = self._capabilities_document()
        catalog: list[dict[str, Any]] = []
        for name, config in sorted(document["mcpServers"].items()):
            if not isinstance(config, dict) or not bool(config.get("enabled", True)):
                continue
            autonomy = str(config.get("autonomy") or "chat_only")
            if autonomy == "off":
                continue
            if autonomous and autonomy not in {"full", "allowlist"}:
                continue
            capability = capabilities.get(name) if isinstance(capabilities.get(name), dict) else {}
            raw_tools = capability.get("tools") if isinstance(capability.get("tools"), list) else []
            tools = [
                {
                    "name": str(tool.get("name") or "")[:160],
                    "desc": str(tool.get("desc") or "")[:600],
                    "kind": str(tool.get("kind") or "unknown")[:20],
                    "inputSchema": deepcopy(tool.get("inputSchema") or {"type": "object"}),
                }
                for tool in raw_tools
                if isinstance(tool, dict)
                and bool(tool.get("allowed"))
                and not bool(tool.get("hardBlocked"))
                and str(tool.get("name") or "").strip()
            ]
            if not tools:
                continue
            catalog.append({
                "name": name,
                "categories": deepcopy(
                    capability.get("categories") or config.get("categories") or ["misc"]
                ),
                "tools": tools,
            })
        return catalog

    def status_snapshot(self) -> dict[str, Any]:
        snapshot = self.public_snapshot()
        return {
            "ok": True,
            "enabled": snapshot["enabled"],
            "servers": [
                {
                    "name": item["name"],
                    "status": item["status"],
                    "connected": item["connected"],
                    "lastError": item["lastError"],
                    "todayCalls": item["todayCalls"],
                }
                for item in snapshot["servers"]
            ],
        }

    async def _open_session(self, stack: AsyncExitStack, config: dict[str, Any]) -> ClientSession:
        transport = str(config.get("transport") or "")
        if transport == "streamable-http":
            http_client = create_mcp_http_client(headers=deepcopy(config.get("headers") or {}))
            await stack.enter_async_context(http_client)
            streams = await stack.enter_async_context(
                streamable_http_client(str(config.get("url") or ""), http_client=http_client)
            )
            read_stream, write_stream = streams[0], streams[1]
        elif transport == "sse":
            streams = await stack.enter_async_context(
                sse_client(
                    str(config.get("url") or ""),
                    headers=deepcopy(config.get("headers") or {}),
                    timeout=10.0,
                    sse_read_timeout=MCP_IDLE_SECONDS + 60.0,
                )
            )
            read_stream, write_stream = streams[0], streams[1]
        elif transport == "stdio":
            parameters = StdioServerParameters(
                command=str(config.get("command") or ""),
                args=[str(value) for value in config.get("args", [])],
                env={**os.environ, **{str(key): str(value) for key, value in (config.get("env") or {}).items()}},
                cwd=str(config.get("cwd")) if config.get("cwd") else None,
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(parameters))
        else:
            raise McpConfigurationError(f"Unsupported MCP transport: {transport}")
        session = await stack.enter_async_context(
            ClientSession(read_stream, write_stream, read_timeout_seconds=MCP_CALL_TIMEOUT)
        )
        await session.initialize()
        return session

    async def _request(self, name: str, operation: str, payload: Any = None) -> Any:
        config = self._resolved_config(name)
        async with self._workers_lock:
            worker = self._workers.get(name)
            if worker is None or worker.task.done():
                worker = _ServerWorker(self, name, config)
                self._workers[name] = worker
        try:
            return await worker.request(
                operation,
                payload,
                timeout=mcp_operation_timeout(name, operation, config),
            )
        except Exception as exc:
            self._mark_failed(name, exc)
            raise McpConnectionError(_clean_text(exc, 500)) from exc

    async def _close_server(self, name: str) -> None:
        async with self._workers_lock:
            worker = self._workers.pop(name, None)
        if worker is not None:
            await worker.close()

    def _resolved_config(self, name: str) -> dict[str, Any]:
        config = deepcopy(self._require_server(name))
        secrets = _read_json(self.secrets_path)

        def replace(value: Any) -> Any:
            if isinstance(value, dict):
                return {str(key): replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if not isinstance(value, str):
                return value

            def resolve(match: re.Match[str]) -> str:
                token = match.group(1).strip()
                if token.startswith("secret:"):
                    reference = token[len("secret:"):]
                    server_name, separator, key = reference.partition(".")
                    if server_name != name:
                        raise McpConfigurationError(
                            f"MCP secret references must stay inside server {name}"
                        )
                    secret_values = secrets.get(server_name) if isinstance(secrets.get(server_name), dict) else {}
                    resolved = secret_values.get(key) if separator else None
                    if resolved is None:
                        raise McpConfigurationError(f"Missing MCP secret: {reference}")
                    return str(resolved)
                resolved = os.environ.get(token)
                if resolved is None:
                    raise McpConfigurationError(f"Missing environment variable: {token}")
                return resolved

            return _PLACEHOLDER_RE.sub(resolve, value)

        return replace(config)

    def _servers_document(self) -> dict[str, Any]:
        value = _read_json(self.servers_path)
        servers = value.get("mcpServers") if isinstance(value.get("mcpServers"), dict) else {}
        return {"version": 1, "updatedAt": str(value.get("updatedAt") or ""), "mcpServers": deepcopy(servers)}

    def _capabilities_document(self) -> dict[str, Any]:
        return deepcopy(_read_json(self.capabilities_path))

    def _require_server(self, name: str, document: dict[str, Any] | None = None) -> dict[str, Any]:
        servers = (document or self._servers_document())["mcpServers"]
        config = servers.get(name)
        if not isinstance(config, dict):
            raise McpConfigurationError(f"Unknown MCP server: {name}")
        return config

    @staticmethod
    def _parse_import(payload: Any) -> dict[str, dict[str, Any]]:
        value = payload
        if isinstance(value, dict) and isinstance(value.get("config"), str):
            value = value["config"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise McpConfigurationError(
                    f"JSON 第 {exc.lineno} 行、第 {exc.colno} 列有错误：{exc.msg}"
                ) from exc
        if not isinstance(value, dict):
            raise McpConfigurationError("MCP config must be a JSON object")
        servers = value.get("mcpServers")
        if servers is None and value and all(isinstance(item, dict) for item in value.values()):
            servers = value
        if not isinstance(servers, dict) or not servers:
            raise McpConfigurationError('Config must contain a non-empty "mcpServers" object')
        imported = {
            str(name): deepcopy(config)
            for name, config in servers.items()
            if isinstance(config, dict)
        }
        if not imported:
            raise McpConfigurationError('"mcpServers" must contain at least one server object')
        return imported

    def _normalize_server(self, name: str, raw: dict[str, Any]) -> dict[str, Any]:
        config = deepcopy(raw)
        raw_transport = str(config.get("transport") or config.get("type") or "").strip().lower()
        aliases = {
            "http": "streamable-http",
            "streamablehttp": "streamable-http",
            "streamable_http": "streamable-http",
            "streamable-http": "streamable-http",
            "sse": "sse",
            "stdio": "stdio",
        }
        transport = aliases.get(raw_transport)
        if not transport:
            transport = "stdio" if config.get("command") else "streamable-http" if config.get("url") else ""
        if transport not in MCP_TRANSPORTS:
            raise McpConfigurationError(f"{name}: unsupported or missing transport")
        config["transport"] = transport
        config.pop("type", None)
        config["enabled"] = bool(config.get("enabled", True))
        autonomy = str(config.get("autonomy") or "chat_only").strip()
        config["autonomy"] = autonomy if autonomy in MCP_AUTONOMY else "chat_only"
        config["categories"] = self._normalize_categories(config.get("categories", []))
        allowed = config.get("allowedTools") if isinstance(config.get("allowedTools"), list) else []
        config["allowedTools"] = sorted({_clean_text(value, 160) for value in allowed if _clean_text(value, 160)})
        if transport in {"streamable-http", "sse"}:
            url = str(config.get("url") or config.get("baseUrl") or "").strip()
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise McpConfigurationError(f"{name}: a valid http(s) url is required")
            if parsed.username or parsed.password:
                raise McpConfigurationError(f"{name}: credentials must not be stored in the URL")
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                if self._is_sensitive_config_key(key) and value and not _PLACEHOLDER_RE.search(value):
                    raise McpConfigurationError(
                        f"{name}: URL query parameter {key} cannot contain plaintext credentials"
                    )
            config["url"] = url
            config.pop("baseUrl", None)
            headers = config.get("headers") if isinstance(config.get("headers"), dict) else {}
            self._validate_sensitive_values(name, headers)
            config["headers"] = {str(key): str(value) for key, value in headers.items()}
        else:
            command = str(config.get("command") or "").strip()
            if not command:
                raise McpConfigurationError(f"{name}: stdio transport requires command")
            config["command"] = command
            config["args"] = [str(value) for value in config.get("args", [])] if isinstance(config.get("args"), list) else []
            env = config.get("env") if isinstance(config.get("env"), dict) else {}
            self._validate_sensitive_values(name, env)
            config["env"] = {str(key): str(value) for key, value in env.items()}
        self._validate_sensitive_tree(name, config)
        return config

    @classmethod
    def _validate_sensitive_tree(cls, name: str, value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if (
                    cls._is_sensitive_config_key(key)
                    and isinstance(item, str)
                    and item
                    and not _PLACEHOLDER_RE.search(item)
                ):
                    raise McpConfigurationError(
                        f"{name}: {key} cannot contain plaintext credentials; use a placeholder"
                    )
                cls._validate_sensitive_tree(name, item)
        elif isinstance(value, list):
            for item in value:
                cls._validate_sensitive_tree(name, item)

    @staticmethod
    def _is_sensitive_config_key(value: Any) -> bool:
        key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
        key = re.sub(r"[^a-zA-Z0-9]+", "_", key).strip("_").lower()
        exact = {
            "authorization", "api_key", "token", "access_token", "refresh_token",
            "bearer_token", "secret", "client_secret", "password", "passwd", "cookie",
            "credential",
        }
        suffixes = (
            "_authorization", "_api_key", "_access_token", "_refresh_token",
            "_bearer_token", "_client_secret", "_password", "_passwd",
        )
        return key in exact or key.endswith(suffixes)

    @staticmethod
    def _validate_sensitive_values(name: str, values: Mapping[str, Any]) -> None:
        for key, raw_value in values.items():
            value = str(raw_value or "")
            if _SENSITIVE_NAME_RE.search(str(key)) and value and not _PLACEHOLDER_RE.search(value):
                raise McpConfigurationError(
                    f"{name}: {key} cannot contain plaintext credentials; use ${{ENV_VAR}} or ${{secret:{name}.token}}"
                )

    @staticmethod
    def _normalize_categories(value: Any) -> list[str]:
        items = value if isinstance(value, list) else []
        return [category for category in MCP_CATEGORIES if category in {str(item).strip().lower() for item in items}]

    @staticmethod
    def _server_name(value: Any) -> str:
        name = str(value or "").strip()
        if not _SERVER_NAME_RE.fullmatch(name):
            raise McpConfigurationError("Server name must use letters, numbers, dot, dash, or underscore")
        return name

    @staticmethod
    def _classify_tool(raw: dict[str, Any]) -> dict[str, Any]:
        name = _clean_text(raw.get("name"), 160)
        description = _clean_text(raw.get("description"), 600)
        lowered = f"{name} {description}".lower()
        kind = "write" if any(hint in lowered for hint in _WRITE_HINTS) else "read" if any(
            hint in lowered for hint in _READ_HINTS
        ) else "unknown"
        blocked_reason = SoloMcpBridge._hard_block_reason(lowered)
        category_hints = [
            category for category, hints in _CATEGORY_HINTS.items()
            if any(hint in lowered for hint in hints)
        ]
        schema = raw.get("inputSchema") if isinstance(raw.get("inputSchema"), dict) else {"type": "object"}
        return {
            "name": name,
            "desc": description,
            "kind": kind,
            "allowed": False,
            "risk": "blocked" if blocked_reason else "write" if kind == "write" else "safe",
            "hardBlocked": bool(blocked_reason),
            "blockedReason": blocked_reason,
            "inputSchema": deepcopy(schema),
            "categoryHints": category_hints,
        }

    @staticmethod
    def _hard_block_reason(lowered: str) -> str:
        if any(term in lowered for term in ("delete", "remove data", "drop table", "destroy", "purge")):
            return "涉及删除数据，只能在对话中由用户确认后使用"
        if any(term in lowered for term in ("payment", "purchase", "pay money", "transfer money", "billing")):
            return "涉及支付或转账，不能自主调用"
        if any(term in lowered for term in ("execute command", "run command", "shell", "terminal", "arbitrary code", "deploy")):
            return "涉及命令执行或部署修改，不能自主调用"
        if any(term in lowered for term in ("send email", "send sms", "text message", "twilio")):
            return "涉及向第三方发送邮件或短信，不能自主调用"
        return ""

    @staticmethod
    def _validated_arguments(schema: Any, arguments: Mapping[str, Any]) -> dict[str, Any]:
        clean = deepcopy(dict(arguments))
        if not isinstance(schema, dict):
            return clean
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else None
        if properties is not None and schema.get("additionalProperties") is False:
            clean = {key: value for key, value in clean.items() if key in properties}
        errors = sorted(Draft202012Validator(schema).iter_errors(clean), key=lambda error: list(error.path))
        if errors:
            raise McpPermissionError(_clean_text(errors[0].message, 400))
        return clean

    @staticmethod
    def _serialize_tool_result(result: Any) -> dict[str, Any]:
        value = result.model_dump(by_alias=True, exclude_none=True) if hasattr(result, "model_dump") else result
        if not isinstance(value, dict):
            value = {"content": [{"type": "text", "text": str(value)}]}
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if len(raw) <= MCP_RESULT_CHAR_LIMIT:
            return value
        return {
            "isError": bool(value.get("isError")),
            "content": [{"type": "text", "text": raw[:MCP_RESULT_CHAR_LIMIT] + "…"}],
            "truncated": True,
        }

    @staticmethod
    def _public_endpoint(config: dict[str, Any]) -> str:
        if config.get("transport") == "stdio":
            return str(config.get("command") or "")
        parsed = urlsplit(str(config.get("url") or ""))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    @staticmethod
    def _contains_placeholder(value: Any) -> bool:
        if isinstance(value, dict):
            return any(SoloMcpBridge._contains_placeholder(item) for item in value.values())
        if isinstance(value, list):
            return any(SoloMcpBridge._contains_placeholder(item) for item in value)
        return isinstance(value, str) and bool(_PLACEHOLDER_RE.search(value))

    def _cache_is_fresh(self, value: Any) -> bool:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - parsed.astimezone(timezone.utc) < self.discovery_ttl

    def _mark_connected(self, name: str) -> None:
        current = self._status.setdefault(name, {})
        current.update({"status": "connected", "connected": True, "lastConnectedAt": _iso_now(), "lastError": ""})

    def _mark_failed(self, name: str, error: Any) -> None:
        current = self._status.setdefault(name, {})
        current.update({"status": "error", "connected": False, "lastError": _clean_text(error, 500)})

    def _mark_disconnected(self, name: str) -> None:
        current = self._status.setdefault(name, {})
        current["connected"] = False
        if current.get("status") != "error":
            current["status"] = "idle"

    def _increment_call_count(self, name: str) -> None:
        current = self._status.setdefault(name, {})
        today = datetime.now(timezone.utc).date().isoformat()
        if current.get("callDate") != today:
            current["callDate"] = today
            current["todayCalls"] = 0
        current["todayCalls"] = int(current.get("todayCalls") or 0) + 1
