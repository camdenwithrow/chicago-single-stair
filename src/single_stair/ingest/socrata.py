import asyncio
import os
from typing import Any

import httpx

MAX_ATTEMPTS = 5
MAX_RETRY_DELAY_SECONDS = 30.0
RETRYABLE_STATUS_CODES = frozenset({429, *range(500, 600)})


class SocrataResponseError(RuntimeError):
    """Raised when a Socrata API returns an invalid response."""


def retry_delay_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return min(float(retry_after), MAX_RETRY_DELAY_SECONDS)
            except ValueError:
                pass

    return min(2 ** (attempt - 1), MAX_RETRY_DELAY_SECONDS)


async def request_rows(
    client: httpx.AsyncClient,
    dataset_url: str,
    params: dict[str, str | int],
) -> list[dict[str, Any]]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response: httpx.Response | None = None

        try:
            response = await client.get(dataset_url, params=params)
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not all(
                    isinstance(record, dict) for record in payload
                ):
                    raise SocrataResponseError("Socrata response was not a list of records")
                return payload

            if attempt == MAX_ATTEMPTS:
                response.raise_for_status()

        await asyncio.sleep(retry_delay_seconds(response, attempt))

    raise RuntimeError("Socrata request exhausted all retry attempts")


def integer_field(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise SocrataResponseError(f"Socrata response contained an invalid {field}") from error


def app_token_headers() -> dict[str, str]:
    app_token = os.environ.get("SOCRATA_APP_TOKEN")
    return {"X-App-Token": app_token} if app_token else {}
