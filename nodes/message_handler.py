"""Handler de pré-processamento de mensagens.

Responsável por:
1. Registro de usuário e envio de boas-vindas com termos (com debounce de 1s)
2. Verificação de aceitação dos termos
3. Comando /delete
4. Debounce de 1 segundo para agrupar mensagens
5. Classificação via Gemini (verificar vs conversar)
6. Histórico de chat de 5 minutos
7. Envio de erros ao usuário em caso de falha
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

# Grafo LangGraph compilado uma vez
_workflow = None


def _get_workflow():
    """Retorna o grafo LangGraph (lazy init)."""
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
    """Handler principal que processa mensagens antes do grafo LangGraph.

    Fluxo:
    1. Extrair dados da mensagem
    2. Verificar /delete
    3. Verificar se é resposta aos botões de termos
    4. Verificar se usuário está registrado (se não, registrar + boas-vindas)
    5. Verificar se aceitou os termos
    6. Debounce de 1s + classificação Gemini
    7. Se VERIFICAR → grafo LangGraph
    8. Se CONVERSAR → resposta conversacional via Gemini
    """
    info = _extract_message_info(body)
    if not info:
        return

    sender = info["sender"]
    msg_type = info["msg_type"]
    text = info["text"]
    msg_id = info["msg_id"]
    button_id = info["button_id"]

    logger.info(
        "handle_incoming_message — sender=%s, type=%s, button_id=%s",
        sender, msg_type, button_id,
    )

    # ── 1. Comando /delete ──
    if msg_type == "text" and text.strip().lower() == "/delete":
        await _handle_delete(sender, msg_id)
        return

    # ── 2. Resposta aos botões de termos ──
    if button_id in ("terms_accept", "terms_reject"):
        await _handle_terms_response(sender, msg_id, button_id, info)
        return

    # ── 3. Verificar se está registrado ──
    is_registered = await redis_client.is_user_registered(sender)
    if not is_registered:
        await _handle_new_user(sender, msg_id, info)
        return

    # ── 4. Verificar se aceitou os termos ──
    terms_status = await redis_client.get_terms_status(sender)
    if terms_status != "yes":
        await _handle_terms_not_accepted(sender, msg_id, info)
        return

    # ── 5. Marcar como lida ──
    await whatsapp_api.mark_as_read(msg_id)

    # ── 6. Documento não suportado ──
    if msg_type == "document":
        await whatsapp_api.send_text(
            sender,
            "Eu não consigo analisar documentos, você pode enviar um texto, "
            "um áudio, uma imagem ou um vídeo para eu analisar.",
            quoted_message_id=msg_id,
        )
        return

    # ── 7. Debounce + classificação ──
    await _handle_message_with_debounce(sender, info)


# ──────────────────────── Handlers específicos ────────────────────────


async def _handle_delete(sender: str, msg_id: str) -> None:
    """Processa o comando /delete."""
    await whatsapp_api.mark_as_read(msg_id)
    await redis_client.unregister_user(sender)
    await whatsapp_api.send_text(sender, DELETE_CONFIRMATION_MESSAGE)
    logger.info("Usuário %s removido via /delete", sender)


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

        # Processar mensagens pendentes usando o fluxo de debounce existente
        pending = await redis_client.get_and_clear_pending_messages(sender)
        if pending:
            logger.info(
                "Usuário %s aceitou termos — processando %d mensagem(ns) pendente(s)",
                sender, len(pending),
            )
            # Salvar histórico de chat para as mensagens pendentes
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

        logger.info("Usuário %s aceitou os termos", sender)
    else:
        await redis_client.set_terms_status(sender, False)
        await whatsapp_api.send_text(
            sender,
            "Entendido. Sem a aceitação dos Termos e Condições, "
            "não podemos processar suas solicitações.\n\n"
            "Se mudar de ideia, é só enviar uma mensagem! 😊",
        )
        logger.info("Usuário %s recusou os termos", sender)


async def _handle_new_user(sender: str, msg_id: str, info: dict) -> None:
    """Processa primeiro contato de um novo usuário.

    Registra o usuário, salva a mensagem como pendente e aplica
    debounce de 1s antes de enviar boas-vindas (para que múltiplas
    mensagens rápidas não gerem múltiplas boas-vindas).
    """
    await whatsapp_api.mark_as_read(msg_id)

    # Registrar o usuário (para que mensagens seguintes entrem no fluxo correto)
    await redis_client.register_user(sender)

    # Definir termos como "pending" para que próximas mensagens durante
    # o debounce entrem em _handle_terms_not_accepted (e não aqui de novo)
    await redis_client.set_terms_status(sender, False)

    # Salvar a mensagem para processar depois da aceitação dos termos
    await _save_pending_message(sender, info)

    # Debounce de 1s antes de enviar boas-vindas
    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        # Outra mensagem chegou durante o debounce.
        # O debounce de _handle_terms_not_accepted vai enviar o pedido de termos.
        logger.info(
            "Debounce welcome — nova mensagem para %s, delegando ao handler de termos",
            sender,
        )
        return

    await redis_client.clear_debounce_lock(sender)

    # Enviar mensagem de boas-vindas com botões
    await whatsapp_api.send_interactive_buttons(
        sender,
        WELCOME_MESSAGE,
        TERMS_BUTTONS,
    )

    logger.info("Novo usuário %s — boas-vindas enviadas", sender)


async def _handle_terms_not_accepted(
    sender: str, msg_id: str, info: dict
) -> None:
    """Processa mensagem de usuário que não aceitou os termos.

    Aplica debounce de 1s para evitar spam de botões se o usuário enviar
    múltiplas mensagens em sequência.
    """
    await whatsapp_api.mark_as_read(msg_id)

    # Salvar a mensagem para processar depois da aceitação
    await _save_pending_message(sender, info)

    # Debounce de 1s para evitar enviar múltiplos pedidos de termos
    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        logger.info(
            "Debounce termos — nova mensagem para %s, cancelando envio de pedido de termos",
            sender,
        )
        return

    await redis_client.clear_debounce_lock(sender)

    # Enviar mensagem pedindo aceitação dos termos com botões
    await whatsapp_api.send_interactive_buttons(
        sender,
        TERMS_REQUIRED_MESSAGE,
        TERMS_BUTTONS,
    )

    logger.info("Usuário %s não aceitou termos — pedindo aceitação", sender)


# ──────────────────────── Debounce e classificação ────────────────────────


async def _save_pending_message(sender: str, info: dict) -> None:
    """Salva uma mensagem na lista pendente do Redis.

    Para mídia, salva apenas as informações necessárias para recuperar depois
    (media_id, tipo), não o conteúdo binário.
    """
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
    """Processa mensagem com debounce de 1 segundo.

    Fluxo:
    1. Salvar mensagem na lista pendente
    2. Criar lock de debounce com ID único
    3. Esperar 1 segundo
    4. Se o lock ainda for o mesmo → processar todas as mensagens pendentes
    5. Se o lock mudou → outra mensagem chegou, esta task fica inativa
    """
    # Salvar mensagem na lista pendente
    await _save_pending_message(sender, info)

    # Salvar no histórico de chat (para contexto conversacional)
    if info["msg_type"] == "text" and info["text"]:
        await redis_client.add_chat_message(sender, "user", info["text"])
    elif info.get("caption"):
        await redis_client.add_chat_message(sender, "user", f"[mídia com legenda: {info['caption']}]")
    elif info["msg_type"] in ("audio", "image", "video", "sticker"):
        await redis_client.add_chat_message(sender, "user", f"[{info['msg_type']}]")

    # Criar lock de debounce
    lock_id = str(uuid.uuid4())
    await redis_client.set_debounce_lock(sender, lock_id)

    # Esperar o tempo de debounce
    await asyncio.sleep(config.MESSAGE_DEBOUNCE_SECONDS)

    # Verificar se o lock ainda é o mesmo (nenhuma nova mensagem chegou)
    current_lock = await redis_client.get_debounce_lock(sender)
    if current_lock != lock_id:
        logger.info(
            "Debounce — nova mensagem detectada para %s, cancelando processamento",
            sender,
        )
        return

    # Limpar o lock
    await redis_client.clear_debounce_lock(sender)

    # Buscar todas as mensagens pendentes e limpar atomicamente
    pending = await redis_client.get_and_clear_pending_messages(sender)
    if not pending:
        logger.warning("Nenhuma mensagem pendente após debounce para %s", sender)
        return

    await _process_with_classification(sender, pending)


async def _process_with_classification(sender: str, pending: list[dict]) -> None:
    """Classifica e processa mensagens pendentes.

    Se alguma mensagem é de mídia (imagem, vídeo, áudio, sticker),
    sempre envia para verificação sem chamar o Gemini para classificar.

    Caso contrário, usa o Gemini para classificar se é para verificar ou conversar.
    Antes de processar o resultado, verifica se novas mensagens chegaram.

    Nota: as mensagens pendentes já foram removidas do Redis antes desta chamada.
    Se novas mensagens chegarem, elas serão adicionadas à lista pendente pelo
    debounce handler da nova mensagem.
    """
    # Verificar se há mídia — se sim, sempre verificar
    has_media = any(
        msg.get("msg_type") in ("audio", "image", "video", "sticker")
        for msg in pending
    )

    if has_media:
        logger.info("Mídia detectada para %s — enviando para verificação", sender)
        try:
            await _run_verification(sender, pending)
        except Exception:
            logger.exception("Erro na verificação de mídia para %s", sender)
            await whatsapp_api.send_text(sender, ERROR_MESSAGE)
        return

    # Só mensagens de texto — classificar com Gemini
    text_messages = [
        msg.get("text", "") for msg in pending if msg.get("text")
    ]

    if not text_messages:
        logger.warning("Nenhuma mensagem de texto para classificar para %s", sender)
        return

    # Enviar indicador de digitação contínuo durante classificação
    last_msg_id = pending[-1].get("msg_id", "")
    typing_task = None
    if last_msg_id:
        typing_task = await whatsapp_api.start_typing_loop(last_msg_id)

    try:
        classification = await ai_services.classify_message(text_messages)

        # Verificar se novas mensagens chegaram durante a classificação
        new_pending_count = await redis_client.get_pending_message_count(sender)
        if new_pending_count > 0:
            if classification == "VERIFICAR":
                logger.info(
                    "Novas mensagens durante classificação para %s, "
                    "mas classificação é VERIFICAR — processando mesmo assim",
                    sender,
                )
                if typing_task:
                    typing_task.cancel()
                await _run_verification(sender, pending)
            else:
                logger.info(
                    "Novas mensagens durante classificação para %s — "
                    "abandonando resposta conversacional (novo debounce vai tratar)",
                    sender,
                )
                if typing_task:
                    typing_task.cancel()
            return

        if classification == "VERIFICAR":
            logger.info("Classificação VERIFICAR para %s", sender)
            if typing_task:
                typing_task.cancel()
            await _run_verification(sender, pending)
        else:
            logger.info("Classificação CONVERSAR para %s", sender)
            # Manter typing_task ativo durante a geração da resposta
            await _run_conversation(sender, text_messages, last_msg_id, typing_task)
            typing_task = None  # Já foi cancelado dentro de _run_conversation
    except Exception:
        logger.exception("Erro no processamento para %s", sender)
        if typing_task:
            typing_task.cancel()
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)


async def _run_verification(sender: str, pending: list[dict]) -> None:
    """Executa o fluxo de verificação via grafo LangGraph.

    Usa o raw_body da última mensagem de mídia, ou da última mensagem de texto.
    """
    # Encontrar a mensagem mais adequada para verificação
    # Prioridade: mídia > texto
    target_msg = None
    for msg in reversed(pending):
        if msg.get("msg_type") in ("audio", "image", "video", "sticker"):
            target_msg = msg
            break

    if target_msg is None:
        # Sem mídia — usar a última mensagem de texto,
        # mas combinar todos os textos em uma única mensagem
        combined_text = " ".join(
            msg.get("text", "") for msg in pending if msg.get("text")
        )
        # Usar o raw_body da última mensagem mas com texto combinado
        target_msg = pending[-1]
        # Modificar o raw_body para incluir o texto combinado
        raw_body = target_msg.get("raw_body", {})
        if raw_body:
            # Deep copy para não modificar o original
            raw_body = copy.deepcopy(raw_body)
            try:
                msg_obj = raw_body["entry"][0]["changes"][0]["value"]["messages"][0]
                if msg_obj.get("type") == "text":
                    msg_obj["text"]["body"] = combined_text
                elif msg_obj.get("type") == "interactive":
                    # Para interactive, manter o original
                    pass
            except (KeyError, IndexError):
                pass
            target_msg = {**target_msg, "raw_body": raw_body}

    raw_body = target_msg.get("raw_body")
    if not raw_body:
        logger.error("Sem raw_body para verificação do usuário %s", sender)
        return

    try:
        workflow = _get_workflow()
        initial_state = {
            "raw_body": raw_body,
            "endpoint_api": config.FACT_CHECK_API_URL,
        }
        result = await workflow.ainvoke(initial_state)

        # Salvar resposta do bot no histórico de chat
        rationale = result.get("rationale", "")
        if rationale:
            await redis_client.add_chat_message(sender, "bot", rationale)

        logger.info("Verificação concluída para %s", sender)
    except Exception:
        logger.exception("Erro na verificação para %s", sender)
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)


async def _run_conversation(
    sender: str,
    text_messages: list[str],
    last_msg_id: str,
    typing_task: asyncio.Task | None = None,
) -> None:
    """Gera e envia uma resposta conversacional via Gemini."""
    try:
        # Buscar histórico de chat dos últimos 5 minutos
        chat_history = await redis_client.get_chat_history(sender)

        # Gerar resposta
        response = await ai_services.generate_chat_response(text_messages, chat_history)

        # Verificar se novas mensagens chegaram durante a geração da resposta
        new_pending_count = await redis_client.get_pending_message_count(sender)
        if new_pending_count > 0:
            logger.info(
                "Novas mensagens chegaram durante resposta conversacional para %s — "
                "abandonando resposta",
                sender,
            )
            if typing_task:
                typing_task.cancel()
            return

        # Cancelar typing antes de enviar resposta
        if typing_task:
            typing_task.cancel()

        # Enviar resposta
        await whatsapp_api.send_text(
            sender,
            response,
            quoted_message_id=last_msg_id if last_msg_id else None,
        )

        # Salvar resposta do bot no histórico de chat
        await redis_client.add_chat_message(sender, "bot", response)

        logger.info("Resposta conversacional enviada para %s", sender)
    except Exception:
        logger.exception("Erro na resposta conversacional para %s", sender)
        if typing_task:
            typing_task.cancel()
        await whatsapp_api.send_text(sender, ERROR_MESSAGE)
