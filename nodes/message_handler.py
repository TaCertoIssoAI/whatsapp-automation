"""Handler de pré-processamento de mensagens.

Responsável por registro, termos, debounce, classificação Gemini,
histórico de chat e roteamento para verificação ou conversa.
"""

import asyncio
import copy
import logging
import time
import uuid

from nodes import ai_services, redis_client, whatsapp_api
from graph import compile_graph
import config

logger = logging.getLogger(__name__)

_workflow = None

# Lock por usuário para evitar race conditions em msgs simultâneas do mesmo sender
_user_locks: dict[str, asyncio.Lock] = {}
_user_locks_meta_lock = asyncio.Lock()


async def _get_user_lock(sender: str) -> asyncio.Lock:
    """Retorna um asyncio.Lock exclusivo por usuário (lazy, thread-safe)."""
    if sender not in _user_locks:
        async with _user_locks_meta_lock:
            if sender not in _user_locks:
                _user_locks[sender] = asyncio.Lock()
    return _user_locks[sender]


def _get_workflow():
    global _workflow
    if _workflow is None:
        _workflow = compile_graph()
    return _workflow


# ──────────────────────── Mensagem de boas-vindas ────────────────────────

WELCOME_MESSAGE = (
    "Olá! 👋\n"
    "Obrigado por usar nossa ferramenta de verificação de informações.\n\n"
    "É só enviar a mensagem, imagem, vídeo, link ou áudio que você quer verificar. 😊\n\n"
    "Saiba mais na nossa plataforma online:\n"
    "https://tacertoissoai.com.br\n\n"
    "Termos e Condições e Política de Privacidade: tacertoissoai.com.br/termos-e-privacidade.\n\n"
    "Antes de começarmos, você concorda com nossos Termos e Condições e Política de Privacidade?"
)

TERMS_BUTTONS = [
    {"id": "terms_accept", "title": "✅ Sim"},
    {"id": "terms_reject", "title": "❌ Não"},
]

TERMS_REQUIRED_MESSAGE = (
    "Para continuar usando nosso serviço, você precisa aceitar nossos "
    "Termos e Condições e Política de Privacidade.\n\n"
    "Acesse: tacertoissoai.com.br/termos-e-privacidade\n\n"
    "Você concorda com nossos Termos e Condições e Política de Privacidade?"
)

DELETE_CONFIRMATION_MESSAGE = (
    "Seus dados foram removidos com sucesso. ✅\n"
    "Se quiser usar nosso serviço novamente, é só enviar uma mensagem!"
)

ERROR_MESSAGE = (
    "Desculpe, ocorreu um erro ao processar sua mensagem. 😔\n"
    "Por favor, tente novamente em alguns instantes."
)


# ──────────────────────── Extração de dados da mensagem ────────────────────────


def _extract_message_info(body: dict) -> dict | None:
    """Extrai informações básicas da mensagem do payload do webhook.

    Returns:
        Dict com sender, msg_type, text, msg_id, media_id, caption, button_id, raw_body
        ou None se não houver mensagem.
    """
    entries = body.get("entry", [])
    if not entries:
        return None

    changes = entries[0].get("changes", [])
    if not changes:
        return None

    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    if not messages:
        return None

    message = messages[0]
    msg_type = message.get("type", "")
    sender = message.get("from", "")
    msg_id = message.get("id", "")

    # Extrair texto
    text = ""
    button_id = ""
    if msg_type == "text":
        text = message.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        interactive = message.get("interactive", {})
        button_reply = interactive.get("button_reply", {})
        if button_reply:
            button_id = button_reply.get("id", "")
            text = button_reply.get("title", "")
        list_reply = interactive.get("list_reply", {})
        if list_reply:
            text = list_reply.get("title", "")
    elif msg_type == "button":
        text = message.get("button", {}).get("text", "")

    # Extrair media_id
    media_id = ""
    if msg_type in ("audio", "image", "video", "sticker", "document"):
        media_obj = message.get(msg_type, {})
        media_id = media_obj.get("id", "")

    # Extrair caption
    caption = ""
    if msg_type in ("image", "video"):
        media_obj = message.get(msg_type, {})
        caption = media_obj.get("caption", "")

    return {
        "sender": sender,
        "msg_type": msg_type,
        "text": text,
        "msg_id": msg_id,
        "media_id": media_id,
        "caption": caption,
        "button_id": button_id,
        "raw_body": body,
    }


# ──────────────────────── Handler principal ────────────────────────


async def handle_incoming_message(body: dict) -> None:
    """Handler principal — processa mensagens antes do grafo LangGraph.

    Usa lock per-user para garantir que a seção crítica de
    registro/termos não sofra race condition quando o mesmo
    usuário envia várias mensagens simultâneas.
    """
    info = _extract_message_info(body)
    if not info:
        return

    sender = info["sender"]
    msg_type = info["msg_type"]
    text = info["text"]
    msg_id = info["msg_id"]
    button_id = info["button_id"]

    # Lock por usuário: protege registro/termos contra msgs simultâneas
    user_lock = await _get_user_lock(sender)
    async with user_lock:
        if msg_type == "text" and text.strip().lower() == "/delete":
            await _handle_delete(sender, msg_id)
            return

        if button_id in ("terms_accept", "terms_reject"):
            await _handle_terms_response(sender, msg_id, button_id, info)
            return

        is_registered = await redis_client.is_user_registered(sender)
        if not is_registered:
            await _handle_new_user(sender, msg_id, info)
            return

        terms_status = await redis_client.get_terms_status(sender)
        if terms_status != "yes":
            await _handle_terms_not_accepted(sender, msg_id, info)
            return

        await whatsapp_api.mark_as_read(msg_id)

        if msg_type == "document":
            await whatsapp_api.send_text(
                sender,
                "Eu não consigo analisar documentos, você pode enviar um texto, "
                "um áudio, uma imagem ou um vídeo para eu analisar.",
                quoted_message_id=msg_id,
            )
            return

    # Debounce e classificação rodam fora do lock (longa duração)
    await _handle_message_with_debounce(sender, info)


# ──────────────────────── Handlers específicos ────────────────────────


async def _handle_delete(sender: str, msg_id: str) -> None:
    """Processa o comando /delete."""
    await whatsapp_api.mark_as_read(msg_id)
    await redis_client.unregister_user(sender)
    await whatsapp_api.send_text(sender, DELETE_CONFIRMATION_MESSAGE)


async def _handle_terms_response(
    sender: str, msg_id: str, button_id: str, info: dict
) -> None:
    """Processa a resposta aos botões de termos (Sim/Não)."""
    await whatsapp_api.mark_as_read(msg_id)

    if button_id == "terms_accept":
        await redis_client.set_terms_status(sender, True)
        await whatsapp_api.send_text(
            sender,
            "Ótimo! ✅ Você aceitou os Termos e Condições.\n\n"
            "Agora é só enviar a mensagem, imagem, vídeo, link ou áudio "
            "que você quer verificar. 😊",
        )

        pending = await redis_client.get_and_clear_pending_messages(sender)
        if pending:
            for msg in pending:
                if msg.get("msg_type") == "text" and msg.get("text"):
                    await redis_client.add_chat_message(sender, "user", msg["text"])
                elif msg.get("caption"):
                    await redis_client.add_chat_message(
                        sender, "user", f"[mídia com legenda: {msg['caption']}]"
                    )
                elif msg.get("msg_type") in ("audio", "image", "video", "sticker"):
                    await redis_client.add_chat_message(
                        sender, "user", f"[{msg['msg_type']}]"
                    )

            try:
                await _process_with_classification(sender, pending)
            except Exception:
                logger.exception("Erro ao processar pendentes para %s", sender)
                await whatsapp_api.send_text(sender, ERROR_MESSAGE)
    else:
        await redis_client.set_terms_status(sender, False)
        await whatsapp_api.send_text(
            sender,
            "Entendido. Sem a aceitação dos Termos e Condições, "
            "não podemos processar suas solicitações.\n\n"
            "Se mudar de ideia, é só enviar uma mensagem! 😊",
        )


async def _handle_new_user(sender: str, msg_id: str, info: dict) -> None:
    """Registra novo usuário atomicamente e envia boas-vindas com debounce."""
    await whatsapp_api.mark_as_read(msg_id)

    # Registrar + definir termos="no" atomicamente (pipeline)
    await redis_client.register_user_with_terms(sender)

    await _save_pending_message(sender, info)

    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        return

    await redis_client.clear_debounce_lock(sender)
    await whatsapp_api.send_interactive_buttons(
        sender, WELCOME_MESSAGE, TERMS_BUTTONS,
    )


async def _handle_terms_not_accepted(
    sender: str, msg_id: str, info: dict
) -> None:
    """Salva mensagem como pendente e reenvia botões de termos com debounce."""
    await whatsapp_api.mark_as_read(msg_id)
    await _save_pending_message(sender, info)

    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        return

    await redis_client.clear_debounce_lock(sender)
    await whatsapp_api.send_interactive_buttons(
        sender, TERMS_REQUIRED_MESSAGE, TERMS_BUTTONS,
    )


# ──────────────────────── Debounce e classificação ────────────────────────


async def _save_pending_message(sender: str, info: dict) -> None:
    """Salva metadados da mensagem na lista pendente do Redis."""
    msg_data = {
        "msg_type": info["msg_type"],
        "text": info["text"],
        "msg_id": info["msg_id"],
        "media_id": info.get("media_id", ""),
        "caption": info.get("caption", ""),
        "timestamp": time.time(),
        "raw_body": info.get("raw_body"),
    }
    await redis_client.add_pending_message(sender, msg_data)


async def _handle_message_with_debounce(sender: str, info: dict) -> None:
    """Salva mensagem, aplica debounce de 1s e processa o batch acumulado."""
    await _save_pending_message(sender, info)

    if info["msg_type"] == "text" and info["text"]:
        await redis_client.add_chat_message(sender, "user", info["text"])
    elif info.get("caption"):
        await redis_client.add_chat_message(sender, "user", f"[mídia com legenda: {info['caption']}]")
    elif info["msg_type"] in ("audio", "image", "video", "sticker"):
        await redis_client.add_chat_message(sender, "user", f"[{info['msg_type']}]")

    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        return

    await redis_client.clear_debounce_lock(sender)

    pending = await redis_client.get_and_clear_pending_messages(sender)
    if not pending:
        return

    await _process_with_classification(sender, pending)


async def _process_with_classification(sender: str, pending: list[dict]) -> None:
    """Classifica e processa mensagens pendentes.

    Mídia → verificação direta. Texto → classificação Gemini.
    Verifica interrupções (novas msgs) antes de processar.
    """
    has_media = any(
        msg.get("msg_type") in ("audio", "image", "video", "sticker")
        for msg in pending
    )

    if has_media:
        try:
            await _run_verification(sender, pending)
        except Exception:
            logger.exception("Erro na verificação para %s", sender)
            await whatsapp_api.send_text(sender, ERROR_MESSAGE)
        return

    text_messages = [
        msg.get("text", "") for msg in pending if msg.get("text")
    ]
    if not text_messages:
        return

    last_msg_id = pending[-1].get("msg_id", "")
    typing_task = None
    if last_msg_id:
        typing_task = await whatsapp_api.start_typing_loop(last_msg_id)

    try:
        classification = await ai_services.classify_message(text_messages)

        new_pending_count = await redis_client.get_pending_message_count(sender)
        if new_pending_count > 0:
            if typing_task:
                typing_task.cancel()
            if classification == "VERIFICAR":
                await _run_verification(sender, pending)
            return

        if classification == "VERIFICAR":
            if typing_task:
                typing_task.cancel()
            await _run_verification(sender, pending)
        else:
            await _run_conversation(sender, text_messages, last_msg_id, typing_task)
            typing_task = None
    except Exception:
        logger.exception("Erro no processamento para %s", sender)
        if typing_task:
            typing_task.cancel()
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)


async def _run_verification(sender: str, pending: list[dict]) -> None:
    """Executa verificação via grafo LangGraph (prioridade: mídia > texto)."""
    target_msg = None
    for msg in reversed(pending):
        if msg.get("msg_type") in ("audio", "image", "video", "sticker"):
            target_msg = msg
            break

    if target_msg is None:
        combined_text = " ".join(
            msg.get("text", "") for msg in pending if msg.get("text")
        )
        target_msg = pending[-1]
        raw_body = target_msg.get("raw_body", {})
        if raw_body:
            raw_body = copy.deepcopy(raw_body)
            try:
                msg_obj = raw_body["entry"][0]["changes"][0]["value"]["messages"][0]
                if msg_obj.get("type") == "text":
                    msg_obj["text"]["body"] = combined_text
            except (KeyError, IndexError):
                pass
            target_msg = {**target_msg, "raw_body": raw_body}

    raw_body = target_msg.get("raw_body")
    if not raw_body:
        logger.error("Sem raw_body para verificação do usuário %s", sender)
        return

    try:
        workflow = _get_workflow()
        result = await workflow.ainvoke({
            "raw_body": raw_body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        })

        rationale = result.get("rationale", "")
        if rationale:
            await redis_client.add_chat_message(sender, "bot", rationale)
    except Exception:
        logger.exception("Erro na verificação para %s", sender)
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)


async def _run_conversation(
    sender: str,
    text_messages: list[str],
    last_msg_id: str,
    typing_task: asyncio.Task | None = None,
) -> None:
    """Gera e envia resposta conversacional via Gemini."""
    try:
        chat_history = await redis_client.get_chat_history(sender)
        response = await ai_services.generate_chat_response(text_messages, chat_history)

        new_pending_count = await redis_client.get_pending_message_count(sender)
        if new_pending_count > 0:
            if typing_task:
                typing_task.cancel()
            return

        if typing_task:
            typing_task.cancel()

        await whatsapp_api.send_text(
            sender,
            response,
            quoted_message_id=last_msg_id if last_msg_id else None,
        )
        await redis_client.add_chat_message(sender, "bot", response)
    except Exception:
        logger.exception("Erro na resposta conversacional para %s", sender)
        if typing_task:
            typing_task.cancel()
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)
