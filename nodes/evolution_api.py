"""Client assíncrono para a Evolution API (modo WhatsApp Business Cloud API oficial).

Limitações da Cloud API vs Baileys:
- ❌ sendPresence             → não suportado
- ❌ sendWhatsAppAudio        → não suportado (usa sendMedia como fallback)
- ❌ getBase64FromMediaMessage→ não suportado (mídia baixada via Meta Graph API)
- ✅ sendText                 → funciona (com quoted/citação)
- ✅ sendMedia (via URL)      → funciona
- ✅ markMessageAsRead        → via Meta Graph API diretamente
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


def _clean_number(remote_jid: str) -> str:
    """Remove sufixos @s.whatsapp.net / @g.us do JID para usar como número."""
    return remote_jid.split("@")[0] if "@" in remote_jid else remote_jid


# ──────────────────────── Enviar Texto ────────────────────────


async def send_text(
    instance: str,
    remote_jid: str,
    text: str,
    quoted_message_id: str | None = None,
    api_key: str | None = None,
) -> dict:
    """Envia mensagem de texto via Evolution API.

    Suporta quoted (citação de mensagem) com Cloud API.
    A Evolution API usa context: { message_id: quoted.id } internamente.
    """
    url = f"{_base_url()}/message/sendText/{instance}"
    body: dict = {
        "number": _clean_number(remote_jid),
        "text": text,
    }
    # Adiciona quoted para referenciar a mensagem original na resposta
    if quoted_message_id:
        body["quoted"] = {"key": {"id": quoted_message_id}}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=_headers(api_key))

        if not resp.is_success:
            logger.error(
                "Erro ao enviar texto — status=%d, response=%s",
                resp.status_code,
                resp.text,
            )
            return {"status": "error", "error": resp.text, "code": resp.status_code}

        result = resp.json()
        
        # Log resposta completa
        logger.info("sendText response: %s", result)
        
        # Verificar se houve erro OAuth (Cloud API)
        # A Evolution API retorna 201 mas o body contém o erro do Meta Graph API
        if isinstance(result, dict) and (
            "OAuthException" in str(result.get("type", ""))
            or result.get("code") == 190
            or "Invalid OAuth access token" in str(result.get("message", ""))
        ):
            logger.error(
                "ERRO DE AUTENTICAÇÃO OAUTH: %s - A instância '%s' NÃO tem o "
                "WhatsApp Access Token correto configurado. "
                "O campo 'token' da instância na Evolution API deve conter o "
                "access token do Meta (começa com 'EAA...'). "
                "Recrie a instância com: token=<WhatsApp Access Token>.",
                result.get("message", ""),
                instance
            )
            return {"status": "error", "error": "oauth", "detail": result}

        # Verificar outros erros no body
        if isinstance(result, dict) and result.get("error_data"):
            logger.error("Erro retornado pela API: %s", result)
            return {"status": "error", "error": "api_error", "detail": result}

        # Extrair ID da mensagem (pode vir em formatos diferentes)
        msg_id = "?"
        if isinstance(result, dict):
            msg_id = (
                result.get("key", {}).get("id") if isinstance(result.get("key"), dict) else
                result.get("message", {}).get("key", {}).get("id") if isinstance(result.get("message"), dict) else
                result.get("id", "?")
            )
        
        logger.info("Texto enviado para %s — messageId=%s", remote_jid, msg_id)
        return result


# ──────────────────────── Enviar Áudio ────────────────────────


async def send_audio(
    instance: str,
    remote_jid: str,
    audio_base64: str,
    api_key: str | None = None,
) -> dict:
    """Envia áudio em base64 via Evolution API.

    NOTA Cloud API: sendWhatsAppAudio não funciona. Tenta sendMedia como fallback.
    """
    url = f"{_base_url()}/message/sendMedia/{instance}"
    body = {
        "number": _clean_number(remote_jid),
        "mediatype": "audio",
        "mimetype": "audio/ogg; codecs=opus",
        "fileName": "audio.ogg",
        "media": audio_base64,
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=body, headers=_headers(api_key))
        except Exception as e:
            logger.error("Exceção ao enviar áudio: %s", e)
            return {"status": "error", "error": str(e)}

        if not resp.is_success:
            logger.error(
                "Erro ao enviar áudio — status=%d, response=%s",
                resp.status_code,
                resp.text,
            )
            logger.warning(
                "Áudio base64 não suportado pela Cloud API. "
                "Considere hospedar o áudio e usar send_media_url()."
            )
            return {"status": "error", "error": resp.text}

        result = resp.json()

        # Verificar OAuth error no body (mesma situação do send_text)
        if isinstance(result, dict) and (
            "OAuthException" in str(result.get("type", ""))
            or result.get("code") == 190
        ):
            logger.error("OAuth error ao enviar áudio: %s", result)
            return {"status": "error", "error": "oauth", "detail": result}

        logger.info("Áudio enviado para %s", remote_jid)
        return result


# ──────────────────────── Enviar Mídia por URL ────────────────────────


async def send_media_url(
    instance: str,
    remote_jid: str,
    media_url: str,
    mediatype: str,
    mimetype: str,
    filename: str,
    caption: str = "",
    api_key: str | None = None,
) -> dict:
    """Envia mídia por URL pública (funciona com Cloud API).

    Args:
        mediatype: "audio", "image", "video", "document"
        mimetype: ex: "audio/ogg", "image/jpeg", "video/mp4"
        filename: nome com extensão, ex: "audio.ogg", "foto.jpg"
    """
    url = f"{_base_url()}/message/sendMedia/{instance}"
    body: dict = {
        "number": _clean_number(remote_jid),
        "mediatype": mediatype,
        "mimetype": mimetype,
        "fileName": filename,
        "media": media_url,
    }
    if caption:
        body["caption"] = caption

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.post(url, json=body, headers=_headers(api_key))
        except Exception as e:
            logger.error("Exceção ao enviar mídia: %s", e)
            return {"status": "error", "error": str(e)}

        if not resp.is_success:
            logger.error(
                "Erro ao enviar mídia — status=%d, response=%s",
                resp.status_code,
                resp.text,
            )
            return {"status": "error", "error": resp.text}

        result = resp.json()

        # Verificar OAuth error
        if isinstance(result, dict) and (
            "OAuthException" in str(result.get("type", ""))
            or result.get("code") == 190
        ):
            logger.error("OAuth error ao enviar mídia: %s", result)
            return {"status": "error", "error": "oauth", "detail": result}

        logger.info("Mídia (%s) enviada para %s", mediatype, remote_jid)
        return result


# ──────────────────────── Marcar como Lida ────────────────────────


async def mark_as_read(
    instance: str,
    remote_jid: str,
    message_id: str,
    api_key: str | None = None,
) -> dict:
    """Marca mensagem como lida via Meta Graph API diretamente.

    A Evolution API não suporta readMessages com Cloud API,
    mas a Meta Graph API suporta diretamente:
    POST /{phone_number_id}/messages
    { messaging_product: 'whatsapp', status: 'read', message_id: '...' }
    """
    phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
    access_token = config.WHATSAPP_ACCESS_TOKEN

    if not phone_number_id or not access_token:
        logger.debug("mark_as_read ignorado — WHATSAPP_PHONE_NUMBER_ID ou WHATSAPP_ACCESS_TOKEN não configurados")
        return {"status": "skipped", "reason": "missing_config"}

    url = f"https://graph.facebook.com/v22.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.is_success:
                logger.info("Mensagem %s marcada como lida", message_id)
                return {"status": "success"}
            else:
                logger.warning(
                    "Falha ao marcar como lida — status=%d, response=%s",
                    resp.status_code,
                    resp.text,
                )
                return {"status": "error", "code": resp.status_code}
    except Exception as exc:
        logger.warning("Exceção ao marcar como lida: %s", exc)
        return {"status": "error", "error": str(exc)}


# ──────────────────────── Obter Mídia em Base64 ────────────────────────


async def get_media_base64(
    instance: str,
    message_id: str,
    api_key: str | None = None,
) -> dict:
    """Obtém mídia de uma mensagem em base64.

    NOTA Cloud API: este endpoint geralmente não funciona (a mídia vem como URL
    no payload do webhook). Use get_media_url_from_payload() em vez disso.
    Este método tenta mesmo assim para compatibilidade.
    """
    url = f"{_base_url()}/chat/getBase64FromMediaMessage/{instance}"
    body = {"message": {"key": {"id": message_id}}}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json=body, headers=_headers(api_key))

            if not resp.is_success:
                logger.warning(
                    "get_media_base64 falhou — status=%d, response=%s",
                    resp.status_code,
                    resp.text,
                )
                return {"error": resp.text, "status": resp.status_code}

            logger.info("Mídia obtida para mensagem %s", message_id)
            return resp.json()
    except Exception as e:
        logger.warning("get_media_base64 falhou: %s", e)
        return {"error": str(e)}


def get_media_url_from_payload(data: dict) -> tuple[str, str, str]:
    """Extrai a URL pública da mídia diretamente do payload do webhook.

    Na Cloud API, a mídia vem com URL pública no payload em vez de base64.
    Retorna (url, mimetype, filename).
    """
    message = data.get("message", {})

    # Tenta cada tipo de mensagem de mídia
    for msg_type, ext, default_mime in [
        ("imageMessage", "jpg", "image/jpeg"),
        ("videoMessage", "mp4", "video/mp4"),
        ("audioMessage", "ogg", "audio/ogg"),
        ("documentMessage", "bin", "application/octet-stream"),
        ("stickerMessage", "webp", "image/webp"),
    ]:
        msg_data = message.get(msg_type, {})
        if msg_data:
            url = msg_data.get("url", "")
            mimetype = msg_data.get("mimetype", default_mime)
            filename = msg_data.get("fileName", f"media.{ext}")
            if not filename or "." not in filename:
                filename = f"media.{ext}"
            return url, mimetype, filename

    return "", "", ""


# ──────────────────────── Obter Base64 de Quoted Message ────────────────────────


async def get_base64_from_quoted_message(
    instance: str,
    stanza_id: str,
    api_key: str | None = None,
) -> dict:
    """Obtém base64 de mídia de uma mensagem citada.

    NOTA Cloud API: pode não funcionar. Tenta mesmo assim.
    """
    return await get_media_base64(instance, stanza_id, api_key)


# ──────────────────────── Presence (digitando / gravando) ────────────────────────


async def send_presence(
    instance: str,
    remote_jid: str,
    presence: str = "composing",
    api_key: str | None = None,
    delay: float = 0,
) -> None:
    """Envia status de presença.

    NÃO DISPONÍVEL na Cloud API — ignorado silenciosamente.
    Mantém o delay para não quebrar o timing do fluxo.
    """
    logger.debug("send_presence ignorado (Cloud API não suporta)")
    if delay > 0:
        await asyncio.sleep(min(delay, 3.0))


def send_presence_fire_and_forget(
    instance: str,
    remote_jid: str,
    presence: str = "composing",
    api_key: str | None = None,
) -> None:
    """Dispara presença em background.

    NÃO DISPONÍVEL na Cloud API — no-op silencioso.
    """
    logger.debug("send_presence_fire_and_forget ignorado (Cloud API não suporta)")

