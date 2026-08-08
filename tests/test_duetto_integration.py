import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from starlette.requests import Request

from solo.duetto import (
    BOOK_NOTE_CREATED,
    DUETTO_APPRAISAL_SYSTEM_PROMPT,
    MUSIC_PLAYED,
    normalize_duetto_event,
)
from solo.emotion_model import default_channels
from solo.service import SoloService
from zeta_openai_gateway import ZetaOpenAIGateway


def _request(path, payload, token="secret"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }, receive)


def _book_event(event_id="book-note:7"):
    return {
        "specversion": "1.0",
        "id": event_id,
        "source": "urn:duetto:test",
        "type": BOOK_NOTE_CREATED,
        "subject": "book/book-1/note/7",
        "time": "2026-08-08T02:00:00Z",
        "data": {
            "actor": "user",
            "book": {"id": "book-1", "title": "测试共读书", "author": "作者"},
            "note": {
                "id": "7",
                "block_idx": 4,
                "passage": "我们在同一页。",
                "text": "这句话让我想到你，我想把它留给我们。",
                "parent_id": 0,
            },
        },
    }


class DuettoEventNormalizationTests(unittest.TestCase):
    def test_bounds_supported_event_and_rejects_unknown_type(self):
        event = normalize_duetto_event(_book_event())
        self.assertEqual(event["type"], BOOK_NOTE_CREATED)
        self.assertEqual(event["data"]["actor"], "user")
        self.assertIn("留给我们", event["data"]["note"]["text"])

        invalid = _book_event("bad")
        invalid["type"] = "com.duetto.unknown.v1"
        with self.assertRaises(ValueError):
            normalize_duetto_event(invalid)


class DuettoSoloServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = SoloService(Path(self.temporary.name), enabled=True, jitter_ratio=0)
        self.now = datetime(2026, 8, 8, 2, 0, tzinfo=timezone.utc)
        self.service.solo_dir.mkdir(parents=True, exist_ok=True)
        runtime = self.service._new_state(self.now)
        runtime["lastUserMessageAt"] = (self.now - timedelta(hours=6)).isoformat()
        self.service._write_json(self.service.state_path, runtime)
        emotion = self.service._new_emotion_state(self.now, self.now - timedelta(hours=6))
        self.service._write_json(self.service.emotion_path, emotion)

    async def asyncTearDown(self):
        await self.service.stop()
        self.temporary.cleanup()

    async def test_note_updates_emotion_presence_and_trajectory_once(self):
        event = normalize_duetto_event(_book_event())
        before = self.service._read_json(self.service.emotion_path)["channels"]["delight"]
        result = await self.service.apply_duetto_event(
            event,
            appraisal={
                "emotion_deltas": {"delight": 7, "kinship": 5},
                "reason": "她把这句话留给了我们",
                "felt": "我更开心，也更有亲近感",
                "confidence": 0.9,
            },
            now=self.now,
        )
        duplicate = await self.service.apply_duetto_event(event, now=self.now)

        self.assertTrue(result["ok"])
        self.assertTrue(duplicate["duplicate"])
        emotion = self.service._read_json(self.service.emotion_path)
        self.assertEqual(emotion["channels"]["delight"], before + 7)
        self.assertEqual(emotion["budget"]["llmCalls"], 1)
        runtime = self.service._read_json(self.service.state_path)
        self.assertEqual(runtime["mode"]["key"], "reading_book")
        self.assertEqual(runtime["lastUserMessageAt"], "2026-08-08T02:00:00Z")
        activities = self.service._read_jsonl(self.service.activities_path, limit=10)
        self.assertEqual(len(activities), 1)
        self.assertIn("页边写了批注", activities[0]["title"])
        self.assertEqual(activities[0]["evidence"]["provider"], "Duetto")

    async def test_music_event_records_without_mechanical_emotion_delta(self):
        event = normalize_duetto_event({
            "specversion": "1.0",
            "id": "music-play:1",
            "source": "urn:duetto:test",
            "type": MUSIC_PLAYED,
            "time": "2026-08-08T02:00:00Z",
            "data": {"actor": "ai", "song": {"id": "1", "title": "夜曲", "artist": "周杰伦"}},
        })
        result = await self.service.apply_duetto_event(event, now=self.now)
        self.assertEqual(result["applied"], {})
        activity = self.service._read_jsonl(self.service.activities_path, limit=1)[0]
        self.assertIn("我在 Duetto 放了《夜曲》", activity["title"])


class DuettoGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_book_context_combines_solitude_state_and_recalled_memory(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.gateway_token = "secret"
        gateway.recall_max_results = 5
        gateway.keyword_limit = 4
        gateway.semantic_limit = 1
        gateway.memory_gateway = SimpleNamespace(recall=AsyncMock(return_value={
            "memories": [{"id": "m1"}],
            "injection_text": "她以前也标过这一句。",
        }))
        gateway.solo = SimpleNamespace(model_context_text=lambda **_kwargs: "情绪：好奇 68；主情绪：好奇")
        gateway._log_recall = lambda *_args: None

        response = await gateway.duetto_context(_request("/api/duetto/context", {
            "kind": "book",
            "message": "我想在这里写批注",
            "book": {"id": "book-1", "title": "测试共读书", "author": "作者", "chapter_title": "第一章"},
            "user": "Eve",
            "ai": "Zeta",
        }))

        body = json.loads(response.body)
        self.assertTrue(body["solitude_state"])
        self.assertIn("情绪：好奇 68", body["context"])
        self.assertIn("她以前也标过这一句", body["context"])
        recall_payload = gateway.memory_gateway.recall.await_args.args[0]
        self.assertIn("正在一起读书：《测试共读书》", recall_payload["current_text"])

    async def test_user_note_reuses_summary_provider_for_semantic_appraisal(self):
        gateway = object.__new__(ZetaOpenAIGateway)
        gateway.gateway_token = "secret"
        gateway.summary_chat_url = "https://summary.example/v1/chat/completions"
        gateway.summary_api_key = "summary-secret"
        gateway.summary_model = "summary-model"
        gateway.summary_timeout = 30
        gateway.openrouter_site_url = ""
        gateway.openrouter_app_name = ""
        gateway.solo = SimpleNamespace(
            enabled=True,
            duetto_event_seen=AsyncMock(return_value=False),
            appraisal_snapshot=lambda: {"channels": default_channels(), "dimensions": {}},
            apply_duetto_event=AsyncMock(return_value={
                "ok": True, "duplicate": False, "applied": {"delight": 6}, "activity_id": "act_duetto_1",
            }),
        )
        gateway.http = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({
                "emotion_deltas": {"delight": 6},
                "reason": "她把这句话留给了我们",
                "felt": "我更开心了一点",
                "confidence": 0.9,
            }, ensure_ascii=False)}}]},
            request=httpx.Request("POST", "https://summary.example/v1/chat/completions"),
        )))

        response = await gateway.duetto_event(_request("/api/duetto/events", _book_event()))

        body = json.loads(response.body)
        self.assertTrue(body["ok"])
        request_payload = gateway.http.post.await_args.kwargs["json"]
        self.assertEqual(request_payload["messages"][0]["content"], DUETTO_APPRAISAL_SYSTEM_PROMPT)
        self.assertIn("留给我们", request_payload["messages"][1]["content"])
        appraisal = gateway.solo.apply_duetto_event.await_args.kwargs["appraisal"]
        self.assertEqual(appraisal["emotion_deltas"]["delight"], 6)


if __name__ == "__main__":
    unittest.main()
