"""Nós de envio de resposta ao usuário."""

import logging

from nodes import ai_services, whatsapp_api
from nodes.rate_limiter import _RESET_CONFIRMATION_MESSAGE, _WELCOME_MESSAGE
from state import WorkflowState

logger = logging.getLogger(__name__)


async def send_welcome_message(state: WorkflowState) -> WorkflowState:
    """Envia mensagem de boas-vindas para usuários novos (primeiro uso ou totalMessageCount == 0)."""
    remote_jid = state.get("numero_quem_enviou", "")
    is_new_user = state.get("is_new_user", False)

    if not remote_jid or not is_new_user:
        return {}  # type: ignore[return-value]

    logger.info("[welcome] Enviando mensagem de boas-vindas para %s…%s",
                remote_jid[:4], remote_jid[-4:])
    try:
        await whatsapp_api.send_text(remote_jid, _WELCOME_MESSAGE)
        logger.info("[welcome] ✅ Mensagem de boas-vindas enviada")
        # Salvar resposta do bot no histórico de chat
        from nodes.message_handler import save_bot_response_to_history
        await save_bot_response_to_history(remote_jid, _WELCOME_MESSAGE)
    except Exception:
        logger.exception("[welcome] Falha ao enviar mensagem de boas-vindas para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def handle_reset_command(state: WorkflowState) -> WorkflowState:
    """Responde ao comando /reset confirmando que os contadores foram zerados."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")

    if not remote_jid:
        return {}  # type: ignore[return-value]

    logger.info("[reset] Enviando confirmação de reset para %s…%s",
                remote_jid[:4], remote_jid[-4:])
    try:
        await whatsapp_api.mark_as_read(msg_id)
        await whatsapp_api.send_text(
            remote_jid,
            _RESET_CONFIRMATION_MESSAGE,
            quoted_message_id=msg_id,
        )
        logger.info("[reset] \u2705 Confirma\u00e7\u00e3o de reset enviada")
        # Salvar resposta do bot no hist\u00f3rico de chat
        from nodes.message_handler import save_bot_response_to_history
        await save_bot_response_to_history(remote_jid, _RESET_CONFIRMATION_MESSAGE)
    except Exception:
        logger.exception("[reset] Falha ao enviar confirma\u00e7\u00e3o de reset para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def send_rationale_text(state: WorkflowState) -> WorkflowState:
    """Envia o rationale como texto citando a mensagem original."""
    rationale = state.get("rationale", "")
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")

    if not remote_jid:
        logger.warning("Sem número de destinatário para enviar rationale")
        return {}  # type: ignore[return-value]

    if not rationale:
        # Rationale vazio = algo falhou no processamento, notificar usuário
        fallback_msg = (
            "⚠️ Não consegui analisar o conteúdo enviado. "
            "Por favor, tente enviar novamente."
        )
        try:
            await whatsapp_api.send_text(
                remote_jid,
                fallback_msg,
                quoted_message_id=msg_id or None,
            )
            # Salvar resposta do bot no histórico de chat
            from nodes.message_handler import save_bot_response_to_history
            await save_bot_response_to_history(remote_jid, fallback_msg)
        except Exception:
            logger.exception("Falha ao enviar mensagem de fallback para %s", remote_jid)
        return {}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(remote_jid, rationale, quoted_message_id=msg_id)
        # Salvar resposta do bot no histórico de chat
        from nodes.message_handler import save_bot_response_to_history
        await save_bot_response_to_history(remote_jid, rationale)
    except Exception:
        logger.exception("Falha ao enviar rationale para %s", remote_jid)
        try:
            await whatsapp_api.send_text(
                remote_jid,
                "⚠️ Desculpe, não consegui enviar a resposta completa. "
                "Por favor, tente enviar sua mensagem novamente.",
            )
        except Exception:
            pass

    return {}  # type: ignore[return-value]


async def send_audio_response(state: WorkflowState) -> WorkflowState:
    """Gera áudio TTS do rationale e envia."""
    response_text = state.get("audio_script", state.get("rationale", ""))
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")

    if not response_text or not remote_jid:
        return {}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(remote_jid, "🗣️🎤 Estou gravando o áudio da resposta...")
        # Reativar typing indicator para a gravação do áudio
        if msg_id:
            await whatsapp_api.send_typing_indicator(msg_id)
        audio_bytes = await ai_services.generate_tts(response_text)
        await whatsapp_api.send_audio(remote_jid, audio_bytes)
    except Exception:
        logger.exception("Falha ao enviar áudio para %s", remote_jid)
        try:
            await whatsapp_api.send_text(
                remote_jid,
                "⚠️ Não consegui gerar o áudio da resposta, mas a resposta em texto já foi enviada acima.",
            )
        except Exception:
            pass

    return {}  # type: ignore[return-value]


async def handle_greeting(state: WorkflowState) -> WorkflowState:
    """Responde a uma saudação com instruções de uso."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    greeting_response = "Vc pode enviar a mensagem, imagem, vídeo, link ou áudio que quer verificar."

    if not remote_jid:
        return {}  # type: ignore[return-value]

    await whatsapp_api.mark_as_read(msg_id)
    try:
        await whatsapp_api.send_text(
            remote_jid,
            greeting_response,
            quoted_message_id=msg_id,
        )
        # Salvar resposta do bot no histórico de chat
        from nodes.message_handler import save_bot_response_to_history
        await save_bot_response_to_history(remote_jid, greeting_response)
    except Exception:
        logger.exception("Falha ao responder saudação para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def handle_document_unsupported(state: WorkflowState) -> WorkflowState:
    """Responde que documentos não são suportados."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    unsupported_msg = (
        "Eu não consigo analisar documentos, você pode enviar um texto, "
        "um áudio, uma imagem ou um vídeo para eu analisar."
    )

    if not remote_jid:
        return {}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            unsupported_msg,
            quoted_message_id=msg_id,
        )
        # Salvar resposta do bot no histórico de chat
        from nodes.message_handler import save_bot_response_to_history
        await save_bot_response_to_history(remote_jid, unsupported_msg)
    except Exception:
        logger.exception("Falha ao enviar msg de doc não suportado para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def mark_as_read_node(state: WorkflowState) -> WorkflowState:
    """Marca a mensagem como lida."""
    msg_id = state.get("id_mensagem", "")
    if msg_id:
        await whatsapp_api.mark_as_read(msg_id)
    return {}  # type: ignore[return-value]
