"""Chamadas à API de fact-checking do TaCertoIssoAI."""

import logging

import httpx

from nodes.whatsapp_api import get_http_client

logger = logging.getLogger(__name__)

_FACT_CHECK_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def _get_fact_check_client() -> httpx.AsyncClient:
    """Cliente com timeout maior para fact-checking (pode levar até 2 min)."""
    # Usa um cliente separado pois o timeout é muito maior
    return httpx.AsyncClient(timeout=_FACT_CHECK_TIMEOUT)


async def check_text(
    endpoint_api: str,
    text_content: str,
    content_type: str = "text",
) -> dict:
    """Envia texto para a API de fact-checking."""
    url = f"{endpoint_api.rstrip('/')}/text"
    payload = {
        "content": [
            {"textContent": text_content, "type": content_type}
        ]
    }

    async with await _get_fact_check_client() as client:
        resp = await client.post(
            url, json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


async def check_content(
    endpoint_api: str,
    content_parts: list[dict],
    deepfake_results: list[dict] | None = None,
) -> dict:
    """Envia múltiplos conteúdos para a API de fact-checking.

    Usado quando há conteúdo composto (ex: imagem + legenda, vídeo + legenda).

    Args:
        endpoint_api: URL base da API.
        content_parts: Lista de dicts com 'textContent' e 'type'.
        deepfake_results: Resultados da detecção de deep-fake (opcional).

    Returns:
        Resposta da API com 'rationale'.
    """
    url = f"{endpoint_api.rstrip('/')}/text"
    payload: dict = {"content": content_parts}
    if deepfake_results is not None:
        payload["deep-fake-verification-result"] = {"results": deepfake_results}

    async with await _get_fact_check_client() as client:
        resp = await client.post(
            url, json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()
