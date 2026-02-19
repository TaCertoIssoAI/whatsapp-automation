"""Nós de envio de resposta ao usuário."""

import logging

from nodes import ai_services, whatsapp_api
from state import WorkflowState

logger = logging.getLogger(__name__)


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
        try:
            await whatsapp_api.send_text(
                remote_jid,
                "⚠️ Não consegui analisar o conteúdo enviado. "
                "Por favor, tente enviar novamente.",
                quoted_message_id=msg_id or None,
            )
        except Exception:
            logger.exception("Falha ao enviar mensagem de fallback para %s", remote_jid)
        return {}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(remote_jid, rationale, quoted_message_id=msg_id)
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
    response_text = state.get("response_without_links", state.get("rationale", ""))
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

    if not remote_jid:
        return {}  # type: ignore[return-value]

    await whatsapp_api.mark_as_read(msg_id)
    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Vc pode enviar a mensagem, imagem, vídeo, link ou áudio que quer verificar.",
            quoted_message_id=msg_id,
        )
    except Exception:
        logger.exception("Falha ao responder saudação para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def handle_document_unsupported(state: WorkflowState) -> WorkflowState:
    """Responde que documentos não são suportados."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")

    if not remote_jid:
        return {}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Eu não consigo analisar documentos, você pode enviar um texto, "
            "um áudio, uma imagem ou um vídeo para eu analisar.",
            quoted_message_id=msg_id,
        )
    except Exception:
        logger.exception("Falha ao enviar msg de doc não suportado para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def mark_as_read_node(state: WorkflowState) -> WorkflowState:
    """Marca a mensagem como lida."""
    msg_id = state.get("id_mensagem", "")
    if msg_id:
        await whatsapp_api.mark_as_read(msg_id)
    return {}  # type: ignore[return-value]
