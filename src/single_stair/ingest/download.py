import asyncio
import json
import re
from typing import Any

import httpx

from single_stair.ingest.socrata import (
    MAX_ATTEMPTS,
    RETRYABLE_STATUS_CODES,
    retry_delay_seconds,
)


class SourceResponseError(RuntimeError):
    """Raised when a downloaded source does not match its documented response format."""


async def request_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: httpx.QueryParamTypes | None = None,
) -> bytes:
    """Download a binary source with the same bounded retry policy as Socrata."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None
        try:
            response = await client.get(url, params=params)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                return response.content
            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()

        await asyncio.sleep(retry_delay_seconds(response, attempt))

    raise RuntimeError("Download exhausted all retry attempts")


async def request_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: httpx.QueryParamTypes | None = None,
) -> Any:
    content = await request_bytes(client, url, params=params)
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        preview = content[:2_000].decode("utf-8", errors="replace")
        title_match = re.search(r"<title>\s*([^<]+?)\s*</title>", preview, flags=re.IGNORECASE)
        detail = f" ({title_match.group(1)})" if title_match else ""
        raise SourceResponseError(f"Source returned invalid JSON{detail}: {url}") from error
