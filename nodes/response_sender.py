"""Nós de envio de resposta ao usuário."""

import logging

from nodes import ai_services, whatsapp_api
from state import WorkflowState

logger = logging.getLogger(__name__)


async def send_rationale_text(state: WorkflowState) -> WorkflowState:
    """Envia o rationale como texto citando a mensagem original."""
    rationale = state.get("rationale", "")
    if not rationale:
        return {}  # type: ignore[return-value]

    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    whatsapp_api.send_typing_fire_and_forget(msg_id)

    try:
        await whatsapp_api.send_text(remote_jid, rationale, quoted_message_id=msg_id)
    except Exception:
        logger.exception("Falha ao enviar rationale para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def send_audio_response(state: WorkflowState) -> WorkflowState:
    """Gera áudio TTS do rationale e envia."""
    response_text = state.get("response_without_links", state.get("rationale", ""))
    if not response_text:
        return {}  # type: ignore[return-value]

    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    try:
        await whatsapp_api.send_text(remote_jid, "🗣️🎤 Estou gravando o áudio da resposta...")
        whatsapp_api.send_typing_fire_and_forget(msg_id)
        audio_bytes = await ai_services.generate_tts(response_text)
        await whatsapp_api.send_audio(remote_jid, audio_bytes)
    except Exception:
        logger.exception("Falha ao enviar áudio para %s", remote_jid)

    return {}  # type: ignore[return-value]


async def handle_greeting(state: WorkflowState) -> WorkflowState:
    """Responde a uma saudação com instruções de uso."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

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
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

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
    await whatsapp_api.mark_as_read(state["id_mensagem"])
    return {}  # type: ignore[return-value]
