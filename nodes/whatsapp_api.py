"""Client assíncrono para a WhatsApp Business Cloud API (API Oficial).

Substitui o evolution_api.py, cobrindo as mesmas operações:
- Enviar mensagem de texto (com e sem citação)
- Enviar áudio (upload de mídia + envio)
- Marcar mensagem como lida
- Baixar mídia (media_id → URL → bytes → base64)
- Enviar indicador de digitação/gravação
"""

import asyncio
import base64
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        resp.raise_for_status()
        logger.info("Texto enviado para %s", remote_jid)
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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        resp.raise_for_status()
        logger.info("Mensagem interativa com botões enviada para %s", remote_jid)
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

    # Upload multipart
    files = {
        "file": (filename, media_bytes, mime_type),
    }
    data = {
        "messaging_product": "whatsapp",
        "type": mime_type,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
        result = resp.json()
        media_id = result.get("id", "")
        logger.info("Mídia uploaded — media_id=%s", media_id)
        return media_id


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
    # 1. Upload da mídia
    media_id = await upload_media(
        audio_bytes,
        mime_type="audio/ogg; codecs=opus",
        filename="audio.ogg",
    )

    # 2. Enviar mensagem de áudio
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
        logger.info("Áudio enviado para %s (media_id=%s)", remote_jid, media_id)
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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        resp.raise_for_status()
        logger.info("Mensagem %s marcada como lida", message_id)
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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # 1. Obter URL de download
        resp = await client.get(_media_url(media_id), headers=auth_header)
        resp.raise_for_status()
        media_info = resp.json()
        download_url = media_info.get("url", "")

        if not download_url:
            raise ValueError(f"URL de download não encontrada para media_id={media_id}")

        # 2. Baixar o arquivo binário (a URL requer Bearer token)
        download_headers = {
            "Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}",
        }
        resp = await client.get(download_url, headers=download_headers)
        resp.raise_for_status()
        media_bytes = resp.content

        logger.info(
            "Mídia baixada — media_id=%s, %d bytes",
            media_id, len(media_bytes),
        )
        return media_bytes


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
    """Envia indicador de digitação via WhatsApp Cloud API.

    A Cloud API exige o message_id da mensagem recebida, junto com
    status 'read' e typing_indicator.type 'text'.
    A API não suporta 'recording' como a Evolution API.
    O indicador desaparece após 25s ou quando uma resposta é enviada.
    """
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {
            "type": "text",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_messages_url(), json=body, headers=_headers())
            resp.raise_for_status()
            logger.debug("Indicador de digitação enviado para msg %s", message_id)
    except Exception as e:
        # Presença não é crítica, apenas log
        logger.warning("Falha ao enviar indicador de digitação: %s", e)


async def start_typing_loop(message_id: str) -> asyncio.Task:
    """Inicia um loop de indicador de digitação que roda em background.

    O indicador é reenviado a cada 20 segundos (expira em ~25s na API).
    Retorna a Task para que possa ser cancelada quando a resposta for enviada.

    Usage:
        typing_task = await start_typing_loop(msg_id)
        # ... processar ...
        typing_task.cancel()
    """

    async def _loop():
        try:
            while True:
                await send_typing_indicator(message_id)
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            logger.debug("Loop de digitação cancelado para msg %s", message_id)

    task = asyncio.create_task(_loop())
    return task


def send_typing_fire_and_forget(message_id: str) -> None:
    """Dispara indicador de digitação em background (fire-and-forget).

    Equivalente ao send_presence_fire_and_forget da Evolution API.
    """
    asyncio.create_task(send_typing_indicator(message_id))
