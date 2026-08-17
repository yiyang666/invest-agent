import base64
from datetime import date, datetime
from decimal import Decimal
import json
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import NavRequest
from invest_agent.data.providers.penghua_official import (
    PenghuaHttpPayload,
    PenghuaOfficialNavProvider,
)
from invest_agent.data.quality import evaluate_nav_batch
from invest_agent.domain.portfolio import QualityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")


def request() -> NavRequest:
    return NavRequest(
        fund_codes=("160646",),
        start_date=date(2026, 8, 13),
        end_date=date(2026, 8, 14),
        as_of=datetime(2026, 8, 15, 20, tzinfo=SHANGHAI),
    )


def response_body(records, *, pages=1, total=None) -> bytes:
    return json.dumps(
        {
            "code": "CM000000",
            "data": {
                "pages": pages,
                "total": len(records) if total is None else total,
                "records": records,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class PenghuaOfficialNavProviderTests(unittest.TestCase):
    def test_archives_every_exact_page_and_normalizes_in_date_order(self) -> None:
        bodies = {
            1: response_body(
                [{"fundCode": "160646", "navDate": "20260814", "unitNav": "1.1342", "aggrUnitNav": "1.1342"}],
                pages=2,
                total=2,
            ),
            2: response_body(
                [{"fundCode": "160646", "navDate": "20260813", "unitNav": "1.1358", "aggrUnitNav": "1.1358"}],
                pages=2,
                total=2,
            ),
        }

        def fetch(_url, payload, _timeout):
            body = bodies[int(payload["pageNo"])]
            return PenghuaHttpPayload(200, body, resolved_url=_url)

        provider = PenghuaOfficialNavProvider(fetcher=fetch, max_attempts=1)
        raw = provider.fetch_raw(request())
        envelope = json.loads(raw.payload)
        archived = [base64.b64decode(item["body_base64"]) for item in envelope["responses"]]
        batch = provider.normalize(raw, request(), raw_content_sha256="a" * 64)

        self.assertEqual(archived, [bodies[1], bodies[2]])
        self.assertEqual(batch.source_domain, "www.phfund.com.cn")
        self.assertEqual([item.nav_date for item in batch.records], [date(2026, 8, 13), date(2026, 8, 14)])
        self.assertEqual(batch.records[-1].unit_nav, Decimal("1.1342"))
        self.assertEqual(batch.records[-1].accumulated_nav, Decimal("1.1342"))
        self.assertEqual(evaluate_nav_batch(batch).status, QualityStatus.PARTIAL)

    def test_http_failure_is_preserved_as_failed_batch(self) -> None:
        provider = PenghuaOfficialNavProvider(
            fetcher=lambda _url, _payload, _timeout: PenghuaHttpPayload(503, b"unavailable"),
            max_attempts=1,
        )
        raw = provider.fetch_raw(request())
        batch = provider.normalize(raw, request())
        report = evaluate_nav_batch(batch)

        self.assertIn(b"dW5hdmFpbGFibGU=", raw.payload)
        self.assertEqual(report.status, QualityStatus.FAIL)
        self.assertIn("upstream_http_status", {issue.code for issue in report.issues})
        self.assertIn("empty_nav_batch", {issue.code for issue in report.issues})

    def test_rejects_excessive_declared_page_count(self) -> None:
        body = response_body([], pages=101, total=10001)
        provider = PenghuaOfficialNavProvider(
            fetcher=lambda _url, _payload, _timeout: PenghuaHttpPayload(200, body),
            max_attempts=1,
            max_pages=100,
        )
        raw = provider.fetch_raw(request())
        batch = provider.normalize(raw, request())
        report = evaluate_nav_batch(batch)

        self.assertIn("pagination_limit_exceeded", {issue.code for issue in report.issues})
        self.assertEqual(report.status, QualityStatus.FAIL)


if __name__ == "__main__":
    unittest.main()
