import json
from pathlib import Path
import tempfile
import unittest

from invest_agent.web.core_dca_service import CoreDcaService
from invest_agent.web.server import make_handler
from tests.test_local_web import FakeAijijinOnboarding, FakeCredentialStore, FakeSocket


class CoreDcaServiceTests(unittest.TestCase):
    def test_plan_revisions_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CoreDcaService(Path(directory))
            self.assertEqual(service.current()["plans"], [])
            self.assertEqual(service.current()["revision"], 0)
            first = service.save({"revision": 0, "plans": [
                {"fund_code": "110020", "cadence": "monthly", "day": 5, "amount_cny": 600},
                {"fund_code": "008401", "cadence": "daily", "day": None, "amount_cny": 10},
            ]})
            self.assertEqual(first["revision"], 1)
            self.assertEqual(service.current()["plans"], first["plans"])
            self.assertFalse(service.current()["locked"])
            locked = service.set_lock({"revision": 1, "locked": True})
            self.assertTrue(locked["locked"])
            self.assertEqual(locked["plans"], first["plans"])
            self.assertTrue(CoreDcaService(Path(directory)).current()["locked"])
            with self.assertRaisesRegex(ValueError, "已锁定"):
                service.save({"revision": 2, "plans": []})
            with self.assertRaisesRegex(ValueError, "刷新"):
                service.set_lock({"revision": 1, "locked": False})
            unlocked = service.set_lock({"revision": 2, "locked": False})
            self.assertFalse(unlocked["locked"])
            self.assertEqual(unlocked["revision"], 3)
            with self.assertRaisesRegex(ValueError, "刷新"):
                service.save({"revision": 0, "plans": []})
            for bad in [
                {"fund_code": "bad", "cadence": "monthly", "day": 5, "amount_cny": 600},
                {"fund_code": "110020", "cadence": "monthly", "day": 29, "amount_cny": 600},
                {"fund_code": "110020", "cadence": "weekly", "day": 6, "amount_cny": 600},
                {"fund_code": "110020", "cadence": "daily", "day": None, "amount_cny": 0},
                {"fund_code": "110020", "cadence": "daily", "day": None, "amount_cny": float("nan")},
            ]:
                with self.assertRaises(ValueError):
                    service.save({"revision": 3, "plans": [bad]})
            self.assertEqual(service.current()["revision"], 3)

    def test_http_is_local_and_cannot_submit_trades(self):
        with tempfile.TemporaryDirectory() as directory:
            handler = make_handler(Path(directory), FakeCredentialStore(), FakeAijijinOnboarding())

            def request(path, method="GET", body=b"", origin=None):
                head = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n"
                if origin:
                    head += f"Origin: {origin}\r\n"
                if body:
                    head += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                sock = FakeSocket((head + "Connection: close\r\n\r\n").encode() + body)
                handler(sock, ("127.0.0.1", 1234), None)
                return bytes(sock.response)

            self.assertIn(b"200 OK", request("/core-dca.js"))
            self.assertIn(b'"revision": 0', request("/api/core-dca"))
            payload = json.dumps({"revision": 0, "plans": [
                {"fund_code": "110020", "cadence": "monthly", "day": 5, "amount_cny": 600}
            ]}).encode()
            self.assertIn(b"403", request("/api/core-dca", "POST", payload, "https://elsewhere.invalid"))
            self.assertIn(b"200 OK", request("/api/core-dca", "POST", payload))
            self.assertIn(b'"revision": 1', request("/api/core-dca"))
            lock = json.dumps({"revision": 1, "locked": True}).encode()
            self.assertIn(b"403", request("/api/core-dca/lock", "POST", lock, "https://elsewhere.invalid"))
            self.assertIn(b'"locked": true', request("/api/core-dca/lock", "POST", lock))
            self.assertIn(b"400", request("/api/core-dca", "POST", json.dumps({"revision": 2, "plans": []}).encode()))
            self.assertIn(b"400", request("/api/core-dca", "POST", payload))
            self.assertIn(b"404", request("/api/core-dca/buy", "POST", payload))


if __name__ == "__main__":
    unittest.main()
