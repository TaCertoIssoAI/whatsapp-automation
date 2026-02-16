"""Nós de envio de resposta ao usuário.

Adaptado para a WhatsApp Business Cloud API.
Usa whatsapp_api em vez de evolution_api.
"""

import logging

from nodes import ai_services, whatsapp_api
from state import WorkflowState

logger = logging.getLogger(__name__)


async def send_rationale_text(state: WorkflowState) -> WorkflowState:
    """Envia o rationale como texto citando a mensagem original.

    Aplicável para: texto direto, imagem, vídeo, áudio.
    """
    rationale = state.get("rationale", "")
    if not rationale:
        logger.info("Sem rationale para enviar, pulando.")
        return {}  # type: ignore[return-value]

    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    # Enviar indicador de digitação (fire-and-forget)
    whatsapp_api.send_typing_fire_and_forget(msg_id)

    await whatsapp_api.send_text(
        remote_jid,
        rationale,
        quoted_message_id=msg_id,
    )

    logger.info("Rationale enviado como texto para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def send_audio_response(state: WorkflowState) -> WorkflowState:
    """Gera áudio TTS do rationale e envia como áudio no WhatsApp.

    Usado apenas para mensagens de áudio (o n8n responde com áudio quando
    a mensagem original era áudio).
    """
    response_text = state.get("response_without_links", state.get("rationale", ""))
    if not response_text:
        logger.info("Sem texto para gerar áudio, pulando.")
        return {}  # type: ignore[return-value]

    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    # 1. Enviar mensagem de status
    await whatsapp_api.send_text(
        remote_jid,
        "🗣️🎤 Estou gravando o áudio da resposta...",
    )

    # 2. Iniciar indicador de digitação contínuo
    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        # 3. Gerar áudio via TTS (retorna bytes OGG/Opus)
        audio_bytes = await ai_services.generate_tts(response_text)

        # 4. Cancelar typing antes de enviar
        typing_task.cancel()

        # 5. Enviar áudio (upload + send via Cloud API)
        await whatsapp_api.send_audio(remote_jid, audio_bytes)
    except Exception:
        typing_task.cancel()
        raise

    logger.info("Áudio de resposta enviado para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def handle_greeting(state: WorkflowState) -> WorkflowState:
    """Responde a uma saudação com instruções de uso."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    # Marcar como lida
    await whatsapp_api.mark_as_read(msg_id)

    # Enviar instrução
    await whatsapp_api.send_text(
        remote_jid,
        "Vc pode enviar a mensagem, imagem, vídeo, link ou áudio que quer verificar.",
        quoted_message_id=msg_id,
    )

    logger.info("Saudação respondida para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def handle_document_unsupported(state: WorkflowState) -> WorkflowState:
    """Responde que documentos não são suportados."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    await whatsapp_api.send_text(
        remote_jid,
        "Eu não consigo analisar documentos, você pode enviar um texto, "
        "um áudio, uma imagem ou um vídeo para eu analisar.",
        quoted_message_id=msg_id,
    )

    logger.info("Documento não suportado — respondido para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def mark_as_read_node(state: WorkflowState) -> WorkflowState:
    """Marca a mensagem como lida (nó genérico)."""
    msg_id = state["id_mensagem"]

    await whatsapp_api.mark_as_read(msg_id)
    return {}  # type: ignore[return-value]
