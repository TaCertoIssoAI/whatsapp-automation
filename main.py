"""Servidor FastAPI com endpoint webhook para receber mensagens do WhatsApp.

Equivalente ao nó 'Mensagem recebida' (webhook POST /messages-upsert) do n8n.
"""

import asyncio
import logging

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

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
    version="1.0.0",
)

# Compila o grafo uma vez na inicialização
workflow = compile_graph()


# ──────────────────────── Processamento assíncrono ────────────────────────


async def process_message(body: dict) -> None:
    """Processa a mensagem recebida usando o grafo LangGraph."""
    try:
        initial_state = {
            "raw_body": body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }

        data = body.get("data", {})
        key = data.get("key", {})
        remote_jid = key.get("remoteJid", "unknown")

        logger.info("Processando mensagem de %s", remote_jid)

        # Log detalhado para mensagens de grupo (ajuda a descobrir BOT_MENTION_JID)
        if remote_jid.endswith("@g.us"):
            from nodes.data_extractor import get_context_info
            context_info = get_context_info(data)
            mentioned = context_info.get("mentionedJid", [])
            participant = key.get("participant", "")
            logger.info(
                "=== GRUPO === participant=%s, mentionedJid=%s, "
                "messageType=%s, fromMe=%s",
                participant,
                mentioned,
                data.get("messageType", ""),
                key.get("fromMe", False),
            )

        result = await workflow.ainvoke(initial_state)

        logger.info(
            "Processamento concluído. Rationale: %s",
            "presente" if result.get("rationale") else "ausente",
        )

    except Exception:
        logger.exception("Erro ao processar mensagem")


# ──────────────────────── Endpoints ────────────────────────


@app.post("/messages-upsert")
@app.post("/messages-upsert/messages-upsert")
async def webhook_messages_upsert(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    """Endpoint webhook que recebe mensagens do Evolution API.

    Equivalente ao nó 'Mensagem recebida' do n8n (POST /messages-upsert).
    Processa a mensagem em background para não bloquear a resposta ao webhook.
    """
    body = await request.json()

    logger.info(
        "Webhook recebido — instância=%s, evento=%s",
        body.get("instance", "unknown"),
        body.get("event", "unknown"),
    )

    # Ignorar mensagens enviadas pelo próprio bot (fromMe) e status broadcast
    data = body.get("data", {})
    key = data.get("key", {})
    if key.get("fromMe", False):
        logger.info("Ignorando mensagem própria (fromMe=true)")
        return JSONResponse(content={"status": "ignored"}, status_code=200)

    remote_jid = key.get("remoteJid", "")
    if remote_jid == "status@broadcast":
        logger.info("Ignorando status broadcast")
        return JSONResponse(content={"status": "ignored"}, status_code=200)

    # Processa em background para responder rapidamente ao webhook
    background_tasks.add_task(process_message, body)

    return JSONResponse(
        content={"status": "received"},
        status_code=200,
    )


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
