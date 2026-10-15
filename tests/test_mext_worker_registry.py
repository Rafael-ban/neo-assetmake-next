import unittest

from _mext.core.worker_registry import WorkerRegistry


class WorkerRegistryTests(unittest.TestCase):
    def test_shutdown_requests_and_waits_for_qthreads(self):
        class FakeQThread:
            def __init__(self):
                self.interruption_requested = False
                self.quit_requested = False
                self.wait_timeout = None
                self.running = True

            def requestInterruption(self):
                self.interruption_requested = True

            def quit(self):
                self.quit_requested = True

            def isRunning(self):
                return self.running

            def wait(self, timeout_ms):
                self.wait_timeout = timeout_ms
                self.running = False
                return True

        thread = FakeQThread()
        registry = WorkerRegistry()
        registry.register_qthread(thread)

        self.assertTrue(registry.shutdown(timeout_ms=50))

        self.assertTrue(thread.interruption_requested)
        self.assertTrue(thread.quit_requested)
        self.assertIsNotNone(thread.wait_timeout)
        self.assertEqual(registry.active_count, 0)

    def test_shutdown_clears_and_waits_for_thread_pools(self):
        class FakeThreadPool:
            def __init__(self):
                self.clear_called = False
                self.wait_timeout = None

            def clear(self):
                self.clear_called = True

            def waitForDone(self, timeout_ms):
                self.wait_timeout = timeout_ms
                return True

        pool = FakeThreadPool()
        registry = WorkerRegistry()
        registry.register_thread_pool(pool)

        self.assertTrue(registry.shutdown(timeout_ms=50))

        self.assertTrue(pool.clear_called)
        self.assertIsNotNone(pool.wait_timeout)
        self.assertEqual(registry.active_count, 0)

    def test_shutdown_reports_lingering_python_threads(self):
        class FakeThread:
            def __init__(self):
                self.join_timeout = None

            def is_alive(self):
                return True

            def join(self, timeout):
                self.join_timeout = timeout

        thread = FakeThread()
        registry = WorkerRegistry()
        registry.register_thread(thread)

        self.assertFalse(registry.shutdown(timeout_ms=50))

        self.assertIsNotNone(thread.join_timeout)
        self.assertEqual(registry.active_count, 1)


if __name__ == "__main__":
    unittest.main()
