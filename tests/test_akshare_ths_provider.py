import base64
from datetime import date, datetime
from decimal import Decimal
import json
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.contracts import FundMetadataRequest, NavRequest
from invest_agent.data.providers.akshare_ths import (
    AkshareThsDailyNavProvider,
    AkshareThsFundMetadataProvider,
    ThsHttpPayload,
)
from invest_agent.data.quality import evaluate_fund_metadata_batch, evaluate_nav_batch
from invest_agent.domain.portfolio import QualityStatus


SHANGHAI = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 8, 15, 20, tzinfo=SHANGHAI)


class AkshareThsFundMetadataProviderTests(unittest.TestCase):
    def test_archives_html_and_retains_canonical_and_raw_fields(self) -> None:
        html = b"""
        <html><ul class="other"></ul><ul class="g-dialog">
          <li><span class="key">\xe5\x9f\xba\xe9\x87\x91\xe4\xbb\xa3\xe7\xa0\x81</span><span class="value">000001</span></li>
          <li><span class="key">\xe5\x9f\xba\xe9\x87\x91\xe7\xae\x80\xe7\xa7\xb0</span><span class="value"><a>\xe6\xb5\x8b\xe8\xaf\x95\xe5\x9f\xba\xe9\x87\x91</a></span></li>
          <li><span class="key">\xe5\x9f\xba\xe9\x87\x91\xe7\xb1\xbb\xe5\x9e\x8b</span><span class="value">\xe6\xb7\xb7\xe5\x90\x88\xe5\x9e\x8b</span></li>
          <li><span class="key">\xe6\x88\x90\xe7\xab\x8b\xe6\x97\xa5\xe6\x9c\x9f</span><span class="value">2020-01-02</span></li>
          <li><span class="key">\xe8\x87\xaa\xe5\xae\x9a\xe4\xb9\x89\xe5\xad\x97\xe6\xae\xb5</span><span class="value">\xe4\xbf\x9d\xe7\x95\x99</span></li>
        </ul></html>
        """
        provider = AkshareThsFundMetadataProvider(
            fetcher=lambda url, timeout: ThsHttpPayload(200, html, resolved_url=url),
            clock=lambda: datetime(2026, 8, 15, 19, tzinfo=SHANGHAI),
            max_attempts=1,
            verify_akshare_install=False,
        )
        request = FundMetadataRequest(("000001",), AS_OF)

        raw = provider.fetch_raw(request)
        envelope = json.loads(raw.payload)
        archived_body = base64.b64decode(envelope["responses"][0]["body_base64"])
        batch = provider.normalize(raw, request, raw_content_sha256="a" * 64)

        self.assertEqual(archived_body, html)
        self.assertEqual(batch.records[0].fund_name, "\u6d4b\u8bd5\u57fa\u91d1")
        self.assertEqual(batch.records[0].fund_type, "\u6df7\u5408\u578b")
        self.assertEqual(batch.records[0].establishment_date, date(2020, 1, 2))
        self.assertEqual(batch.records[0].raw_fields["\u81ea\u5b9a\u4e49\u5b57\u6bb5"], "\u4fdd\u7559")
        self.assertEqual(evaluate_fund_metadata_batch(batch).status, QualityStatus.PASS)


class AkshareThsDailyNavProviderTests(unittest.TestCase):
    def test_filters_requested_funds_and_parses_explicit_date(self) -> None:
        body = (
            "xx("
            + json.dumps(
                {
                    "data": {
                        "data": {
                            "0": {"code": "000001", "net": "1.2345", "totalnet": "2.3456"},
                            "1": {"code": "999999", "net": "9.9", "totalnet": "9.9"},
                        }
                    }
                }
            )
            + ")"
        ).encode("utf-8")
        provider = AkshareThsDailyNavProvider(
            fetcher=lambda url, timeout: ThsHttpPayload(200, body, resolved_url=url),
            clock=lambda: datetime(2026, 8, 15, 19, tzinfo=SHANGHAI),
            max_attempts=1,
            verify_akshare_install=False,
        )
        request = NavRequest(("000001",), date(2026, 8, 14), date(2026, 8, 14), AS_OF)

        raw = provider.fetch_raw(request)
        batch = provider.normalize(raw, request, raw_content_sha256="b" * 64)

        self.assertEqual(len(batch.records), 1)
        self.assertEqual(batch.records[0].nav_date, date(2026, 8, 14))
        self.assertEqual(batch.records[0].unit_nav, Decimal("1.2345"))
        self.assertEqual(batch.records[0].accumulated_nav, Decimal("2.3456"))
        self.assertEqual(evaluate_nav_batch(batch).status, QualityStatus.PASS)

    def test_limits_crosscheck_batch_size_before_network(self) -> None:
        calls = []
        provider = AkshareThsDailyNavProvider(
            fetcher=lambda url, timeout: calls.append((url, timeout)),
            max_query_dates=2,
            verify_akshare_install=False,
        )
        request = NavRequest(("000001",), date(2026, 8, 1), date(2026, 8, 3), AS_OF)

        with self.assertRaises(ValueError):
            provider.fetch_raw(request)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
