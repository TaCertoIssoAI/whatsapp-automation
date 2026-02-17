"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API."""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import config
from nodes.message_handler import handle_incoming_message
from nodes.whatsapp_api import close_http_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: limpa clients compartilhados ao desligar."""
    logger.info("Bot iniciado na porta %d", config.WEBHOOK_PORT)
    yield
    await close_http_client()
    logger.info("Bot encerrado")


app = FastAPI(
    title="TaCertoIssoAI",
    version="2.1.0",
    lifespan=lifespan,
)


# ──────────────────────── Validação de assinatura ────────────────────────


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Valida a assinatura X-Hub-Signature-256."""
    app_secret = config.WHATSAPP_APP_SECRET
    if not app_secret:
        return True

    if not signature_header:
        return False

    # Formato: "sha256=<hex_digest>"
    expected_signature = hmac.HMAC(
        app_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected_signature, received)


# ──────────────────────── Processamento assíncrono ────────────────────────


async def process_message(body: dict) -> None:
    """Processa a mensagem recebida em background."""
    try:
        await handle_incoming_message(body)
    except Exception:
        logger.exception("Erro ao processar mensagem")


# ──────────────────────── Endpoints ────────────────────────


@app.get("/webhook", response_model=None)
async def webhook_verify(request: Request):
    """Verificação do webhook pela Meta (GET)."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Falha na verificação do webhook")
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    """Recebe mensagens da WhatsApp Cloud API e processa em background."""
    payload = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload, signature):
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

            background_tasks.add_task(process_message, body)

    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"}, status_code=200)


# ──────────────────────── Main ────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        reload=False,
        log_level="info",
    )
