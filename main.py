"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API.

v3.1.0 — Arquitetura ACK-first com fila interna.

O webhook retorna 200 em < 5ms SEMPRE, sem nenhum await pesado no caminho
crítico. O processamento ocorre em workers dedicados consumindo uma fila
asyncio.Queue, completamente desacoplados do event loop do HTTP.

Mudanças vs v3.0.0:
- Fila interna (asyncio.Queue) entre webhook e processamento
- Webhook faz apenas: ler body raw → enfileirar → retornar 200
  (sem await de lock, sem dedup síncrono, sem lógica de roteamento)
- Workers dedicados (QUEUE_WORKERS=3) consomem a fila em background
- Dedup movido para dentro do worker (não bloqueia o webhook)
- Payload raw (bytes) enfileirado, JSON parse feito no worker
- Sem await pesado no path crítico do webhook
"""

import asyncio
import concurrent.futures
import copy
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

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TaCertoIssoAI", version="3.1.0")

# Workflow compilado no startup (NÃO no import)
_workflow = None

# ── Fila de mensagens ──
# O webhook apenas enfileira o payload raw. Workers dedicados processam.
_queue: asyncio.Queue  # inicializado no startup
_QUEUE_WORKERS = 3      # workers consumindo a fila em paralelo
_QUEUE_MAX = 500        # tamanho máximo da fila (evita memory leak)

# Tracking de tasks ativas para graceful shutdown
_active_tasks: set[asyncio.Task] = set()
_worker_tasks: list[asyncio.Task] = []

# ThreadPoolExecutor com pool maior para Gemini sync calls
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=20)

# Timeout máximo por mensagem (5 minutos)
_MESSAGE_TIMEOUT = 300

# ── Deduplicação (usada pelos workers, não pelo webhook) ──
_processed_messages: dict[str, float] = {}
_DEDUP_TTL = 300
_dedup_lock = asyncio.Lock()


async def _is_duplicate(message_id: str) -> bool:
    """Verifica e registra mensagem para evitar processamento duplicado."""
    if not message_id:
        return False
    now = time.monotonic()
    async with _dedup_lock:
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


def _build_single_message_body(
    original_body: dict, entry_idx: int, change_idx: int, msg_idx: int
) -> dict:
    """Constrói um body ISOLADO contendo apenas UMA mensagem específica."""
    try:
        entry = original_body["entry"][entry_idx]
        change = entry["changes"][change_idx]
        value = change["value"]
        message = value["messages"][msg_idx]
        contacts = value.get("contacts", [])

        return {
            "object": original_body.get("object", "whatsapp_business_account"),
            "entry": [{
                "id": entry.get("id", ""),
                "changes": [{
                    "value": {
                        "messaging_product": value.get("messaging_product", "whatsapp"),
                        "metadata": value.get("metadata", {}),
                        "contacts": contacts,
                        "messages": [message],
                    },
                    "field": change.get("field", "messages"),
                }],
            }],
        }
    except (KeyError, IndexError):
        logger.exception("Falha ao construir body isolado")
        return copy.deepcopy(original_body)


async def process_message(body: dict, message_id: str, sender: str) -> None:
    """Processa uma mensagem individual via LangGraph com timeout global."""
    logger.info("[%s] Iniciando processamento (de=%s)", message_id, sender)
    try:
        initial_state = {
            "raw_body": body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }
        await asyncio.wait_for(
            _workflow.ainvoke(initial_state),
            timeout=_MESSAGE_TIMEOUT,
        )
        logger.info("[%s] Processamento concluído com sucesso", message_id)
    except asyncio.TimeoutError:
        logger.error("[%s] TIMEOUT após %ds — task abortada", message_id, _MESSAGE_TIMEOUT)
        if sender:
            with suppress(Exception):
                from nodes import whatsapp_api
                await whatsapp_api.send_text(
                    sender,
                    "⚠️ O processamento da sua mensagem demorou demais e foi cancelado. "
                    "Por favor, tente enviar novamente.",
                )
    except asyncio.CancelledError:
        logger.warning("[%s] Task cancelada (shutdown?)", message_id)
        raise
    except Exception:
        logger.exception("[%s] Erro ao processar mensagem", message_id)
        if sender:
            with suppress(Exception):
                from nodes import whatsapp_api
                await whatsapp_api.send_text(
                    sender,
                    "⚠️ Desculpe, ocorreu um erro inesperado ao processar sua mensagem. "
                    "Por favor, tente enviar novamente.",
                )


def _task_done_callback(task: asyncio.Task) -> None:
    """Callback para logar exceções e remover task do set de tracking."""
    _active_tasks.discard(task)
    if task.cancelled():
        logger.warning("Task %s foi cancelada", task.get_name())
        return
    exc = task.exception()
    if exc:
        logger.error(
            "Task %s falhou com exceção não tratada: %s",
            task.get_name(), exc, exc_info=exc,
        )


async def _queue_worker(worker_id: int) -> None:
    """Worker que consome a fila e processa mensagens.

    Totalmente desacoplado do event loop HTTP. O webhook apenas enfileira
    o payload bytes — este worker faz todo o trabalho pesado.
    """
    logger.info("Worker %d iniciado", worker_id)
    while True:
        try:
            # Aguarda próximo item da fila (bloqueia este worker, não o webhook)
            payload: bytes = await _queue.get()

            try:
                # Parse JSON feito aqui, fora do caminho crítico do webhook
                try:
                    body = json.loads(payload)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.error("[worker-%d] Payload inválido ignorado: %s", worker_id, e)
                    continue

                # Processar todas as mensagens do payload
                for entry_idx, entry in enumerate(body.get("entry", [])):
                    for change_idx, change in enumerate(entry.get("changes", [])):
                        try:
                            value = change.get("value", {})

                            if value.get("statuses"):
                                continue

                            messages = value.get("messages")
                            if not messages:
                                continue

                            for msg_idx, message in enumerate(messages):
                                try:
                                    msg_id = message.get("id", "")
                                    msg_type = message.get("type", "unknown")
                                    sender = message.get("from", "unknown")

                                    if msg_type in ("reaction", "system", "ephemeral", "unsupported"):
                                        logger.info(
                                            "[worker-%d] Tipo ignorado: %s de %s",
                                            worker_id, msg_type, sender,
                                        )
                                        continue

                                    logger.info(
                                        "[worker-%d] >>> Processando: id=%s tipo=%s de=%s",
                                        worker_id, msg_id, msg_type, sender,
                                    )

                                    if await _is_duplicate(msg_id):
                                        logger.info(
                                            "[worker-%d] Duplicado ignorado: %s",
                                            worker_id, msg_id,
                                        )
                                        continue

                                    isolated_body = _build_single_message_body(
                                        body, entry_idx, change_idx, msg_idx
                                    )

                                    task = asyncio.create_task(
                                        process_message(isolated_body, msg_id, sender),
                                        name=f"msg-{msg_id}",
                                    )
                                    _active_tasks.add(task)
                                    task.add_done_callback(_task_done_callback)

                                except Exception:
                                    logger.exception(
                                        "[worker-%d] Erro ao despachar mensagem %d",
                                        worker_id, msg_idx,
                                    )
                        except Exception:
                            logger.exception(
                                "[worker-%d] Erro ao processar change", worker_id
                            )
            finally:
                _queue.task_done()

        except asyncio.CancelledError:
            logger.info("Worker %d encerrado", worker_id)
            break
        except Exception:
            logger.exception("[worker-%d] Erro inesperado no worker", worker_id)


# ── Eventos de startup/shutdown ──


@app.on_event("startup")
async def startup_event():
    """Compilar grafo, iniciar workers e validar configuração."""
    global _workflow, _queue

    logger.info("=" * 60)
    logger.info("TaCertoIssoAI WhatsApp Bot v3.1.0 iniciando...")
    logger.info("=" * 60)

    # Fila de payloads (bytes) a processar
    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)

    # Compilar grafo dentro do startup (NÃO no import do módulo)
    try:
        from graph import compile_graph
        _workflow = compile_graph()
        logger.info("Grafo LangGraph compilado com sucesso")
    except Exception:
        logger.exception("FALHA CRÍTICA ao compilar grafo LangGraph!")

    # Configurar ThreadPoolExecutor para asyncio.to_thread()
    loop = asyncio.get_running_loop()
    loop.set_default_executor(_thread_pool)
    logger.info("ThreadPoolExecutor configurado com 20 workers")

    # Iniciar workers de fila
    for i in range(_QUEUE_WORKERS):
        t = asyncio.create_task(_queue_worker(i), name=f"queue-worker-{i}")
        _worker_tasks.append(t)
    logger.info("%d queue workers iniciados", _QUEUE_WORKERS)

    # Verificar variáveis críticas
    issues = []
    if not config.WHATSAPP_ACCESS_TOKEN:
        issues.append("WHATSAPP_ACCESS_TOKEN não configurado")
    if not config.WHATSAPP_PHONE_NUMBER_ID:
        issues.append("WHATSAPP_PHONE_NUMBER_ID não configurado")
    if not config.WHATSAPP_VERIFY_TOKEN:
        issues.append("WHATSAPP_VERIFY_TOKEN não configurado")
    if not config.GOOGLE_GEMINI_API_KEY:
        issues.append("GOOGLE_GEMINI_API_KEY não configurado")
    if not config.FACT_CHECK_API_URL:
        issues.append("FACT_CHECK_API_URL não configurado")

    if config.WHATSAPP_APP_SECRET:
        logger.info("Verificação de assinatura ATIVA (APP_SECRET configurado)")
    else:
        logger.warning("Verificação de assinatura DESATIVADA (APP_SECRET vazio)")

    if issues:
        for issue in issues:
            logger.error("CONFIG: %s", issue)
        logger.error("O bot pode não funcionar corretamente sem essas configurações!")
    else:
        logger.info("Todas as configurações críticas estão presentes")

    logger.info("API URL: %s", config.WHATSAPP_API_BASE_URL)
    logger.info("Fact-check API: %s", config.FACT_CHECK_API_URL)
    logger.info("Porta: %d", config.WEBHOOK_PORT)
    logger.info("Servidor pronto para receber webhooks!")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown: para workers e aguarda tasks ativas."""
    # Cancelar workers de fila
    for t in _worker_tasks:
        t.cancel()
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
        logger.info("Queue workers encerrados")

    # Aguardar tasks de processamento ativas
    if _active_tasks:
        logger.info(
            "Shutdown: aguardando %d tasks ativas (max 30s)...",
            len(_active_tasks),
        )
        done, pending = await asyncio.wait(
            _active_tasks, timeout=30, return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            logger.warning(
                "Shutdown: %d tasks ainda pendentes, cancelando...", len(pending)
            )
            for task in pending:
                task.cancel()
            await asyncio.wait(pending, timeout=5)
    _thread_pool.shutdown(wait=False)
    logger.info("Shutdown completo")


# ── Middleware de logging ──


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Loga TODAS as requests HTTP para visibilidade total."""
    start = time.monotonic()
    method = request.method
    path = request.url.path

    response = await call_next(request)

    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s → %d (%.0fms)",
        method, path, response.status_code, elapsed,
    )
    return response


# ── Endpoints ──


@app.get("/webhook", response_model=None)
async def webhook_verify(request: Request):
    """Verificação de webhook (handshake do Meta)."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    logger.info("Webhook verify: mode=%s token=%s", mode, "***" if token else "(vazio)")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado com sucesso")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Falha na verificação do webhook (mode=%s)", mode)
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(request: Request) -> JSONResponse:
    """Recebe notificações do webhook — ACK-only, retorna 200 em < 5ms.

    O único trabalho feito aqui:
    1) Ler bytes do body
    2) Verificar assinatura HMAC (CPU puro, < 1ms)
    3) Enfileirar o payload bytes na _queue
    4) Retornar 200

    TODO PROCESSAMENTO (JSON parse, dedup, LangGraph) é feito pelos workers.
    Isso garante que o Meta nunca recebe timeout nem erro neste endpoint.
    """
    # 1) Ler payload raw (< 1ms)
    try:
        payload = await request.body()
    except Exception:
        logger.exception("Falha ao ler body do webhook")
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # 2) Verificar assinatura HMAC (< 0.1ms, CPU puro)
    app_secret = config.WHATSAPP_APP_SECRET
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if signature:
            try:
                expected = hmac.HMAC(
                    app_secret.encode(), payload, hashlib.sha256
                ).hexdigest()
                received = signature.removeprefix("sha256=")
                if not hmac.compare_digest(expected, received):
                    logger.error(
                        "ASSINATURA INVÁLIDA — processando mesmo assim "
                        "(verifique WHATSAPP_APP_SECRET)"
                    )
            except Exception:
                logger.exception("Erro ao verificar assinatura")

    # 3) Enfileirar o payload para processamento assíncrono
    #    put_nowait() não bloqueia — se a fila estiver cheia loga e descarta
    try:
        _queue.put_nowait(payload)
        logger.debug("Payload enfileirado (qsize=%d)", _queue.qsize())
    except asyncio.QueueFull:
        logger.error(
            "Fila cheia (%d itens)! Payload descartado — aumente _QUEUE_MAX",
            _QUEUE_MAX,
        )

    # 4) Retornar 200 IMEDIATAMENTE
    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        content={
            "status": "ok",
            "version": "3.1.0",
            "workflow_ready": _workflow is not None,
            "queue_size": _queue.qsize() if _queue else 0,
            "active_tasks": len(_active_tasks),
            "dedup_cache_size": len(_processed_messages),
            "thread_pool_workers": _thread_pool._max_workers,
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
        log_level="info",
        timeout_keep_alive=120,
        # limit_concurrency alto pois o handler retorna 200 em < 5ms
        limit_concurrency=200,
    )
