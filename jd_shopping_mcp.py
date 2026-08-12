from __future__ import annotations

import hmac
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from jd_shopping_gateway import JdShoppingBroker, SEARCH_TOOL, SUBMIT_ORDER_TOOL


class GatewayTokenMiddleware:
    """Protect the shopping MCP endpoint with Ombre's existing gateway token."""

    def __init__(self, app: ASGIApp, gateway_token: str) -> None:
        self.app = app
        self.router = getattr(app, "router", None)
        self.gateway_token = str(gateway_token or "").strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if not self.gateway_token:
                response = JSONResponse(
                    {"error": {"message": "Gateway token is not configured", "type": "server_error"}},
                    status_code=503,
                )
                await response(scope, receive, send)
                return
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            authorization = headers.get("authorization", "")
            provided = (
                authorization[7:].strip()
                if authorization.lower().startswith("bearer ")
                else headers.get("x-api-key", "").strip()
            )
            if not provided or not hmac.compare_digest(provided, self.gateway_token):
                response = JSONResponse(
                    {"error": {"message": "Unauthorized", "type": "invalid_api_key"}},
                    status_code=401,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_jd_shopping_mcp(
    broker: JdShoppingBroker,
    gateway_token: str,
) -> tuple[MCPServer, ASGIApp]:
    """Expose the JD broker as an ordinary, user-manageable MCP server."""

    server = MCPServer(
        name="ombre-jd-shopping",
        title="Ombre 京东购物",
        description="通过用户电脑上的京东执行器搜索商品并在本地授权额度内提交订单。",
        version="0.3.0",
    )

    @server.tool(
        name=SEARCH_TOOL,
        title="搜索京东商品",
        description="根据当前对话拟定一到四个搜索词，并返回京东真实候选。提交订单前必须先调用此工具。",
    )
    async def search_jd_products(
        queries: Annotated[list[str], Field(min_length=1, max_length=4)],
        budgetCny: Annotated[float, Field(ge=10, le=5000)],
        maxCandidates: Annotated[int, Field(ge=4, le=30)] = 20,
    ) -> CallToolResult:
        result = await broker.call(
            SEARCH_TOOL,
            {
                "queries": queries,
                "budgetCny": budgetCny,
                "maxCandidates": maxCandidates,
            },
        )
        return CallToolResult.model_validate(result)

    @server.tool(
        name=SUBMIT_ORDER_TOOL,
        title="提交授权内的京东订单",
        description="从本次真实搜索候选中选择一个 SKU，并交给本地执行器按已有额度、品类和防重复规则处理。",
    )
    async def submit_authorized_jd_order(
        searchTaskId: Annotated[str, Field(min_length=8, max_length=80)],
        sku: Annotated[str, Field(pattern=r"^\d{5,24}$")],
        budgetCny: Annotated[float, Field(ge=10, le=5000)],
    ) -> CallToolResult:
        result = await broker.call(
            SUBMIT_ORDER_TOOL,
            {
                "searchTaskId": searchTaskId,
                "sku": sku,
                "budgetCny": budgetCny,
            },
        )
        return CallToolResult.model_validate(result)

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server, GatewayTokenMiddleware(app, gateway_token)
