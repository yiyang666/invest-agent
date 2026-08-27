import unittest

from scripts.capture_portfolio_snapshot import build_snapshot


class CapturePortfolioSnapshotTests(unittest.TestCase):
    def test_known_pending_share_marker_does_not_fail_snapshot(self) -> None:
        snapshot = build_snapshot(
            {
                "data": {
                    "wallet": {"ok": True, "bank_total": "0"},
                    "fundApi": {"failedCategories": []},
                    "funds": [
                        {
                            "fundCode": "539001",
                            "fundName": "Pending QDII",
                            "holdVol": "--",
                            "totalAmount": "100.00",
                        }
                    ],
                }
            }
        )

        self.assertEqual(snapshot.quality_status.value, "pass")
        self.assertEqual(snapshot.to_public_dict()["positions"][0]["status"], "pending_confirmation")

    def test_unknown_share_text_still_fails_closed(self) -> None:
        snapshot = build_snapshot(
            {
                "data": {
                    "wallet": {"ok": True, "bank_total": "0"},
                    "fundApi": {"failedCategories": []},
                    "funds": [
                        {
                            "fundCode": "539001",
                            "holdVol": "unexpected",
                            "totalAmount": "100.00",
                        }
                    ],
                }
            }
        )

        self.assertEqual(snapshot.quality_status.value, "fail")


if __name__ == "__main__":
    unittest.main()
