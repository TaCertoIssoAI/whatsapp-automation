"""Roteamento por tipo de mensagem (equivalente aos nós Switch6 e Switch9 do n8n)."""

import logging

from state import WorkflowState

logger = logging.getLogger(__name__)


def route_direct_message(state: WorkflowState) -> str:
    """Switch6: Roteia mensagens diretas pelo tipo de mensagem.

    Outputs: process_audio, process_text, process_image, process_video,
             handle_document_unsupported.
    Sticker é tratado como imagem.
    """
    tipo = state.get("tipo_mensagem", "")
    logger.info("Switch6 — tipo_mensagem: %s", tipo)

    mapping = {
        "audioMessage": "process_audio",
        "conversation": "process_text",
        "extendedTextMessage": "process_text",
        "imageMessage": "process_image",
        "stickerMessage": "process_image",
        "videoMessage": "process_video",
        "documentMessage": "handle_document_unsupported",
    }

    route = mapping.get(tipo, "handle_document_unsupported")
    logger.info("Switch6 — rota selecionada: %s", route)
    return route


def detect_quoted_message_type(state: WorkflowState) -> WorkflowState:
    """Detecta o tipo da mensagem citada (quotedMessage) para o Switch9."""
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = data.get("contextInfo", {})
    quoted = context_info.get("quotedMessage", {})

    if not quoted:
        return {"quoted_message_type": "unknown"}  # type: ignore[return-value]

    # Verifica na ordem de prioridade
    if "audioMessage" in quoted:
        qtype = "audioMessage"
    elif "conversation" in quoted:
        qtype = "conversation"
    elif "imageMessage" in quoted:
        qtype = "imageMessage"
    elif "stickerMessage" in quoted:
        qtype = "stickerMessage"
    elif "videoMessage" in quoted:
        qtype = "videoMessage"
    elif "documentMessage" in quoted:
        qtype = "documentMessage"
    else:
        qtype = "unknown"

    logger.info("Quoted message type detected: %s", qtype)
    return {"quoted_message_type": qtype}  # type: ignore[return-value]


def route_quoted_message(state: WorkflowState) -> str:
    """Switch9: Roteia mensagens citadas pelo tipo."""
    qtype = state.get("quoted_message_type", "unknown")
    logger.info("Switch9 — quoted_message_type: %s", qtype)

    mapping = {
        "audioMessage": "process_quoted_audio",
        "conversation": "process_quoted_text",
        "imageMessage": "process_quoted_image",
        "stickerMessage": "process_quoted_image",
        "videoMessage": "process_quoted_video",
        "documentMessage": "handle_document_unsupported",
    }

    route = mapping.get(qtype, "handle_document_unsupported")
    logger.info("Switch9 — rota selecionada: %s", route)
    return route
