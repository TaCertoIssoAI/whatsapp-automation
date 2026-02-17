"""Client assíncrono para a WhatsApp Business Cloud API."""

import asyncio
import base64
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_TEXT_LENGTH = 4096  # Limite da WhatsApp Cloud API
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]  # segundos


def _messages_url() -> str:
    return f"{config.WHATSAPP_API_BASE_URL}/messages"


def _media_url(media_id: str = "") -> str:
    if media_id:
        return f"https://graph.facebook.com/v22.0/{media_id}"
    return f"{config.WHATSAPP_API_BASE_URL}/media"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _split_text(text: str, max_len: int = _MAX_TEXT_LENGTH) -> list[str]:
    """Divide texto em pedaços respeitando o limite de caracteres."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        cut_at = remaining.rfind("\n\n", 0, max_len)
        if cut_at == -1:
            cut_at = remaining.rfind("\n", 0, max_len)
        if cut_at == -1:
            cut_at = remaining.rfind(" ", 0, max_len)
        if cut_at == -1:
            cut_at = max_len

        chunks.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip()

    return chunks


async def _request_with_retry(
    method: str,
    url: str,
    client: httpx.AsyncClient,
    **kwargs,
) -> httpx.Response:
    """Executa request HTTP com retry para erros transientes."""
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            if method == "GET":
                resp = await client.get(url, **kwargs)
            else:
                resp = await client.post(url, **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            last_exc = e
            status = e.response.status_code
            # Retry apenas em erros transientes (429 rate limit, 5xx server error)
            # NÃO fazer retry em 4xx (400 Bad Request, 401 Unauthorized, etc.)
            if status in (429, 500, 502, 503, 504) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "WhatsApp API %d em %s, retry %d/%d em %ds",
                    status, url, attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            else:
                # Para 4xx, logar o body da resposta para debug
                if 400 <= status < 500:
                    try:
                        error_body = e.response.text[:500]
                    except Exception:
                        error_body = "N/A"
                    logger.error(
                        "WhatsApp API erro %d em %s: %s",
                        status, url, error_body,
                    )
                raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.ConnectTimeout) as e:
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning("WhatsApp API timeout/conexão em %s, retry %d/%d em %ds", url, attempt + 1, _MAX_RETRIES, delay)
                await asyncio.sleep(delay)
            else:
                raise

    raise last_exc  # type: ignore[misc]


# ── Enviar Texto ──

async def send_text(
    remote_jid: str,
    text: str,
    quoted_message_id: str | None = None,
) -> dict:
    """Envia mensagem de texto. Divide automaticamente se > 4096 chars."""
    chunks = _split_text(text)
    last_result = {}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for i, chunk in enumerate(chunks):
            body: dict = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": remote_jid,
                "type": "text",
                "text": {"body": chunk},
            }
            if quoted_message_id and i == 0:
                body["context"] = {"message_id": quoted_message_id}

            resp = await _request_with_retry("POST", _messages_url(), client, json=body, headers=_headers())
            last_result = resp.json()

    return last_result


# ── Upload de Mídia ──

async def upload_media(
    media_bytes: bytes,
    mime_type: str = "audio/ogg",
    filename: str = "audio.ogg",
) -> str:
    url = _media_url()
    headers = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}
    files = {"file": (filename, media_bytes, mime_type)}
    data = {"messaging_product": "whatsapp", "type": mime_type}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request_with_retry("POST", url, client, headers=headers, files=files, data=data)
        return resp.json().get("id", "")


# ── Enviar Áudio ──

async def send_audio(remote_jid: str, audio_bytes: bytes) -> dict:
    media_id = await upload_media(
        audio_bytes, mime_type="audio/ogg; codecs=opus", filename="audio.ogg",
    )
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": remote_jid,
        "type": "audio",
        "audio": {"id": media_id},
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request_with_retry("POST", _messages_url(), client, json=body, headers=_headers())
        return resp.json()


# ── Marcar como Lida ──

async def mark_as_read(message_id: str) -> None:
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await _request_with_retry("POST", _messages_url(), client, json=body, headers=_headers())
    except Exception:
        logger.warning("Falha ao marcar mensagem como lida: %s", message_id)


# ── Download de Mídia ──

async def download_media(media_id: str) -> bytes:
    auth_header = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await _request_with_retry("GET", _media_url(media_id), client, headers=auth_header)
        download_url = resp.json().get("url", "")

        if not download_url:
            raise ValueError(f"URL de download não encontrada para media_id={media_id}")

        resp = await _request_with_retry("GET", download_url, client, headers=auth_header)
        return resp.content


async def download_media_as_base64(media_id: str) -> str:
    media_bytes = await download_media(media_id)
    return base64.b64encode(media_bytes).decode("utf-8")


# ── Indicador de Digitação ──

async def send_typing_indicator(message_id: str) -> None:
    """Envia indicador de digitação (best-effort, erros são ignorados)."""
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "",  # Preenchido pelo Meta quando usa message_id
        "status": "read",
        "message_id": message_id,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            await client.post(_messages_url(), json=body, headers=_headers())
    except Exception:
        pass  # Typing indicator é best-effort


def send_typing_fire_and_forget(message_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_typing_indicator(message_id))
    except RuntimeError:
        pass
