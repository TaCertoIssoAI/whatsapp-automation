"""Chamadas à API de fact-checking do TaCertoIssoAI."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]  # segundos


async def _post_with_retry(url: str, payload: dict) -> dict:
    """POST com retry e exponential backoff para erros 5xx."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Fact-check API retornou %d, tentativa %d/%d, retry em %ds",
                    e.response.status_code, attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.ConnectTimeout) as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "Fact-check API erro de conexão, tentativa %d/%d, retry em %ds",
                    attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise

    raise last_exc  # type: ignore[misc]


async def check_text(
    endpoint_api: str,
    text_content: str,
    content_type: str = "text",
) -> dict:
    url = f"{endpoint_api.rstrip('/')}/text"
    payload = {
        "content": [
            {"textContent": text_content, "type": content_type},
        ]
    }
    return await _post_with_retry(url, payload)


async def check_content(
    endpoint_api: str,
    content_parts: list[dict],
) -> dict:
    url = f"{endpoint_api.rstrip('/')}/text"
    payload = {"content": content_parts}
    return await _post_with_retry(url, payload)
