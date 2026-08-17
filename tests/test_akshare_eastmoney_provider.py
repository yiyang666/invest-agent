import base64
from datetime import date, datetime
from decimal import Decimal
import json
import re
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import NavRequest
from invest_agent.data.providers.akshare_eastmoney import (
    AkshareEastmoneyNavProvider,
    HttpPayload,
)
from invest_agent.data.quality import evaluate_distribution_batch, evaluate_nav_batch
from invest_agent.domain.portfolio import QualityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")


def epoch_ms(year: int, month: int, day: int) -> int:
    return int(datetime(year, month, day, tzinfo=SHANGHAI).timestamp() * 1000)


def request() -> NavRequest:
    return NavRequest(
        fund_codes=("000001",),
        start_date=date(2026, 8, 14),
        end_date=date(2026, 8, 15),
        as_of=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
    )


class AkshareEastmoneyNavProviderTests(unittest.TestCase):
    def test_archives_exact_body_and_parses_nav_without_executing_javascript(self) -> None:
        body = (
            "var Data_netWorthTrend = "
            + json.dumps(
                [
                    {"x": epoch_ms(2026, 8, 14), "y": 1.2345},
                    {"x": epoch_ms(2026, 8, 15), "y": 1.2500},
                ]
            )
            + ";\nvar ignored = function () { throw new Error('must not run'); };\n"
            + "var Data_ACWorthTrend = "
            + json.dumps(
                [
                    [epoch_ms(2026, 8, 14), 2.0000],
                    [epoch_ms(2026, 8, 15), 2.0155],
                ]
            )
            + ";\n"
        ).encode("utf-8")

        provider = AkshareEastmoneyNavProvider(
            fetcher=lambda url, timeout: HttpPayload(200, body, resolved_url=url),
            max_attempts=1,
            verify_akshare_install=False,
        )

        raw = provider.fetch_raw(request())
        envelope = json.loads(raw.payload)
        archived_body = base64.b64decode(envelope["responses"][0]["body_base64"])
        batch = provider.normalize(raw, request(), raw_content_sha256="a" * 64)

        self.assertEqual(archived_body, body)
        self.assertRegex(raw.batch_id, re.compile(r"^[A-Za-z0-9._-]+$"))
        self.assertEqual(batch.source_domain, "fund.eastmoney.com")
        self.assertEqual(batch.raw_content_sha256, "a" * 64)
        self.assertEqual(len(batch.records), 2)
        self.assertEqual(batch.records[0].unit_nav, Decimal("1.2345"))
        self.assertEqual(batch.records[1].accumulated_nav, Decimal("2.0155"))
        self.assertEqual(evaluate_nav_batch(batch).status, QualityStatus.PARTIAL)

    def test_network_failure_becomes_auditable_failed_batch(self) -> None:
        def fail(_url: str, _timeout: float) -> HttpPayload:
            raise TimeoutError("synthetic timeout")

        provider = AkshareEastmoneyNavProvider(
            fetcher=fail,
            max_attempts=1,
            verify_akshare_install=False,
        )

        raw = provider.fetch_raw(request())
        batch = provider.normalize(raw, request())
        report = evaluate_nav_batch(batch)

        self.assertIn(b"synthetic timeout", raw.payload)
        self.assertEqual(report.status, QualityStatus.FAIL)
        self.assertIn("upstream_fetch_failed", {issue.code for issue in report.issues})
        self.assertIn("empty_nav_batch", {issue.code for issue in report.issues})

    def test_extracts_cash_distribution_from_archived_unit_money(self) -> None:
        body = (
            "var Data_netWorthTrend = "
            + json.dumps(
                [
                    {
                        "x": epoch_ms(2026, 8, 14),
                        "y": 1.20,
                        "unitMoney": "分红：每份派现金0.015元",
                    },
                    {"x": epoch_ms(2026, 8, 15), "y": 1.21, "unitMoney": ""},
                ]
            )
            + ";\nvar Data_ACWorthTrend = [];\n"
        ).encode("utf-8")
        provider = AkshareEastmoneyNavProvider(
            fetcher=lambda url, timeout: HttpPayload(200, body, resolved_url=url),
            max_attempts=1,
            verify_akshare_install=False,
        )

        raw = provider.fetch_raw(request())
        batch = provider.normalize_distributions(
            raw,
            request(),
            raw_content_sha256="c" * 64,
        )
        report = evaluate_distribution_batch(batch)

        self.assertEqual(len(batch.records), 1)
        self.assertEqual(batch.records[0].ex_date, date(2026, 8, 14))
        self.assertEqual(batch.records[0].cash_per_share, Decimal("0.015"))
        self.assertEqual(report.status, QualityStatus.PARTIAL)
        self.assertEqual(batch.source_nav_batch_id, raw.batch_id)

    def test_rejects_future_request_before_network(self) -> None:
        calls = []
        provider = AkshareEastmoneyNavProvider(
            fetcher=lambda url, timeout: calls.append((url, timeout)),
            verify_akshare_install=False,
        )
        invalid = NavRequest(
            fund_codes=("000001",),
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 16),
            as_of=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
        )

        with self.assertRaises(ValueError):
            provider.fetch_raw(invalid)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
