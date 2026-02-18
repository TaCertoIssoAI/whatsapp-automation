"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API.

v4.0.0 — Arquitetura production-ready para alta concorrência.

Mudanças vs v3.1.0:
- _QUEUE_WORKERS: 3 → 5
- Semáforo global (_concurrency_sem) limita processamento a _MAX_CONCURRENT
  mensagens simultâneas — evita saturação de CPU/memória/APIs externas
- ThreadPoolExecutor: 20 → 32 threads para Gemini sync calls
- Fila: 500 → 2000 itens (margem maior para picos)
- Dedup simplificado sem asyncio.Lock (dict ops são atômicas no CPython)
- Health check com métricas: concurrency, total_received/processed/errors
- Shutdown fecha clients HTTP dos módulos (whatsapp_api, fact_checker)
- Módulos usam httpx client singleton com connection pool (não cria por request)
- ai_services.py usa asyncio.Semaphore para limitar chamadas Gemini concorrentes
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
logger = logging.getLogger("main")

app = FastAPI(title="TaCertoIssoAI", version="4.0.0")

# ── Constantes de tunning ──
_QUEUE_WORKERS = 5           # workers consumindo a fila
_QUEUE_MAX = 2000            # tamanho máximo da fila
_MAX_CONCURRENT = 30         # máx. de mensagens processando simultaneamente
_THREAD_POOL_SIZE = 32       # threads para chamadas síncronas (Gemini)
_MESSAGE_TIMEOUT = 300       # 5 min por mensagem
_DEDUP_TTL = 300             # 5 min de TTL no cache de dedup

# ── Estado global (inicializado no startup) ──
_workflow = None
_queue: asyncio.Queue
_concurrency_sem: asyncio.Semaphore
_active_tasks: set[asyncio.Task] = set()
_worker_tasks: list[asyncio.Task] = []
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_THREAD_POOL_SIZE)

# ── Contadores ──
_total_received = 0
_total_processed = 0
_total_errors = 0

# ── Dedup (sem lock — operações em dict são atômicas no CPython) ──
_processed_messages: dict[str, float] = {}


def _is_duplicate(message_id: str) -> bool:
    """Verifica duplicação — chamado SÓ dentro dos workers.

    Sem lock: operações dict.__contains__ e dict.__setitem__ são
    atômicas no CPython (GIL). Limpeza lazy quando > 2000 entradas.
    """
    if not message_id:
        return False

    now = time.monotonic()

    # Limpeza lazy
    if len(_processed_messages) > 2000:
        to_delete = [
            k for k, t in _processed_messages.items() if now - t > _DEDUP_TTL
        ]
        for k in to_delete:
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


async def _process_message(body: dict, message_id: str, sender: str) -> None:
    """Processa uma mensagem via LangGraph, com semáforo de concorrência.

    O semáforo _concurrency_sem garante que no máximo _MAX_CONCURRENT
    mensagens processam simultaneamente. Isso evita saturação das APIs
    externas (Gemini, WhatsApp, Fact-check) e protege o event loop.
    """
    global _total_processed, _total_errors

    async with _concurrency_sem:
        logger.info(
            "[%s] Processando (de=%s, concurrent=%d/%d)",
            message_id[:30], sender,
            _MAX_CONCURRENT - _concurrency_sem._value, _MAX_CONCURRENT,
        )
        try:
            initial_state = {
                "raw_body": body,
                "endpoint_api": config.FACT_CHECK_API_URL,
            }
            await asyncio.wait_for(
                _workflow.ainvoke(initial_state),
                timeout=_MESSAGE_TIMEOUT,
            )
            _total_processed += 1
            logger.info("[%s] ✓ Concluído", message_id[:30])

        except asyncio.TimeoutError:
            _total_errors += 1
            logger.error("[%s] ✗ TIMEOUT após %ds", message_id[:30], _MESSAGE_TIMEOUT)
            if sender:
                with suppress(Exception):
                    from nodes import whatsapp_api
                    await whatsapp_api.send_text(
                        sender,
                        "⚠️ O processamento demorou demais e foi cancelado. "
                        "Por favor, tente enviar novamente.",
                    )
        except asyncio.CancelledError:
            logger.warning("[%s] Task cancelada (shutdown?)", message_id[:30])
            raise
        except Exception:
            _total_errors += 1
            logger.exception("[%s] ✗ Erro no processamento", message_id[:30])
            if sender:
                with suppress(Exception):
                    from nodes import whatsapp_api
                    await whatsapp_api.send_text(
                        sender,
                        "⚠️ Ocorreu um erro inesperado. "
                        "Por favor, tente enviar novamente.",
                    )


def _task_done_callback(task: asyncio.Task) -> None:
    """Remove task do tracking e loga erros não tratados."""
    _active_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("Task %s falhou: %s", task.get_name(), exc, exc_info=exc)


async def _queue_worker(worker_id: int) -> None:
    """Worker que consome a fila e despacha mensagens para processamento.

    Faz: JSON parse → dedup → build body → create_task(_process_message).
    O semáforo _concurrency_sem dentro de _process_message garante
    que não há sobrecarga mesmo com muitas tasks simultâneas.
    """
    logger.info("[worker-%d] Iniciado", worker_id)

    while True:
        try:
            payload: bytes = await _queue.get()

            try:
                try:
                    body = json.loads(payload)
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    logger.error("[worker-%d] JSON inválido: %s", worker_id, e)
                    continue

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

                                    if msg_type in (
                                        "reaction", "system", "ephemeral",
                                        "unsupported", "request_welcome",
                                    ):
                                        continue

                                    if _is_duplicate(msg_id):
                                        logger.debug(
                                            "[worker-%d] Dup: %s",
                                            worker_id, msg_id[:20],
                                        )
                                        continue

                                    logger.info(
                                        "[worker-%d] >>> id=%s tipo=%s de=%s",
                                        worker_id, msg_id[:30], msg_type, sender,
                                    )

                                    isolated_body = _build_single_message_body(
                                        body, entry_idx, change_idx, msg_idx,
                                    )

                                    task = asyncio.create_task(
                                        _process_message(
                                            isolated_body, msg_id, sender,
                                        ),
                                        name=f"msg-{msg_id[-12:]}",
                                    )
                                    _active_tasks.add(task)
                                    task.add_done_callback(_task_done_callback)

                                except Exception:
                                    logger.exception(
                                        "[worker-%d] Erro msg %d",
                                        worker_id, msg_idx,
                                    )
                        except Exception:
                            logger.exception(
                                "[worker-%d] Erro change", worker_id,
                            )
            finally:
                _queue.task_done()

        except asyncio.CancelledError:
            logger.info("[worker-%d] Encerrado", worker_id)
            break
        except Exception:
            logger.exception("[worker-%d] Erro inesperado", worker_id)
            await asyncio.sleep(0.1)


# ── Eventos de startup/shutdown ──


@app.on_event("startup")
async def startup_event():
    """Inicializa fila, workers, grafo, executor e valida config."""
    global _workflow, _queue, _concurrency_sem

    logger.info("=" * 60)
    logger.info("TaCertoIssoAI WhatsApp Bot v4.0.0 iniciando...")
    logger.info("=" * 60)

    # Fila de payloads (bytes)
    _queue = asyncio.Queue(maxsize=_QUEUE_MAX)

    # Semáforo de concorrência
    _concurrency_sem = asyncio.Semaphore(_MAX_CONCURRENT)

    # Compilar grafo LangGraph
    try:
        from graph import compile_graph
        _workflow = compile_graph()
        logger.info("Grafo LangGraph compilado com sucesso")
    except Exception:
        logger.exception("FALHA CRÍTICA ao compilar grafo!")

    # ThreadPoolExecutor como executor padrão
    loop = asyncio.get_running_loop()
    loop.set_default_executor(_thread_pool)
    logger.info("ThreadPoolExecutor: %d threads", _THREAD_POOL_SIZE)

    # Iniciar workers
    for i in range(_QUEUE_WORKERS):
        t = asyncio.create_task(_queue_worker(i), name=f"queue-worker-{i}")
        _worker_tasks.append(t)
    logger.info(
        "%d queue workers, max concurrent=%d",
        _QUEUE_WORKERS, _MAX_CONCURRENT,
    )

    # Validar config
    missing = []
    for var_name in (
        "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
        "WHATSAPP_VERIFY_TOKEN", "GOOGLE_GEMINI_API_KEY",
        "FACT_CHECK_API_URL",
    ):
        if not getattr(config, var_name, ""):
            missing.append(var_name)

    if missing:
        logger.error("VARS FALTANDO: %s", ", ".join(missing))
    else:
        logger.info("Config OK")

    if config.WHATSAPP_APP_SECRET:
        logger.info("Assinatura HMAC: ATIVA")
    else:
        logger.warning("Assinatura HMAC: DESATIVADA")

    logger.info("API: %s", config.WHATSAPP_API_BASE_URL)
    logger.info("Fact-check: %s", config.FACT_CHECK_API_URL)
    logger.info("Porta: %d", config.WEBHOOK_PORT)
    logger.info("Servidor pronto!")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Graceful shutdown com timeout."""
    logger.info("Shutdown iniciado...")

    # 1. Cancelar workers
    for t in _worker_tasks:
        t.cancel()
    if _worker_tasks:
        await asyncio.gather(*_worker_tasks, return_exceptions=True)
        logger.info("Workers encerrados")

    # 2. Aguardar tasks ativas (max 30s)
    if _active_tasks:
        logger.info("Aguardando %d tasks (max 30s)...", len(_active_tasks))
        done, pending = await asyncio.wait(
            _active_tasks, timeout=30, return_when=asyncio.ALL_COMPLETED,
        )
        if pending:
            logger.warning("Cancelando %d tasks pendentes", len(pending))
            for task in pending:
                task.cancel()
            await asyncio.wait(pending, timeout=5)

    # 3. Fechar clients HTTP dos módulos
    try:
        from nodes import whatsapp_api, fact_checker
        await whatsapp_api.close_client()
        await fact_checker.close_client()
    except Exception:
        pass

    _thread_pool.shutdown(wait=False)
    logger.info("Shutdown completo")


# ── Middleware de logging ──


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Loga todas as requests HTTP."""
    start = time.monotonic()
    response = await call_next(request)
    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "%s %s → %d (%.0fms)",
        request.method, request.url.path, response.status_code, elapsed,
    )
    return response


# ── Endpoints ──


@app.get("/webhook", response_model=None)
async def webhook_verify(request: Request):
    """Handshake de verificação do Meta."""
    mode = request.query_params.get("hub.mode", "")
    token = request.query_params.get("hub.verify_token", "")
    challenge = request.query_params.get("hub.challenge", "")

    logger.info("Verify: mode=%s token=%s", mode, "***" if token else "(vazio)")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verificado ✓")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Verify falhou (mode=%s)", mode)
    return JSONResponse(content={"error": "Forbidden"}, status_code=403)


@app.post("/webhook")
async def webhook_receive(request: Request) -> JSONResponse:
    """Webhook ACK-only — retorna 200 em < 5ms."""
    global _total_received

    try:
        payload = await request.body()
    except Exception:
        return JSONResponse(content={"status": "ok"}, status_code=200)

    # HMAC (best-effort)
    app_secret = config.WHATSAPP_APP_SECRET
    if app_secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        if sig:
            try:
                expected = hmac.HMAC(
                    app_secret.encode(), payload, hashlib.sha256,
                ).hexdigest()
                received = sig.removeprefix("sha256=")
                if not hmac.compare_digest(expected, received):
                    logger.error("HMAC inválido!")
            except Exception:
                pass

    # Enfileirar
    try:
        _queue.put_nowait(payload)
        _total_received += 1
    except asyncio.QueueFull:
        logger.error("FILA CHEIA (%d)! Payload descartado!", _QUEUE_MAX)

    return JSONResponse(content={"status": "received"}, status_code=200)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check com métricas."""
    try:
        queue_size = _queue.qsize()
    except Exception:
        queue_size = -1

    try:
        concurrent_now = _MAX_CONCURRENT - _concurrency_sem._value
    except Exception:
        concurrent_now = -1

    return JSONResponse(
        content={
            "status": "ok",
            "version": "4.0.0",
            "workflow_ready": _workflow is not None,
            "queue_size": queue_size,
            "active_tasks": len(_active_tasks),
            "concurrency": f"{concurrent_now}/{_MAX_CONCURRENT}",
            "total_received": _total_received,
            "total_processed": _total_processed,
            "total_errors": _total_errors,
            "dedup_cache_size": len(_processed_messages),
            "thread_pool_workers": _THREAD_POOL_SIZE,
        },
        status_code=200,
    )


if __name__ == "__main__":
    logger.info("Iniciando servidor na porta %d...", config.WEBHOOK_PORT)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        workers=1,
        reload=False,
        log_level="info",
        timeout_keep_alive=120,
        limit_concurrency=500,
        limit_max_requests=None,
    )
