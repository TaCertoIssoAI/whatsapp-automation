"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API.

Endpoints:
- GET  /webhook  → Verificação do webhook (hub.verify_token)
- POST /webhook  → Receber mensagens e eventos
- GET  /health   → Health check
"""

import hashlib
import hmac
import logging

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import config
from graph import compile_graph

# ──────────────────────── Logging ────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ──────────────────────── App ────────────────────────

app = FastAPI(
    title="TaCertoIssoAI - Fake News Detector",
    description="Bot de detecção de fake news para WhatsApp via LangGraph",
    version="2.0.0",
)

# Compila o grafo uma vez na inicialização
workflow = compile_graph()


# ──────────────────────── Validação de assinatura ────────────────────────


def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Valida a assinatura X-Hub-Signature-256 do webhook.

    A Meta assina cada requisição com HMAC-SHA256 usando o App Secret.
    Se WHATSAPP_APP_SECRET não estiver configurado, pula a validação.
    """
    app_secret = config.WHATSAPP_APP_SECRET
    if not app_secret:
        logger.warning("WHATSAPP_APP_SECRET não configurado — assinatura não validada")
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
    """Processa a mensagem recebida usando o grafo LangGraph."""
    try:
        initial_state = {
            "raw_body": body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }

        # Extrair informações básicas para log
        value = (
            body.get("entry", [{}])[0]
            .get("changes", [{}])[0]
            .get("value", {})
        )
        messages = value.get("messages", [])
        sender = messages[0].get("from", "unknown") if messages else "unknown"

        logger.info("Processando mensagem de %s", sender)

        result = await workflow.ainvoke(initial_state)

        logger.info(
            "Processamento concluído. Rationale: %s",
            "presente" if result.get("rationale") else "ausente",
        )

    except Exception:
        logger.exception("Erro ao processar mensagem")


# ──────────────────────── Endpoints ────────────────────────


@app.get("/webhook", response_model=None)
async def webhook_verify(
    request: Request,
):
    """Verificação do webhook pela Meta (GET).

    A Meta envia um GET com:
    - hub.mode = "subscribe"
    - hub.verify_token = o token que você definiu
    - hub.challenge = string de desafio para retornar

    Deve retornar o hub.challenge se o token for válido.
    """
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado com sucesso")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Falha na verificação do webhook (token inválido)")
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    """Endpoint webhook que recebe mensagens da WhatsApp Cloud API.

    Valida a assinatura X-Hub-Signature-256 e processa a mensagem em background.
    """
    payload = await request.body()

    # Validar assinatura
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(payload, signature):
        logger.warning("Assinatura inválida no webhook")
        return JSONResponse(content={"error": "Invalid signature"}, status_code=403)

    body = await request.json()

    # A Cloud API envia vários tipos de evento; só processamos mensagens
    entries = body.get("entry", [])
    if not entries:
        return JSONResponse(content={"status": "ok"}, status_code=200)

    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])

            if not messages:
                # Pode ser evento de status (delivered, read), ignorar
                statuses = value.get("statuses", [])
                if statuses:
                    logger.debug("Evento de status recebido, ignorando")
                continue

            message = messages[0]
            sender = message.get("from", "unknown")
            msg_type = message.get("type", "unknown")

            logger.info(
                "Webhook recebido — de=%s, tipo=%s",
                sender,
                msg_type,
            )

            # Processa em background para responder rapidamente ao webhook
            background_tasks.add_task(process_message, body)

    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(content={"status": "ok"}, status_code=200)


# ──────────────────────── Main ────────────────────────

if __name__ == "__main__":
    logger.info("Iniciando servidor na porta %d...", config.WEBHOOK_PORT)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        reload=False,
        log_level="info",
    )
