"""Client assíncrono para a WhatsApp Business Cloud API."""

import asyncio
import base64
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_http_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Retorna cliente httpx compartilhado (connection pool reutilizável)."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


async def close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def _messages_url() -> str:
    """URL base para enviar mensagens."""
    return f"{config.WHATSAPP_API_BASE_URL}/messages"


def _media_url(media_id: str = "") -> str:
    """URL para upload/download de mídia."""
    if media_id:
        return f"https://graph.facebook.com/v22.0/{media_id}"
    return f"{config.WHATSAPP_API_BASE_URL}/media"


def _headers() -> dict[str, str]:
    """Headers padrão com Bearer token."""
    return {
        "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


# ──────────────────────── Enviar Texto ────────────────────────


async def send_text(
    remote_jid: str,
    text: str,
    quoted_message_id: str | None = None,
) -> dict:
    """Envia mensagem de texto via WhatsApp Cloud API.

    Args:
        remote_jid: Número do destinatário (ex: '5511999999999').
        text: Texto da mensagem.
        quoted_message_id: ID da mensagem a ser citada (opcional).
    """
    body: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": remote_jid,
        "type": "text",
        "text": {"body": text},
    }
    if quoted_message_id:
        body["context"] = {"message_id": quoted_message_id}

    client = await get_http_client()
    resp = await client.post(_messages_url(), json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json()


# ──────────────────────── Enviar Mensagem Interativa com Botões ────────────────────────


async def send_interactive_buttons(
    remote_jid: str,
    body_text: str,
    buttons: list[dict[str, str]],
    header_text: str | None = None,
    footer_text: str | None = None,
) -> dict:
    """Envia mensagem interativa com botões de resposta via WhatsApp Cloud API.

    Args:
        remote_jid: Número do destinatário.
        body_text: Texto principal da mensagem.
        buttons: Lista de dicts com 'id' e 'title' para cada botão.
        header_text: Texto do cabeçalho (opcional).
        footer_text: Texto do rodapé (opcional).
    """
    action_buttons = [
        {
            "type": "reply",
            "reply": {
                "id": btn["id"],
                "title": btn["title"],
            },
        }
        for btn in buttons
    ]

    interactive: dict = {
        "type": "button",
        "body": {"text": body_text},
        "action": {"buttons": action_buttons},
    }

    if header_text:
        interactive["header"] = {"type": "text", "text": header_text}
    if footer_text:
        interactive["footer"] = {"text": footer_text}

    body: dict = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": remote_jid,
        "type": "interactive",
        "interactive": interactive,
    }

    client = await get_http_client()
    resp = await client.post(_messages_url(), json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json()


# ──────────────────────── Upload de Mídia ────────────────────────


async def upload_media(
    media_bytes: bytes,
    mime_type: str = "audio/ogg",
    filename: str = "audio.ogg",
) -> str:
    """Faz upload de mídia para o WhatsApp e retorna o media_id.

    Args:
        media_bytes: Bytes do arquivo.
        mime_type: Tipo MIME do arquivo.
        filename: Nome do arquivo.

    Returns:
        media_id retornado pela API.
    """
    url = _media_url()
    headers = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    files = {
        "file": (filename, media_bytes, mime_type),
    }
    data = {
        "messaging_product": "whatsapp",
        "type": mime_type,
    }

    client = await get_http_client()
    resp = await client.post(url, headers=headers, files=files, data=data)
    resp.raise_for_status()
    return resp.json().get("id", "")


# ──────────────────────── Enviar Áudio ────────────────────────


async def send_audio(
    remote_jid: str,
    audio_bytes: bytes,
) -> dict:
    """Faz upload do áudio e envia via WhatsApp Cloud API.

    A Cloud API exige upload prévio da mídia. O áudio deve estar
    em formato OGG/Opus para ser reproduzido como mensagem de voz.

    Args:
        remote_jid: Número do destinatário.
        audio_bytes: Bytes do áudio em OGG/Opus.
    """
    media_id = await upload_media(
        audio_bytes,
        mime_type="audio/ogg; codecs=opus",
        filename="audio.ogg",
    )

    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": remote_jid,
        "type": "audio",
        "audio": {"id": media_id},
    }

    client = await get_http_client()
    resp = await client.post(_messages_url(), json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json()


# ──────────────────────── Marcar como Lida ────────────────────────


async def mark_as_read(message_id: str) -> dict:
    """Marca mensagem como lida via WhatsApp Cloud API.

    Args:
        message_id: ID da mensagem (wamid.xxx).
    """
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    client = await get_http_client()
    resp = await client.post(_messages_url(), json=body, headers=_headers())
    resp.raise_for_status()
    return resp.json()


# ──────────────────────── Download de Mídia ────────────────────────


async def download_media(media_id: str) -> bytes:
    """Baixa mídia do WhatsApp Cloud API.

    Flow: GET /{media_id} → obtém URL → GET {URL} → bytes binários.

    Args:
        media_id: ID da mídia retornado no webhook.

    Returns:
        Bytes binários da mídia.
    """
    auth_header = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    client = await get_http_client()

    resp = await client.get(_media_url(media_id), headers=auth_header)
    resp.raise_for_status()
    download_url = resp.json().get("url", "")

    if not download_url:
        raise ValueError(f"URL de download não encontrada para media_id={media_id}")

    resp = await client.get(download_url, headers=auth_header)
    resp.raise_for_status()
    return resp.content


async def download_media_as_base64(media_id: str) -> str:
    """Baixa mídia e retorna como base64.

    Conveniência para manter compatibilidade com o pipeline de processamento
    que espera base64.

    Args:
        media_id: ID da mídia retornado no webhook.

    Returns:
        String base64 da mídia.
    """
    media_bytes = await download_media(media_id)
    return base64.b64encode(media_bytes).decode("utf-8")


# ──────────────────────── Indicador de Digitação ────────────────────────


async def send_typing_indicator(message_id: str) -> None:
    """Envia indicador de digitação (expira em ~25s ou quando resposta é enviada)."""
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    try:
        client = await get_http_client()
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        resp.raise_for_status()
    except Exception:
        pass  # Typing não é crítico


async def start_typing_loop(message_id: str) -> asyncio.Task:
    """Loop de digitação em background (reenvia a cada 20s). Cancelar para parar."""

    async def _loop():
        try:
            while True:
                await send_typing_indicator(message_id)
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            pass

    return asyncio.create_task(_loop())


def send_typing_fire_and_forget(message_id: str) -> None:
    """Dispara indicador de digitação em background (fire-and-forget)."""
    asyncio.create_task(send_typing_indicator(message_id))
