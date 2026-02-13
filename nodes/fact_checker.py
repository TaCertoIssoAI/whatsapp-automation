"""Chamadas à API de fact-checking do TaCertoIssoAI.

Equivalente aos nós HTTP Request8, HTTP Request13, HTTP Request15,
HTTP Request16, HTTP Request18, HTTP Request23 do n8n.

Todos chamam o endpoint POST /text com payloads diferentes.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


async def check_text(
    endpoint_api: str,
    text_content: str,
    content_type: str = "text",
) -> dict:
    """Envia texto para a API de fact-checking.

    Args:
        endpoint_api: URL base da API.
        text_content: Conteúdo textual a verificar.
        content_type: Tipo do conteúdo ('text', 'audio', 'image', 'video').

    Returns:
        Resposta da API com 'rationale' e opcionalmente 'responseWithoutLinks'.
    """
    url = f"{endpoint_api.rstrip('/')}/text"
    payload = {
        "content": [
            {
                "textContent": text_content,
                "type": content_type,
            }
        ]
    }

    logger.info("Fact-check — tipo=%s, url=%s", content_type, url)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("Fact-check resultado recebido")
        return result


async def check_content(
    endpoint_api: str,
    content_parts: list[dict],
) -> dict:
    """Envia múltiplos conteúdos para a API de fact-checking.

    Usado quando há conteúdo composto (ex: imagem + legenda, vídeo + legenda).

    Args:
        endpoint_api: URL base da API.
        content_parts: Lista de dicts com 'textContent' e 'type'.

    Returns:
        Resposta da API com 'rationale'.
    """
    url = f"{endpoint_api.rstrip('/')}/text"
    payload = {"content": content_parts}

    logger.info(
        "Fact-check (multi) — %d parts, url=%s",
        len(content_parts),
        url,
    )

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()
        logger.info("Fact-check (multi) resultado recebido")
        return result
