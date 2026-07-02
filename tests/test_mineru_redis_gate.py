import unittest
from unittest.mock import MagicMock, patch

from app.rag.mineru_redis_gate import MinerUConcurrencyGate
from app.rag.mineru_ingest import reset_mineru_gate_for_tests


class _FakeRedisLock:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestMinerURedisGateRepair(unittest.TestCase):
    def setUp(self):
        reset_mineru_gate_for_tests()

    def test_repair_leaked_pool_when_initialized_but_empty(self):
        gate = MinerUConcurrencyGate(redis_url="redis://fake", max_concurrent=2, key_prefix="test:mineru")
        fake = MagicMock()
        fake.get.side_effect = lambda k: "1" if k == gate._init_key else "0"
        fake.llen.return_value = 0
        fake.lock.return_value = _FakeRedisLock()
        pipe = MagicMock()
        fake.pipeline.return_value = pipe
        gate._redis = fake

        repaired = gate._try_repair_leaked_pool()
        self.assertTrue(repaired)
        self.assertEqual(pipe.rpush.call_count, 2)
        pipe.set.assert_any_call(gate._holders_key, "0")

    def test_no_repair_when_holders_active(self):
        gate = MinerUConcurrencyGate(redis_url="redis://fake", max_concurrent=2, key_prefix="test:mineru")
        fake = MagicMock()
        fake.get.side_effect = lambda k: "1" if k in {gate._init_key, gate._holders_key} else None
        fake.llen.return_value = 0
        gate._redis = fake

        repaired = gate._try_repair_leaked_pool()
        self.assertFalse(repaired)

    @patch("app.rag.mineru_redis_gate.MinerUConcurrencyGate.__init__", lambda self, **kwargs: None)
    def test_ensure_initializes_when_not_initialized(self):
        gate = MinerUConcurrencyGate.__new__(MinerUConcurrencyGate)
        gate._max = 2
        gate._pool_key = "test:mineru:sem_pool"
        gate._init_key = "test:mineru:sem_pool_initialized"
        gate._lock_key = "test:mineru:sem_pool_init_lock"
        gate._holders_key = "test:mineru:sem_holders"
        fake = MagicMock()
        fake.get.return_value = None
        fake.lock.return_value = _FakeRedisLock()
        pipe = MagicMock()
        fake.pipeline.return_value = pipe
        gate._redis = fake

        gate._ensure_redis_pool()
        self.assertEqual(pipe.rpush.call_count, 2)


if __name__ == "__main__":
    unittest.main()
