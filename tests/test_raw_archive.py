from datetime import datetime
import gzip
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from invest_agent.data.archive import (
    ImmutableArchiveConflict,
    archive_raw_payload,
    load_raw_payload,
)


class RawArchiveTests(unittest.TestCase):
    def test_archive_is_idempotent_and_refuses_changed_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            kwargs = {
                "root": Path(directory),
                "provider_id": "local_csv_nav",
                "batch_id": "batch-001",
                "content_type": "text/csv",
                "observed_at": datetime(2026, 8, 15, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
                "request_parameters": {"fund_code": "000001"},
            }
            first = archive_raw_payload(payload=b"a,b\n1,2\n", **kwargs)
            replay = archive_raw_payload(payload=b"a,b\n1,2\n", **kwargs)

            self.assertEqual(first.content_sha256, replay.content_sha256)
            self.assertEqual(gzip.decompress(first.payload_path.read_bytes()), b"a,b\n1,2\n")
            loaded = load_raw_payload(
                root=Path(directory), provider_id="local_csv_nav", batch_id="batch-001"
            )
            self.assertEqual(loaded.payload, b"a,b\n1,2\n")
            self.assertEqual(loaded.request_parameters, {"fund_code": "000001"})
            with self.assertRaises(ImmutableArchiveConflict):
                archive_raw_payload(payload=b"different", **kwargs)


if __name__ == "__main__":
    unittest.main()
