"""Client assíncrono para a WhatsApp Business Cloud API.

Usa httpx.AsyncClient singleton com connection pool para evitar
overhead de TCP/TLS handshake em cada request.
"""

import asyncio
import base64
import logging

import httpx

import config

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_TEXT_LENGTH = 4096
_MAX_RETRIES = 3
_RETRY_DELAYS = [1, 2, 4]

# ── Typing indicator management ──
# Maps recipient phone → asyncio.Event that stops the keepalive loop.
# When send_text/send_audio delivers a message, we set the event so
# the keepalive task stops immediately (no stale "typing..." after reply).
_typing_stop_events: dict[str, asyncio.Event] = {}
# Maps recipient phone → original message_id (wamid) used for typing indicator.
# Needed so send_text(keep_typing=True) can re-fire the indicator after sending.
_typing_message_ids: dict[str, str] = {}


def register_typing_stop_event(recipient: str, event: asyncio.Event, message_id: str = "") -> None:
    """Registra um Event para parar o typing keepalive de um destinatário."""
    _typing_stop_events[recipient] = event
    if message_id:
        _typing_message_ids[recipient] = message_id


def unregister_typing_stop_event(recipient: str) -> None:
    """Remove o Event de typing de um destinatário."""
    _typing_stop_events.pop(recipient, None)
    _typing_message_ids.pop(recipient, None)


def _stop_typing_for(recipient: str) -> None:
    """Para o typing keepalive para o destinatário (chamado ao enviar mensagem)."""
    ev = _typing_stop_events.get(recipient)
    if ev is not None:
        ev.set()

# Client singleton com connection pool — reutiliza conexões TCP/TLS
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Retorna client singleton, criando se necessário."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=_TIMEOUT,
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=120,
            ),
        )
    return _client


async def close_client() -> None:
    """Fecha o client HTTP (chamado no shutdown do app)."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


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
    **kwargs,
) -> httpx.Response:
    """Executa request HTTP com retry para erros transientes."""
    client = _get_client()
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
    *,
    keep_typing: bool = False,
) -> dict:
    """Envia mensagem de texto. Divide automaticamente se > 4096 chars.

    Automaticamente para o typing keepalive para este destinatário
    assim que a primeira parte da mensagem é enviada — a menos que
    ``keep_typing=True`` (usado para mensagens intermediárias como
    "Estou analisando …" onde o typing deve continuar).
    """
    # Parar typing indicator ANTES de enviar (a mensagem em si já cancela
    # o indicador no lado do WhatsApp, mas paramos o keepalive loop no nosso lado)
    if not keep_typing:
        _stop_typing_for(remote_jid)

    chunks = _split_text(text)
    last_result = {}

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

        resp = await _request_with_retry("POST", _messages_url(), json=body, headers=_headers())
        last_result = resp.json()

    # Quando keep_typing=True, re-disparar o typing indicator após enviar
    # a mensagem, pois o WhatsApp cancela o indicador ao entregar a mensagem.
    #
    # Estratégia: disparo DUPLO com delays escalonados para maximizar a
    # chance do typing reaparecer antes que o usuário perceba o gap.
    # O primeiro disparo (0.5s) cobre o caso normal; o segundo (1.5s) é
    # uma rede de segurança caso o primeiro chegue cedo demais (antes do
    # WhatsApp processar a entrega da mensagem que cancela o typing).
    if keep_typing:
        wamid = _typing_message_ids.get(remote_jid, "")
        if wamid:
            async def _refire_typing():
                try:
                    await asyncio.sleep(0.5)
                    await send_typing_indicator(wamid)
                    logger.info("[typing] Re-fire 1/2 OK para %s (wamid=%s)", remote_jid[-4:], wamid[:20])
                except Exception as e:
                    logger.warning("[typing] Re-fire 1/2 falhou para %s: %s", remote_jid[-4:], e)
                try:
                    await asyncio.sleep(1.0)
                    # Verificar se o typing não foi parado (stop event settado)
                    ev = _typing_stop_events.get(remote_jid)
                    if ev is None or not ev.is_set():
                        await send_typing_indicator(wamid)
                        logger.info("[typing] Re-fire 2/2 OK para %s", remote_jid[-4:])
                except Exception:
                    pass
            try:
                asyncio.get_running_loop().create_task(
                    _refire_typing(), name=f"refire-typing-{remote_jid[-4:]}"
                )
            except RuntimeError:
                pass
        else:
            logger.warning("[typing] keep_typing=True mas sem wamid registrado para %s", remote_jid[-4:])

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

    resp = await _request_with_retry("POST", url, headers=headers, files=files, data=data)
    return resp.json().get("id", "")


# ── Enviar Áudio ──

async def send_audio(remote_jid: str, audio_bytes: bytes) -> dict:
    _stop_typing_for(remote_jid)
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
    resp = await _request_with_retry("POST", _messages_url(), json=body, headers=_headers())
    return resp.json()


# ── Marcar como Lida ──

async def mark_as_read(message_id: str) -> None:
    """Marca mensagem como lida, preservando o typing indicator se ativo.

    Se há um typing indicator ativo para o destinatário desta mensagem,
    inclui o campo typing_indicator no payload para NÃO cancelar o
    indicador de digitação. Caso contrário, envia mark-as-read simples.
    """
    body: dict = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
    # Se há typing ativo para algum recipient que usa este message_id,
    # incluir typing_indicator para não cancelar o indicador.
    if message_id and message_id in _typing_message_ids.values():
        body["typing_indicator"] = {"type": "text"}
    try:
        await _request_with_retry("POST", _messages_url(), json=body, headers=_headers())
    except Exception:
        logger.warning("Falha ao marcar mensagem como lida: %s", message_id)


# ── Download de Mídia ──

async def download_media(media_id: str) -> bytes:
    auth_header = {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}

    resp = await _request_with_retry("GET", _media_url(media_id), headers=auth_header)
    download_url = resp.json().get("url", "")

    if not download_url:
        raise ValueError(f"URL de download não encontrada para media_id={media_id}")

    resp = await _request_with_retry("GET", download_url, headers=auth_header)
    return resp.content


async def download_media_as_base64(media_id: str) -> str:
    media_bytes = await download_media(media_id)
    return base64.b64encode(media_bytes).decode("utf-8")


# ── Indicador de Digitação (Cloud API) ──
#
# A Cloud API NÃO tem um endpoint separado para typing.
# O typing indicator é ativado JUNTO com o mark-as-read, usando:
#   POST /{PHONE_NUMBER_ID}/messages
#   {
#     "messaging_product": "whatsapp",
#     "status": "read",
#     "message_id": "<WAMID>",
#     "typing_indicator": {"type": "text"}
#   }
# Isso marca a mensagem como lida E mostra "digitando..." por 25s
# (ou até a próxima mensagem enviada, o que vier primeiro).
# Não existe "typing_off" — ele some automaticamente.


async def send_typing_indicator(message_id: str) -> None:
    """Marca mensagem como lida E ativa 'digitando...' no WhatsApp.

    Cloud API: POST /{PHONE_NUMBER_ID}/messages com status=read +
    typing_indicator. O indicador dura até 25s ou até enviarmos uma
    mensagem, o que vier primeiro.

    Requer o message_id (wamid) da mensagem recebida do usuário.
    Best-effort — erros são logados em DEBUG para facilitar diagnóstico.
    """
    if not message_id:
        return

    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }
    try:
        client = _get_client()
        resp = await client.post(_messages_url(), json=body, headers=_headers())
        if resp.status_code != 200:
            logger.warning(
                "Typing indicator retornou status %d para msg %s: %s",
                resp.status_code, message_id[:30], resp.text[:200],
            )
        else:
            logger.info("[typing] Indicator enviado OK para msg %s", message_id[:20])
    except Exception as exc:
        logger.warning("Typing indicator falhou para msg %s: %s", message_id[:30], exc)


def typing_indicator_fire_and_forget(message_id: str) -> None:
    """Dispara typing indicator sem bloquear (fire-and-forget).

    Marca como lido + mostra 'digitando...' automaticamente.
    """
    if not message_id:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_typing_indicator(message_id))
    except RuntimeError:
        pass
