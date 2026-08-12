from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


JD_SHOPPING_SERVER = "ombre-jd-shopping"
SEARCH_TOOL = "search_surprise_gift"
SUBMIT_ORDER_TOOL = "submit_authorized_jd_order"
_SKU_RE = re.compile(r"^\d{5,24}$")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return deepcopy(fallback)


def _atomic_write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    if private:
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _bounded_float(value: Any, *, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("金额必须是数字") from None
    if not low <= number <= high:
        raise ValueError(f"金额必须在 {low:g}～{high:g} 元之间")
    return round(number, 2)


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


class JdShoppingBroker:
    """Bridge the standard shopping MCP to a browser worker on the user's PC."""

    def __init__(
        self,
        base_dir: str | Path,
        *,
        enabled: bool = False,
        worker_token: str = "",
        max_budget_cny: float = 500.0,
        task_timeout_seconds: int = 100,
        worker_ttl_seconds: int = 45,
        lease_seconds: int = 180,
    ) -> None:
        self.base_dir = Path(base_dir) / "jd_shopping"
        self.tasks_dir = self.base_dir / "tasks"
        self.worker_path = self.base_dir / "worker_status.json"
        self.enabled = bool(enabled)
        self.worker_token = str(worker_token or "").strip()
        self.max_budget_cny = max(10.0, min(5000.0, float(max_budget_cny)))
        self.task_timeout_seconds = max(20, min(240, int(task_timeout_seconds)))
        self.worker_ttl_seconds = max(10, min(180, int(worker_ttl_seconds)))
        self.lease_seconds = max(60, min(600, int(lease_seconds)))
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.worker_token)

    def authorize_worker(self, headers: Mapping[str, str]) -> bool:
        if not self.configured:
            return False
        authorization = str(headers.get("authorization") or "")
        x_key = str(headers.get("x-api-key") or "")
        provided = authorization[7:].strip() if authorization.lower().startswith("bearer ") else x_key.strip()
        return bool(provided) and hmac.compare_digest(provided, self.worker_token)

    def status_snapshot(self) -> dict[str, Any]:
        worker = _read_json(self.worker_path, {})
        last_seen_epoch = float(worker.get("lastSeenEpoch") or 0)
        queued = claimed = completed = failed = 0
        for task in self._task_documents():
            status = str(task.get("status") or "")
            if status == "queued":
                queued += 1
            elif status == "claimed":
                claimed += 1
            elif status == "completed":
                completed += 1
            elif status == "failed":
                failed += 1
        return {
            "ok": True,
            "enabled": self.enabled,
            "configured": self.configured,
            "workerOnline": self._worker_online(last_seen_epoch),
            "workerId": _clean_text(worker.get("workerId"), 80),
            "lastSeenAt": str(worker.get("lastSeenAt") or ""),
            "maxBudgetCny": self.max_budget_cny,
            "effectiveBudgetCapCny": self._effective_budget_cap(),
            "tasks": {"queued": queued, "claimed": claimed, "completed": completed, "failed": failed},
        }

    async def heartbeat(self, body: Any) -> dict[str, Any]:
        payload = body if isinstance(body, dict) else {}
        worker_id = _clean_text(payload.get("workerId"), 80) or "windows-worker"
        document = {
            "workerId": worker_id,
            "lastSeenAt": _iso_now(),
            "lastSeenEpoch": time.time(),
            "version": _clean_text(payload.get("version"), 40),
            "loggedIn": bool(payload.get("loggedIn")),
            "perOrderCapCny": self._safe_worker_cap(payload.get("perOrderCapCny")),
        }
        async with self._lock:
            _atomic_write_json(self.worker_path, document)
        return {"ok": True, "workerId": worker_id, "serverTime": document["lastSeenAt"]}

    async def claim(self, body: Any) -> dict[str, Any]:
        payload = body if isinstance(body, dict) else {}
        worker_id = _clean_text(payload.get("workerId"), 80) or "windows-worker"
        now = time.time()
        async with self._lock:
            self._write_worker_seen(worker_id, payload)
            for task in self._task_documents():
                status = str(task.get("status") or "")
                lease_expired = status == "claimed" and now - float(task.get("claimedEpoch") or 0) > self.lease_seconds
                if status != "queued" and not lease_expired:
                    continue
                attempts = int(task.get("attempts") or 0)
                if attempts >= 2:
                    task.update({
                        "status": "failed",
                        "error": "本地购物助手两次领取任务后都没有完成",
                        "finishedAt": _iso_now(),
                    })
                    self._write_task(task)
                    continue
                task.update({
                    "status": "claimed",
                    "workerId": worker_id,
                    "claimedAt": _iso_now(),
                    "claimedEpoch": now,
                    "attempts": attempts + 1,
                })
                self._write_task(task)
                return {"ok": True, "task": self._public_task(task)}
        return {"ok": True, "task": None}

    async def complete(self, task_id: str, body: Any) -> dict[str, Any]:
        payload = body if isinstance(body, dict) else {}
        worker_id = _clean_text(payload.get("workerId"), 80)
        async with self._lock:
            task = self._read_task(task_id)
            if not task:
                raise ValueError("购物任务不存在")
            if task.get("status") not in {"claimed", "queued"}:
                return {"ok": True, "taskId": task_id, "status": task.get("status")}
            claimed_by = _clean_text(task.get("workerId"), 80)
            if claimed_by and worker_id != claimed_by:
                raise ValueError("购物任务属于另一台本地助手")
            error = _clean_text(payload.get("error"), 500)
            result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            if error:
                task.update({"status": "failed", "error": error, "result": {}, "finishedAt": _iso_now()})
            else:
                task.update({"status": "completed", "error": "", "result": result, "finishedAt": _iso_now()})
            self._write_task(task)
        return {"ok": True, "taskId": task_id, "status": task["status"]}

    async def call(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            return self._tool_result({"ok": False, "status": "disabled", "message": "京东购物工具尚未配置"}, error=True)
        if not self.status_snapshot()["workerOnline"]:
            return self._tool_result({
                "ok": False,
                "status": "worker_offline",
                "message": "她的本地购物助手没有在线；需要先打开电脑并运行购物助手。",
            }, error=True)
        args = dict(arguments or {})
        try:
            if tool_name == SEARCH_TOOL:
                return await self._search(args)
            if tool_name == SUBMIT_ORDER_TOOL:
                return await self._purchase(args)
            raise ValueError("未知的京东购物工具")
        except ValueError as exc:
            return self._tool_result({"ok": False, "status": "rejected", "message": str(exc)}, error=True)

    async def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        budget = _bounded_float(args.get("budgetCny"), low=10, high=self._effective_budget_cap())
        raw_queries = args.get("queries") if isinstance(args.get("queries"), list) else []
        queries: list[str] = []
        for value in raw_queries:
            query = _clean_text(value, 40)
            if not query or query in queries or "http://" in query.lower() or "https://" in query.lower():
                continue
            queries.append(query)
        if not queries:
            raise ValueError("至少需要一个有效的京东搜索词")
        max_candidates = max(4, min(30, int(args.get("maxCandidates") or 20)))
        task = await self._enqueue("search", {
            "queries": queries[:4],
            "budgetCny": budget,
            "maxCandidates": max_candidates,
        })
        finished = await self._wait(task["id"])
        if finished.get("status") != "completed":
            return self._tool_result({
                "ok": False,
                "status": finished.get("status"),
                "message": _clean_text(finished.get("error"), 500) or "本地京东搜索没有完成",
            }, error=True)
        candidates = self._safe_candidates(finished.get("result", {}).get("candidates"), budget, max_candidates)
        if len(candidates) < 2:
            return self._tool_result({
                "ok": False,
                "status": "insufficient_candidates",
                "message": "安全候选不足两件，请换一组搜索词再试。",
            }, error=True)
        return self._tool_result({
            "ok": True,
            "status": "candidates_ready",
            "searchTaskId": task["id"],
            "budgetCny": budget,
            "candidates": candidates,
        })

    async def _purchase(self, args: dict[str, Any]) -> dict[str, Any]:
        search_task_id = _clean_text(args.get("searchTaskId"), 80)
        sku = _clean_text(args.get("sku"), 24)
        budget = _bounded_float(args.get("budgetCny"), low=10, high=self._effective_budget_cap())
        if not search_task_id or not _SKU_RE.fullmatch(sku):
            raise ValueError("购买参数缺少有效的搜索任务或 SKU")
        search_task = self._read_task(search_task_id)
        if not search_task or search_task.get("kind") != "search" or search_task.get("status") != "completed":
            raise ValueError("只能购买刚才真实搜索完成的候选商品")
        search_budget = float(search_task.get("payload", {}).get("budgetCny") or 0)
        if budget > search_budget:
            raise ValueError("购买预算不能高于搜索时的授权上限")
        candidates = self._safe_candidates(search_task.get("result", {}).get("candidates"), search_budget, 30)
        candidate = next((item for item in candidates if item["sku"] == sku), None)
        if candidate is None:
            raise ValueError("所选 SKU 不在刚才的真实候选列表中")
        if float(candidate["priceCny"]) > budget:
            raise ValueError("所选商品标价超过本次预算")

        existing = self._find_purchase(search_task_id, sku)
        task = existing or await self._enqueue("purchase", {
            "searchTaskId": search_task_id,
            "sku": sku,
            "budgetCny": budget,
            "candidate": candidate,
        })
        finished = task if task.get("status") in {"completed", "failed"} else await self._wait(task["id"])
        if finished.get("status") != "completed":
            return self._tool_result({
                "ok": False,
                "status": finished.get("status"),
                "message": _clean_text(finished.get("error"), 500) or "本地购买任务没有完成",
            }, error=True)
        raw = finished.get("result") if isinstance(finished.get("result"), dict) else {}
        safe_result = {
            "ok": bool(raw.get("ok", True)),
            "status": _clean_text(raw.get("status"), 80) or "completed",
            "orderSubmitted": bool(raw.get("orderSubmitted")),
            "paid": bool(raw.get("paid")),
            "needsVerification": bool(raw.get("needsVerification")),
            "totalCny": raw.get("totalCny"),
            "message": _clean_text(raw.get("message"), 300),
        }
        return self._tool_result(safe_result, error=not safe_result["ok"])

    async def _enqueue(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        task = {
            "id": f"jd_{uuid.uuid4().hex}",
            "kind": kind,
            "status": "queued",
            "payload": deepcopy(payload),
            "result": {},
            "error": "",
            "createdAt": _iso_now(),
            "createdEpoch": time.time(),
            "attempts": 0,
        }
        async with self._lock:
            self._write_task(task)
        return task

    async def _wait(self, task_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.task_timeout_seconds
        while time.monotonic() < deadline:
            task = self._read_task(task_id)
            if task and task.get("status") in {"completed", "failed"}:
                return task
            await asyncio.sleep(0.35)
        async with self._lock:
            task = self._read_task(task_id) or {"id": task_id}
            if task.get("status") not in {"completed", "failed"}:
                task.update({"status": "failed", "error": "等待本地购物助手超时", "finishedAt": _iso_now()})
                self._write_task(task)
        return task

    def _find_purchase(self, search_task_id: str, sku: str) -> dict[str, Any] | None:
        for task in self._task_documents():
            payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            if task.get("kind") == "purchase" and payload.get("searchTaskId") == search_task_id and payload.get("sku") == sku:
                return task
        return None

    def _safe_candidates(self, value: Any, budget: float, limit: int) -> list[dict[str, Any]]:
        items = value if isinstance(value, list) else []
        safe: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            sku = _clean_text(raw.get("sku"), 24)
            title = _clean_text(raw.get("title"), 240)
            try:
                price = _bounded_float(raw.get("priceCny"), low=0.01, high=budget)
            except ValueError:
                continue
            url = _clean_text(raw.get("url"), 400)
            if not _SKU_RE.fullmatch(sku) or sku in seen or not title:
                continue
            if url != f"https://item.jd.com/{sku}.html":
                continue
            seen.add(sku)
            safe.append({
                "sku": sku,
                "title": title,
                "priceCny": price,
                "url": url,
                "shop": _clean_text(raw.get("shop"), 120),
                "reviews": _clean_text(raw.get("reviews"), 120),
            })
            if len(safe) >= limit:
                break
        return safe

    def _worker_online(self, last_seen_epoch: float | None = None) -> bool:
        if last_seen_epoch is None:
            worker = _read_json(self.worker_path, {})
            last_seen_epoch = float(worker.get("lastSeenEpoch") or 0)
        return bool(last_seen_epoch and time.time() - last_seen_epoch <= self.worker_ttl_seconds)

    def _write_worker_seen(self, worker_id: str, payload: dict[str, Any]) -> None:
        document = {
            "workerId": worker_id,
            "lastSeenAt": _iso_now(),
            "lastSeenEpoch": time.time(),
            "version": _clean_text(payload.get("version"), 40),
            "loggedIn": bool(payload.get("loggedIn")),
            "perOrderCapCny": self._safe_worker_cap(payload.get("perOrderCapCny")),
        }
        _atomic_write_json(self.worker_path, document)

    def _effective_budget_cap(self) -> float:
        worker = _read_json(self.worker_path, {})
        local_cap = self._safe_worker_cap(worker.get("perOrderCapCny"))
        return min(self.max_budget_cny, local_cap) if local_cap else self.max_budget_cny

    @staticmethod
    def _safe_worker_cap(value: Any) -> float | None:
        try:
            cap = round(float(value), 2)
        except (TypeError, ValueError):
            return None
        return cap if 10 <= cap <= 5000 else None

    def _task_path(self, task_id: str) -> Path:
        safe_id = _clean_text(task_id, 80)
        if not re.fullmatch(r"jd_[a-f0-9]{32}", safe_id):
            raise ValueError("购物任务 ID 无效")
        return self.tasks_dir / f"{safe_id}.json"

    def _read_task(self, task_id: str) -> dict[str, Any]:
        try:
            value = _read_json(self._task_path(task_id), {})
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def _write_task(self, task: dict[str, Any]) -> None:
        _atomic_write_json(self._task_path(str(task.get("id") or "")), task, private=True)

    def _task_documents(self) -> list[dict[str, Any]]:
        if not self.tasks_dir.exists():
            return []
        tasks: list[dict[str, Any]] = []
        for path in self.tasks_dir.glob("jd_*.json"):
            value = _read_json(path, {})
            if isinstance(value, dict) and value.get("id"):
                tasks.append(value)
        return sorted(tasks, key=lambda item: float(item.get("createdEpoch") or 0))

    @staticmethod
    def _public_task(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(task.get("id") or ""),
            "kind": str(task.get("kind") or ""),
            "payload": deepcopy(task.get("payload") or {}),
            "createdAt": str(task.get("createdAt") or ""),
            "attempts": int(task.get("attempts") or 0),
        }

    @staticmethod
    def _tool_result(payload: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": deepcopy(payload),
            "isError": bool(error),
        }
