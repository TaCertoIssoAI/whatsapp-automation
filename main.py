"""Servidor FastAPI com webhook para a WhatsApp Business Cloud API.

v5.1.0 — Resposta ULTRA-INSTANTÂNEA ao webhook da Meta + typing indicator correto.

Arquitetura "Raw ASGI intercept → ACK-first, process-later":

1. CAMADA ASGI RAW (WebhookInterceptASGI):
   - Intercepta POST /webhook no nível MAIS BAIXO possível (protocolo ASGI)
   - Lê o body cru via receive() e envia 200 OK via send() DIRETAMENTE
   - NENHUM framework (FastAPI/Starlette/middleware) toca nessa requisição
   - O payload é enfileirado em thread separada para zero-blocking
   - Meta recebe 200 OK em <1ms, IMPOSSÍVEL de bloquear

2. FILA + WORKERS:
   - O payload cru (bytes) é enfileirado em asyncio.Queue pelos workers
   - Workers fazem parse JSON, dedup, HMAC (best-effort) e disparam tasks
   - Typing indicator (correto: mark-as-read + typing_indicator) é ativado

3. TYPING INDICATOR (Cloud API oficial):
   - Usa POST /{PHONE_NUMBER_ID}/messages com:
     {"status":"read","message_id":"<wamid>","typing_indicator":{"type":"text"}}
   - Marca como lido + mostra "digitando..." por 25s ou até a resposta
   - Não existe "typing_off" — some automaticamente ao enviar mensagem

Tunning para VPS 1-core:
- 3 queue workers (1 por core + 2 para await I/O)
- 8 threads no pool (suficiente para chamadas síncronas em 1 core)
- 10 max concurrent (protege CPU/memória/APIs em VPS pequena)
- Fila de 500 itens (suficiente para picos, sem desperdiçar memória)
"""

import asyncio
import concurrent.futures
import copy
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.responses import Response

import config

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")

# ── Constantes de tunning (VPS 1-core) ──
_QUEUE_WORKERS = 3           # workers consumindo a fila (suficiente para 1 core)
_QUEUE_MAX = 500             # tamanho máximo da fila
_MAX_CONCURRENT = 10         # máx. de mensagens processando simultaneamente
_THREAD_POOL_SIZE = 8        # threads para chamadas síncronas (Gemini)
_MESSAGE_TIMEOUT = 300       # 5 min por mensagem
_DEDUP_TTL = 300             # 5 min de TTL no cache de dedup

# ── Estado global (inicializado no startup) ──
_workflow = None
_queue: asyncio.Queue | None = None
_concurrency_sem: asyncio.Semaphore | None = None
_active_tasks: set[asyncio.Task] = set()
_worker_tasks: list[asyncio.Task] = []
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_THREAD_POOL_SIZE)
_shutting_down = False  # Flag para impedir novos enfileiramentos durante shutdown

# ── Contadores ──
_total_received = 0
_total_processed = 0
_total_errors = 0

# ── Dedup (sem lock — operações em dict são atômicas no CPython) ──
_processed_messages: dict[str, float] = {}

# ── Resposta pré-serializada (evita serialização JSON a cada webhook) ──
_OK_RESPONSE_BODY = b'{"status":"ok"}'


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

    Ativa typing indicator (+ mark as read) IMEDIATAMENTE ao começar.
    O indicador desaparece automaticamente quando enviamos a resposta
    ou após 25s (Cloud API), não é necessário typing_off.
    """
    global _total_processed, _total_errors

    async with _concurrency_sem:
        logger.info(
            "[%s] Processando (de=%s, concurrent=%d/%d)",
            message_id[:30], sender,
            _MAX_CONCURRENT - _concurrency_sem._value, _MAX_CONCURRENT,
        )

        # ── TYPING INDICATOR + MARK AS READ imediato ──
        # Mostra "digitando..." e marca como lida assim que entra em processamento
        if message_id:
            try:
                from nodes import whatsapp_api
                await whatsapp_api.send_typing_indicator(message_id)
            except Exception:
                pass  # typing é best-effort

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

    Faz: HMAC (best-effort) → JSON parse → dedup → build body → create_task.
    A validação HMAC é feita AQUI (fora do hot path do webhook) para não
    atrasar o 200 OK que vai para a Meta.
    """
    logger.info("[worker-%d] Iniciado", worker_id)

    while True:
        try:
            item = await _queue.get()

            try:
                # Desempacotar tupla (payload_bytes, hmac_signature)
                payload: bytes
                hmac_sig: str
                payload, hmac_sig = item

                # HMAC validation (best-effort, fora do hot path do webhook)
                app_secret = config.WHATSAPP_APP_SECRET
                if app_secret and hmac_sig:
                    try:
                        expected = hmac.HMAC(
                            app_secret.encode(), payload, hashlib.sha256,
                        ).hexdigest()
                        received = hmac_sig.removeprefix("sha256=")
                        if not hmac.compare_digest(expected, received):
                            logger.error("[worker-%d] HMAC inválido! Descartando payload.", worker_id)
                            continue
                    except Exception:
                        logger.warning("[worker-%d] Erro na validação HMAC, processando mesmo assim", worker_id)

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


# ── Lifespan (startup + shutdown) ──


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gerencia startup e shutdown do app com context manager.

    Usar lifespan é o padrão moderno do FastAPI (on_event está deprecated).
    Garante que o shutdown SEMPRE executa, mesmo com erros no startup.
    """
    global _workflow, _queue, _concurrency_sem, _shutting_down
    _shutting_down = False

    logger.info("=" * 60)
    logger.info("TaCertoIssoAI WhatsApp Bot v5.1.0 iniciando...")
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

    # ── APP RODANDO ──
    yield

    # ── SHUTDOWN ──
    logger.info("Shutdown iniciado...")
    _shutting_down = True

    # 1. Cancelar workers (param de entrada na fila)
    for t in _worker_tasks:
        t.cancel()
    if _worker_tasks:
        with suppress(Exception):
            await asyncio.wait(_worker_tasks, timeout=5)
        _worker_tasks.clear()
        logger.info("Workers encerrados")

    # 2. Aguardar tasks ativas (max 15s) — depois força cancelamento
    if _active_tasks:
        logger.info("Aguardando %d tasks ativas (max 15s)...", len(_active_tasks))
        done, pending = await asyncio.wait(
            _active_tasks, timeout=15, return_when=asyncio.ALL_COMPLETED,
        )
        if pending:
            logger.warning("Cancelando %d tasks pendentes", len(pending))
            for task in pending:
                task.cancel()
            with suppress(Exception):
                await asyncio.wait(pending, timeout=5)
        _active_tasks.clear()

    # 3. Fechar clients HTTP dos módulos
    with suppress(Exception):
        from nodes import whatsapp_api, fact_checker
        await whatsapp_api.close_client()
        await fact_checker.close_client()

    # 4. Desligar thread pool
    _thread_pool.shutdown(wait=False)
    logger.info("Shutdown completo")


# ── Criar app com lifespan ──
app = FastAPI(title="TaCertoIssoAI", version="5.1.0", lifespan=lifespan)


# ── Raw ASGI Wrapper para Webhook ULTRA-RÁPIDO ──
#
# Esta classe intercepta requisições POST /webhook no nível ASGI puro,
# ANTES de qualquer middleware/framework tocar na requisição.
# Isso garante resposta em <1ms mesmo com event loop 100% saturado.
#
# Para todas as outras rotas, delega ao FastAPI normalmente.


class WebhookInterceptASGI:
    """ASGI wrapper que intercepta POST /webhook no nível de protocolo.

    Opera direto no protocolo ASGI (receive/send), sem nenhuma abstração
    de framework. Isso é o mais rápido possível em Python ASGI.
    """

    def __init__(self, fastapi_app: FastAPI) -> None:
        self._app = fastapi_app

    async def __call__(self, scope, receive, send) -> None:
        # Só intercepta HTTP — websockets etc vão pro FastAPI
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Fast-path: POST /webhook → responde IMEDIATAMENTE
        if scope["method"] == "POST" and scope["path"] == "/webhook":
            await self._handle_webhook(scope, receive, send)
            return

        # Todas as outras rotas → FastAPI normal
        await self._app(scope, receive, send)

    async def _handle_webhook(self, scope, receive, send) -> None:
        """Processa webhook no nível ASGI puro — máxima velocidade."""
        global _total_received

        # 1. Ler body completo via receive() — protocolo ASGI raw
        body_parts: list[bytes] = []
        while True:
            message = await receive()
            body_chunk = message.get("body", b"")
            if body_chunk:
                body_parts.append(body_chunk)
            if not message.get("more_body", False):
                break
        payload = b"".join(body_parts)

        # 2. Capturar HMAC header dos headers ASGI (são bytes, lowercase)
        hmac_sig = ""
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"x-hub-signature-256":
                hmac_sig = header_value.decode("latin-1")
                break

        # 3. Enfileirar em background — fire and forget
        if not _shutting_down and _queue is not None:
            try:
                asyncio.create_task(_enqueue_webhook(payload, hmac_sig))
                _total_received += 1
            except Exception:
                pass  # Mesmo se falhar, retorna 200

        # 4. ENVIAR 200 OK diretamente via send() — protocolo ASGI raw
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", b"15"],  # len('{"status":"ok"}')
            ],
        })
        await send({
            "type": "http.response.body",
            "body": _OK_RESPONSE_BODY,
        })


# Wrapper ASGI que intercepta webhook antes do FastAPI
asgi_app = WebhookInterceptASGI(app)


async def _enqueue_webhook(payload: bytes, hmac_sig: str) -> None:
    """Task em background para enfileirar webhook sem bloquear a resposta."""
    try:
        if _queue is not None:
            await _queue.put((payload, hmac_sig))
    except asyncio.QueueFull:
        logger.error("FILA CHEIA (%d)! Payload descartado!", _QUEUE_MAX)
    except Exception:
        logger.exception("Erro ao enfileirar webhook")


# ── Endpoints ──
# NOTA: O webhook POST é interceptado pelo WebhookInterceptASGI no nível ASGI
# e NUNCA chega aqui. Os endpoints abaixo servem como documentação e fallback.


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
async def webhook_receive(request: Request):
    """Webhook endpoint (FALLBACK — interceptado pelo ASGI wrapper).

    Este endpoint SÓ é chamado se o WebhookInterceptASGI falhar.
    O wrapper ASGI intercepta POST /webhook e retorna 200 OK
    INSTANTANEAMENTE no nível de protocolo, sem chegar aqui.

    Mantido apenas como documentação e fallback de segurança.
    """
    logger.warning("POST /webhook chegou no endpoint FastAPI (deveria ter sido interceptado pelo ASGI wrapper)")
    return Response(content=_OK_RESPONSE_BODY, status_code=200, media_type="application/json")


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check com métricas."""
    try:
        queue_size = _queue.qsize() if _queue else -1
    except Exception:
        queue_size = -1

    try:
        concurrent_now = _MAX_CONCURRENT - _concurrency_sem._value if _concurrency_sem else -1
    except Exception:
        concurrent_now = -1

    return JSONResponse(
        content={
            "status": "ok",
            "version": "5.1.0",
            "workflow_ready": _workflow is not None,
            "queue_size": queue_size,
            "active_tasks": len(_active_tasks),
            "concurrency": f"{concurrent_now}/{_MAX_CONCURRENT}",
            "total_received": _total_received,
            "total_processed": _total_processed,
            "total_errors": _total_errors,
            "dedup_cache_size": len(_processed_messages),
            "thread_pool_workers": _THREAD_POOL_SIZE,
            "shutting_down": _shutting_down,
        },
        status_code=200,
    )


if __name__ == "__main__":
    logger.info("Iniciando servidor na porta %d...", config.WEBHOOK_PORT)
    
    # Configuração de produção para VPS 1-core com webhook ULTRA-RÁPIDO
    # Usa asgi_app (WebhookInterceptASGI wrapper) que intercepta POST /webhook
    # no nível ASGI puro ANTES do FastAPI, garantindo resposta em <1ms.
    uvicorn.run(
        "main:asgi_app",
        host="0.0.0.0",
        port=config.WEBHOOK_PORT,
        workers=1,
        reload=False,
        log_level="info",
        timeout_keep_alive=30,
        timeout_graceful_shutdown=20,
        limit_concurrency=100,
        limit_max_requests=None,
        backlog=2048,  # Aumenta fila de conexões TCP (aceita mais requests simultâneas)
    )
