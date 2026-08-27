import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pyarrow.parquet as pq

from single_stair.ingest.bls_cpi import (
    CHICAGO_CPI_SERIES_ID,
    _parse_bls_payload,
    _year_ranges,
    ingest_chicago_cpi,
)
from single_stair.ingest.snapshot import SnapshotError


def _payload(year: int, value: str = "300.000") -> dict[str, object]:
    return {
        "status": "REQUEST_SUCCEEDED",
        "Results": {
            "series": [
                {
                    "seriesID": CHICAGO_CPI_SERIES_ID,
                    "data": [
                        {
                            "year": str(year),
                            "period": "M01",
                            "periodName": "January",
                            "value": value,
                            "footnotes": [{}],
                        }
                    ],
                }
            ]
        },
    }


class BlsCpiTests(unittest.IsolatedAsyncioTestCase):
    def test_splits_unregistered_requests_into_ten_year_ranges(self) -> None:
        self.assertEqual(_year_ranges(2015, 2026), [(2015, 2024), (2025, 2026)])

    def test_rejects_an_unexpected_series(self) -> None:
        payload = _payload(2025)
        payload["Results"]["series"][0]["seriesID"] = "WRONG"  # type: ignore[index]

        with self.assertRaises(SnapshotError):
            _parse_bls_payload(payload)

    async def test_writes_one_raw_observation_snapshot_across_request_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch(
                "single_stair.ingest.bls_cpi._request_bls",
                new=AsyncMock(side_effect=[_payload(2024, "292.225"), _payload(2025, "301.232")]),
            ):
                path = await ingest_chicago_cpi(
                    raw_root=Path(temporary_directory),
                    snapshot_date=date(2026, 8, 27),
                    start_year=2015,
                    end_year=2025,
                )

            records = pq.read_table(path / "part-00001.parquet").to_pylist()

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["year"], "2024")
        self.assertEqual(records[1]["value"], "301.232")


if __name__ == "__main__":
    unittest.main()
