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
) -> dict:
    """Envia mensagem de texto. Divide automaticamente se > 4096 chars."""
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
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }
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
    Best-effort — erros são silenciados.
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
        await client.post(_messages_url(), json=body, headers=_headers())
    except Exception:
        pass  # Typing indicator é best-effort


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
