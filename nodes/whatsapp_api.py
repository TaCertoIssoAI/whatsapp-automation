"""Client assíncrono para a WhatsApp Business Cloud API."""

import asyncio
import base64
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_TEXT_LENGTH = 4096  # Limite da WhatsApp Cloud API


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
    """Divide texto em pedaços respeitando o limite de caracteres.

    Tenta quebrar em parágrafos (\n\n), depois em linhas (\n),
    depois em espaços, e por último corta no limite.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Tentar quebrar em parágrafo
        cut_at = remaining.rfind("\n\n", 0, max_len)
        if cut_at == -1:
            # Tentar quebrar em linha
            cut_at = remaining.rfind("\n", 0, max_len)
        if cut_at == -1:
            # Tentar quebrar em espaço
            cut_at = remaining.rfind(" ", 0, max_len)
        if cut_at == -1:
            # Cortar no limite
            cut_at = max_len

        chunks.append(remaining[:cut_at].rstrip())
        remaining = remaining[cut_at:].lstrip()

    return chunks


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
            # Só cita a mensagem original no primeiro chunk
            if quoted_message_id and i == 0:
                body["context"] = {"message_id": quoted_message_id}

            resp = await client.post(_messages_url(), json=body, headers=_headers())
            resp.raise_for_status()
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
        resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
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
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        resp.raise_for_status()
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
            resp = await client.post(_messages_url(), json=body, headers=_headers())
            resp.raise_for_status()
    except Exception:
        logger.warning("Falha ao marcar mensagem como lida: %s", message_id)


# ── Download de Mídia ──

async def download_media(media_id: str) -> bytes:
    auth_header = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(_media_url(media_id), headers=auth_header)
        resp.raise_for_status()
        download_url = resp.json().get("url", "")

        if not download_url:
            raise ValueError(f"URL de download não encontrada para media_id={media_id}")

        resp = await client.get(download_url, headers=auth_header)
        resp.raise_for_status()
        return resp.content


async def download_media_as_base64(media_id: str) -> str:
    media_bytes = await download_media(media_id)
    return base64.b64encode(media_bytes).decode("utf-8")


# ── Indicador de Digitação ──

async def send_typing_indicator(message_id: str) -> None:
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_messages_url(), json=body, headers=_headers())
            resp.raise_for_status()
    except Exception:
        pass  # Indicador não é crítico


def send_typing_fire_and_forget(message_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_typing_indicator(message_id))
    except RuntimeError:
        pass
