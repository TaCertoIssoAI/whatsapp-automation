"""Nós de envio de resposta ao usuário."""

from nodes import ai_services, whatsapp_api
from state import WorkflowState


async def send_rationale_text(state: WorkflowState) -> WorkflowState:
    """Envia o rationale como texto citando a mensagem original.

    Aplicável para: texto direto, imagem, vídeo, áudio.
    """
    rationale = state.get("rationale", "")
    if not rationale:
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

    return {}  # type: ignore[return-value]


async def send_audio_response(state: WorkflowState) -> WorkflowState:
    """Gera áudio TTS do rationale e envia como áudio no WhatsApp.

    Usado apenas para mensagens de áudio (o n8n responde com áudio quando
    a mensagem original era áudio).
    """
    response_text = state.get("response_without_links", state.get("rationale", ""))
    if not response_text:
        return {}  # type: ignore[return-value]

    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    await whatsapp_api.send_text(
        remote_jid,
        "🗣️🎤 Estou gravando o áudio da resposta...",
    )

    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        audio_bytes = await ai_services.generate_tts(response_text)
        typing_task.cancel()
        await whatsapp_api.send_audio(remote_jid, audio_bytes)
    except Exception:
        typing_task.cancel()
        raise

    return {}  # type: ignore[return-value]


async def handle_greeting(state: WorkflowState) -> WorkflowState:
    """Responde a uma saudação com instruções de uso."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]

    await whatsapp_api.mark_as_read(msg_id)
    await whatsapp_api.send_text(
        remote_jid,
        "Vc pode enviar a mensagem, imagem, vídeo, link ou áudio que quer verificar.",
        quoted_message_id=msg_id,
    )
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

    return {}  # type: ignore[return-value]


async def mark_as_read_node(state: WorkflowState) -> WorkflowState:
    """Marca a mensagem como lida (nó genérico)."""
    msg_id = state["id_mensagem"]

    await whatsapp_api.mark_as_read(msg_id)
    return {}  # type: ignore[return-value]
