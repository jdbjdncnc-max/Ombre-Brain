import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from zeta_gateway import ZetaMemoryGateway


class RecallEmbeddingBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_hybrid_keyword_expansion_uses_one_vector_request(self):
        gateway = object.__new__(ZetaMemoryGateway)
        gateway.include_legacy = True
        gateway.natural_limit = 0
        gateway.embedding_engine = SimpleNamespace(
            enabled=True,
            search_similar=AsyncMock(return_value=[]),
        )
        gateway.bucket_mgr = SimpleNamespace(
            list_all=AsyncMock(return_value=[]),
            search=AsyncMock(return_value=[]),
        )
        gateway._content_search = AsyncMock(return_value=[])
        gateway._natural_float = AsyncMock(return_value=[])

        await gateway._hybrid_recall_buckets(
            "Duetto 一起听歌 夜曲 周杰伦 此刻 心情",
            keyword_limit=6,
            semantic_limit=1,
            max_results=6,
        )

        gateway.embedding_engine.search_similar.assert_awaited_once()
        gateway.bucket_mgr.list_all.assert_awaited_once_with(include_archive=False)
        self.assertGreater(gateway.bucket_mgr.search.await_count, 1)
        for call in gateway.bucket_mgr.search.await_args_list:
            self.assertIs(call.kwargs.get("use_embedding"), False)
            self.assertIsNotNone(call.kwargs.get("candidate_buckets"))


if __name__ == "__main__":
    unittest.main()
