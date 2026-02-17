"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API."""

import asyncio
import hashlib
import hmac
import logging
import time

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

app = FastAPI(title="TaCertoIssoAI", version="2.2.0")

workflow = compile_graph()

# ── Deduplicação ──
_processed_messages: dict[str, float] = {}
_DEDUP_TTL = 300
_dedup_lock = asyncio.Lock()


async def _is_duplicate(message_id: str) -> bool:
    now = time.monotonic()
    async with _dedup_lock:
        expired = [k for k, t in _processed_messages.items() if now - t > _DEDUP_TTL]
        for k in expired:
            del _processed_messages[k]
        if message_id in _processed_messages:
            return True
        _processed_messages[message_id] = now
        return False


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    app_secret = config.WHATSAPP_APP_SECRET
    if not app_secret:
        return True
    if not signature_header:
        return False
    expected = hmac.HMAC(app_secret.encode(), payload, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


async def process_message(body: dict, message_id: str) -> None:
    try:
        initial_state = {
            "raw_body": body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }
        await workflow.ainvoke(initial_state)
    except Exception:
        logger.exception("[%s] Erro ao processar mensagem", message_id)


# ── Endpoints ──

@app.get("/webhook", response_model=None)
async def webhook_verify(request: Request):
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado com sucesso")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Falha na verificação do webhook")
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(request: Request) -> JSONResponse:
    payload = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload, signature):
        logger.warning("Assinatura inválida no webhook")
        return JSONResponse(content={"error": "Invalid signature"}, status_code=403)

    body = await request.json()

    entries = body.get("entry", [])
    if not entries:
        return JSONResponse(content={"status": "ok"}, status_code=200)

    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                continue

            message = messages[0]
            msg_id = message.get("id", "")

            if msg_id and await _is_duplicate(msg_id):
                continue

            asyncio.create_task(
                process_message(body, msg_id),
                name=f"process-{msg_id}",
            )

    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "active_tasks": len([
                t for t in asyncio.all_tasks()
                if t.get_name().startswith("process-")
            ]),
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
        timeout_keep_alive=65,
    )
