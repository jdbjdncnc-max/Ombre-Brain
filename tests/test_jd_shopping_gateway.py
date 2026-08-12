import asyncio
import shutil
import unittest
import uuid
from pathlib import Path

from jd_shopping_gateway import (
    SEARCH_TOOL,
    SUBMIT_ORDER_TOOL,
    JdShoppingBroker,
)
from jd_shopping_mcp import build_jd_shopping_mcp
from solo.mcp_bridge import SoloMcpBridge
from starlette.testclient import TestClient


def candidate(sku="100123456789", price=199.0):
    return {
        "sku": sku,
        "title": "京东自营桌面氛围灯",
        "priceCny": price,
        "url": f"https://item.jd.com/{sku}.html",
        "shop": "京东自营",
        "reviews": "10万+评价",
    }


class JdShoppingBrokerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = Path(__file__).parent / "_jd_shopping_tmp" / uuid.uuid4().hex
        self.temporary.mkdir(parents=True, exist_ok=True)
        self.broker = JdShoppingBroker(
            self.temporary,
            enabled=True,
            worker_token="worker-secret",
            max_budget_cny=500,
            task_timeout_seconds=5,
            worker_ttl_seconds=45,
        )

    def tearDown(self):
        shutil.rmtree(self.temporary, ignore_errors=True)

    async def _online(self):
        await self.broker.heartbeat({"workerId": "pc-1", "loggedIn": True, "version": "0.2.0"})

    async def _finish_next(self, result):
        claim = await self.broker.claim({"workerId": "pc-1"})
        self.assertIsNotNone(claim["task"])
        await self.broker.complete(claim["task"]["id"], {
            "workerId": "pc-1",
            "result": result,
        })
        return claim["task"]

    def test_worker_auth_is_separate_and_constant_time_compatible(self):
        self.assertTrue(self.broker.authorize_worker({"authorization": "Bearer worker-secret"}))
        self.assertTrue(self.broker.authorize_worker({"x-api-key": "worker-secret"}))
        self.assertFalse(self.broker.authorize_worker({"authorization": "Bearer wrong"}))

    async def test_offline_worker_fails_without_creating_a_task(self):
        result = await self.broker.call(SEARCH_TOOL, {"queries": ["桌面礼物"], "budgetCny": 300})
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "worker_offline")
        self.assertEqual(self.broker.status_snapshot()["tasks"]["queued"], 0)

    async def test_search_and_purchase_round_trip_through_local_worker(self):
        await self._online()
        search_call = asyncio.create_task(self.broker.call(SEARCH_TOOL, {
            "queries": ["桌面礼物", "有设计感的实用礼物"],
            "budgetCny": 300,
            "maxCandidates": 12,
        }))
        await asyncio.sleep(0.05)
        search_task = await self._finish_next({
            "candidates": [candidate(), candidate("100987654321", 259.0)]
        })
        search_result = await search_call
        self.assertFalse(search_result["isError"])
        self.assertEqual(search_result["structuredContent"]["searchTaskId"], search_task["id"])
        self.assertEqual(len(search_result["structuredContent"]["candidates"]), 2)

        purchase_call = asyncio.create_task(self.broker.call(SUBMIT_ORDER_TOOL, {
            "searchTaskId": search_task["id"],
            "sku": "100987654321",
            "budgetCny": 300,
        }))
        await asyncio.sleep(0.05)
        purchase_task = await self._finish_next({
            "ok": True,
            "status": "paid",
            "orderSubmitted": True,
            "paid": True,
            "needsVerification": False,
            "totalCny": 259.0,
            "title": "不应回传给模型的商品名",
        })
        purchase_result = await purchase_call
        self.assertEqual(purchase_task["kind"], "purchase")
        self.assertFalse(purchase_result["isError"])
        self.assertTrue(purchase_result["structuredContent"]["paid"])
        self.assertNotIn("title", purchase_result["structuredContent"])

    async def test_purchase_rejects_sku_outside_completed_search(self):
        await self._online()
        search_call = asyncio.create_task(self.broker.call(SEARCH_TOOL, {
            "queries": ["礼物"], "budgetCny": 300,
        }))
        await asyncio.sleep(0.05)
        search_task = await self._finish_next({"candidates": [candidate(), candidate("100987654321", 259)]})
        await search_call

        result = await self.broker.call(SUBMIT_ORDER_TOOL, {
            "searchTaskId": search_task["id"],
            "sku": "100555555555",
            "budgetCny": 300,
        })
        self.assertTrue(result["isError"])
        self.assertIn("不在刚才", result["structuredContent"]["message"])


class JdShoppingMcpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = Path(__file__).parent / "_jd_shopping_mcp_tmp" / uuid.uuid4().hex
        self.temporary.mkdir(parents=True, exist_ok=True)
        self.broker = JdShoppingBroker(
            self.temporary,
            enabled=True,
            worker_token="worker-secret",
            max_budget_cny=500,
        )

    def tearDown(self):
        shutil.rmtree(self.temporary, ignore_errors=True)

    async def test_server_exposes_ordinary_mcp_tools_without_special_prompt_rules(self):
        server, _app = build_jd_shopping_mcp(self.broker, "gateway-secret")
        tools = {
            tool.name: tool.model_dump(by_alias=True, exclude_none=True)
            for tool in await server.list_tools()
        }
        self.assertEqual(set(tools), {SEARCH_TOOL, SUBMIT_ORDER_TOOL})
        serialized = str(tools)
        self.assertNotIn("reasoning", serialized.lower())
        self.assertNotIn("严禁", serialized)

        submit = SoloMcpBridge._classify_tool(tools[SUBMIT_ORDER_TOOL])
        self.assertEqual(submit["kind"], "write")
        self.assertFalse(submit["hardBlocked"])

    def test_mcp_endpoint_requires_the_gateway_token(self):
        _server, app = build_jd_shopping_mcp(self.broker, "gateway-secret")
        with TestClient(app) as client:
            unauthorized = client.post("/mcp")
            authorized = client.post(
                "/mcp",
                headers={"x-api-key": "gateway-secret"},
            )
        self.assertEqual(unauthorized.status_code, 401)
        self.assertNotEqual(authorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
