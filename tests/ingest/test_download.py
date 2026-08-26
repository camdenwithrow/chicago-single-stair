import unittest

import httpx

from single_stair.ingest.download import SourceResponseError, request_json


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_error_includes_safe_html_title(self) -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                text="<html><head><title>Invalid Key</title></head></html>",
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaisesRegex(SourceResponseError, "Invalid Key"):
                await request_json(client, "https://example.test/data")


if __name__ == "__main__":
    unittest.main()
