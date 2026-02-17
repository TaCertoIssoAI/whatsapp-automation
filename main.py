"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import suppress

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import config
from graph import compile_graph

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = FastAPI(title="TaCertoIssoAI", version="2.3.0")

workflow = compile_graph()

# ── Deduplicação ──
_processed_messages: dict[str, float] = {}
_DEDUP_TTL = 300
_dedup_lock = asyncio.Lock()


async def _is_duplicate(message_id: str) -> bool:
    """Verifica e registra mensagem para evitar processamento duplicado."""
    if not message_id:
        return False  # Sem ID = não consegue deduplicar, processa normalmente
    now = time.monotonic()
    async with _dedup_lock:
        # Limpa expirados (máx 1000 para evitar memory leak)
        if len(_processed_messages) > 1000:
            expired = sorted(_processed_messages.items(), key=lambda x: x[1])[:500]
            for k, _ in expired:
                del _processed_messages[k]
        else:
            expired_keys = [k for k, t in _processed_messages.items() if now - t > _DEDUP_TTL]
            for k in expired_keys:
                del _processed_messages[k]

        if message_id in _processed_messages:
            return True
        _processed_messages[message_id] = now
        return False


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verifica assinatura HMAC SHA-256 do webhook do Meta."""
    app_secret = config.WHATSAPP_APP_SECRET
    if not app_secret:
        # Sem APP_SECRET configurado → aceita tudo (dev mode)
        return True
    if not signature_header:
        logger.warning("Webhook recebido sem header X-Hub-Signature-256")
        return False
    try:
        expected = hmac.HMAC(app_secret.encode(), payload, hashlib.sha256).hexdigest()
        received = signature_header.removeprefix("sha256=")
        return hmac.compare_digest(expected, received)
    except Exception:
        logger.exception("Erro ao verificar assinatura do webhook")
        return False


def _extract_sender_from_body(body: dict) -> str:
    """Extrai número do remetente do body do webhook (para notificação de erro)."""
    try:
        entries = body.get("entry", [])
        if not entries:
            return ""
        changes = entries[0].get("changes", [])
        if not changes:
            return ""
        messages = changes[0].get("value", {}).get("messages", [])
        if not messages:
            return ""
        return messages[0].get("from", "")
    except Exception:
        return ""


async def process_message(body: dict, message_id: str) -> None:
    """Processa uma mensagem individual via LangGraph."""
    try:
        initial_state = {
            "raw_body": body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }
        await workflow.ainvoke(initial_state)
    except Exception:
        logger.exception("[%s] Erro ao processar mensagem", message_id)
        # Tentar notificar o usuário sobre o erro
        sender = _extract_sender_from_body(body)
        if sender:
            with suppress(Exception):
                from nodes import whatsapp_api
                await whatsapp_api.send_text(
                    sender,
                    "⚠️ Desculpe, ocorreu um erro inesperado ao processar sua mensagem. "
                    "Por favor, tente enviar novamente.",
                )


def _task_done_callback(task: asyncio.Task) -> None:
    """Callback para logar exceções de tasks em background."""
    if task.cancelled():
        logger.warning("Task %s foi cancelada", task.get_name())
        return
    exc = task.exception()
    if exc:
        logger.error(
            "Task %s falhou com exceção não tratada: %s",
            task.get_name(), exc, exc_info=exc,
        )


# ── Endpoints ──


@app.get("/webhook", response_model=None)
async def webhook_verify(request: Request):
    """Verificação de webhook (handshake do Meta)."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado com sucesso")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Falha na verificação do webhook (mode=%s)", mode)
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(request: Request) -> JSONResponse:
    """Recebe notificações do webhook do WhatsApp Business Cloud API.

    CRÍTICO: Este endpoint DEVE sempre retornar 200 rapidamente.
    Se retornar erro (4xx/5xx), o Meta faz retry exponencial e
    eventualmente desativa o webhook.
    """
    # 1) Ler payload raw (para verificação de assinatura)
    try:
        payload = await request.body()
    except Exception:
        logger.exception("Falha ao ler body do webhook")
        # Retorna 200 mesmo assim — não queremos que Meta desative webhook
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # 2) Verificar assinatura (se APP_SECRET configurado)
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload, signature):
        logger.warning("Assinatura inválida no webhook")
        # Retorna 200 para não causar retries do Meta por assinatura inválida
        # Em produção, se APP_SECRET estiver errado, TODAS as msgs seriam perdidas
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # 3) Parsear JSON (pode falhar com payload corrompido)
    try:
        body = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error("Payload do webhook não é JSON válido: %s", e)
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # 4) Processar entries
    entries = body.get("entry", [])
    if not entries:
        return JSONResponse(content={"status": "ok"}, status_code=200)

    for entry in entries:
        for change in entry.get("changes", []):
            try:
                value = change.get("value", {})

                # Ignorar notificações de status (delivered, read, etc.)
                statuses = value.get("statuses")
                if statuses:
                    continue

                messages = value.get("messages")
                if not messages:
                    continue

                message = messages[0]
                msg_id = message.get("id", "")
                msg_type = message.get("type", "unknown")
                sender = message.get("from", "unknown")

                # Ignorar tipos que não precisam de processamento
                if msg_type in ("reaction", "system", "ephemeral", "unsupported"):
                    continue

                logger.info(
                    "Mensagem recebida: id=%s tipo=%s de=%s",
                    msg_id, msg_type, sender,
                )

                if await _is_duplicate(msg_id):
                    logger.info("Mensagem duplicada ignorada: %s", msg_id)
                    continue

                task = asyncio.create_task(
                    process_message(body, msg_id),
                    name=f"process-{msg_id}",
                )
                task.add_done_callback(_task_done_callback)

            except Exception:
                logger.exception("Erro ao extrair mensagem do webhook change")
                # Continua processando outros changes

    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        content={
            "status": "ok",
            "active_tasks": len([
                t for t in asyncio.all_tasks()
                if t.get_name().startswith("process-")
            ]),
            "dedup_cache_size": len(_processed_messages),
        },
        status_code=200,
    )


if __name__ == "__main__":
    logger.info("Iniciando servidor na porta %d...", config.WEBHOOK_PORT)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        reload=False,
        log_level="warning",
        timeout_keep_alive=120,
        limit_concurrency=100,
    )
