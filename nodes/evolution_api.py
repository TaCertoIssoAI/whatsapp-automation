"""Client assíncrono para a Evolution API.

Cobre as operações usadas no workflow n8n:
- Enviar mensagem de texto (com e sem citação)
- Enviar áudio em base64
- Marcar mensagem como lida
- Obter mídia em base64
- Obter base64 de mensagem citada (via HTTP direto)
- Enviar status de presença (digitando/gravando)
"""

import asyncio
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _base_url() -> str:
    return config.EVOLUTION_API_URL.rstrip("/")


def _headers(api_key: str | None = None) -> dict[str, str]:
    key = api_key or config.EVOLUTION_API_KEY
    return {"apiKey": key, "Content-Type": "application/json"}


# ──────────────────────── Enviar Texto ────────────────────────


async def send_text(
    instance: str,
    remote_jid: str,
    text: str,
    quoted_message_id: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Envia mensagem de texto via Evolution API."""
    url = f"{_base_url()}/message/sendText/{instance}"
    body: dict = {
        "number": remote_jid,
        "text": text,
    }
    if quoted_message_id:
        body["options"] = {"quoted": {"key": {"id": quoted_message_id}}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        logger.info("Texto enviado para %s", remote_jid)
        return resp.json()


# ──────────────────────── Enviar Áudio ────────────────────────


async def send_audio(
    instance: str,
    remote_jid: str,
    audio_base64: str,
    api_key: str | None = None,
) -> dict:
    """Envia áudio em base64 via Evolution API."""
    url = f"{_base_url()}/message/sendWhatsAppAudio/{instance}"
    body = {
        "number": remote_jid,
        "audio": audio_base64,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        logger.info("Áudio enviado para %s", remote_jid)
        return resp.json()


# ──────────────────────── Marcar como Lida ────────────────────────


async def mark_as_read(
    instance: str,
    remote_jid: str,
    message_id: str,
    api_key: str | None = None,
) -> dict:
    """Marca mensagem como lida via Evolution API."""
    url = f"{_base_url()}/chat/markMessageAsRead/{instance}"
    body = {
        "readMessages": [
            {
                "remoteJid": remote_jid,
                "id": message_id,
            }
        ]
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.put(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        logger.info("Mensagem %s marcada como lida", message_id)
        return resp.json()


# ──────────────────────── Obter Mídia em Base64 ────────────────────────


async def get_media_base64(
    instance: str,
    message_id: str,
    api_key: str | None = None,
) -> dict:
    """Obtém mídia de uma mensagem em base64 (equivalente ao nó 'Obter mídia em base64')."""
    url = f"{_base_url()}/chat/getBase64FromMediaMessage/{instance}"
    body = {"message": {"key": {"id": message_id}}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        logger.info("Mídia obtida para mensagem %s", message_id)
        return resp.json()


# ──────────────────────── Obter Base64 de Quoted Message ────────────────────────


async def get_base64_from_quoted_message(
    instance: str,
    stanza_id: str,
    api_key: str | None = None,
) -> dict:
    """Obtém base64 de mídia de uma mensagem citada (via HTTP direto)."""
    url = f"{_base_url()}/chat/getBase64FromMediaMessage/{instance}"
    body = {"message": {"key": {"id": stanza_id}}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers(api_key))
        resp.raise_for_status()
        logger.info("Base64 da mídia citada obtida (stanzaId=%s)", stanza_id)
        return resp.json()


# ──────────────────────── Presence (digitando / gravando) ────────────────────────


async def send_presence(
    instance: str,
    remote_jid: str,
    presence: str = "composing",
    api_key: str | None = None,
    delay: float = 0,
) -> None:
    """Envia status de presença (composing=digitando, recording=gravando).

    Equivalente aos sub-workflows 'digitando' e 'gravando' do n8n.
    - digitando: 1s wait + presence composing com 15s de delay
    - gravando: 1s wait + presence recording com 5s de delay

    Args:
        delay: Tempo em segundos para manter a presença (simula o delay do n8n).
    """
    url = f"{_base_url()}/chat/updatePresence/{instance}"
    body = {
        "number": remote_jid,
        "presence": presence,
    }

    try:
        # Espera inicial (1s, como nos sub-workflows do n8n)
        await asyncio.sleep(1)

        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.put(url, json=body, headers=_headers(api_key))
            resp.raise_for_status()
            logger.info("Presença '%s' enviada para %s", presence, remote_jid)

        # Sustenta a presença pelo tempo do delay (15s para digitando, 5s para gravando)
        if delay > 0:
            await asyncio.sleep(delay)

    except Exception as e:
        # Presença não é crítica, apenas log
        logger.warning("Falha ao enviar presença: %s", e)


def send_presence_fire_and_forget(
    instance: str,
    remote_jid: str,
    presence: str = "composing",
    api_key: str | None = None,
) -> None:
    """Dispara presença em background (fire-and-forget), como o n8n faz.

    No n8n, os sub-workflows 'digitando' e 'gravando' são chamados com
    waitForSubWorkflow=false, ou seja, não bloqueiam o fluxo principal.
    """
    delay = 15.0 if presence == "composing" else 5.0

    asyncio.create_task(
        send_presence(instance, remote_jid, presence, api_key, delay=delay)
    )

