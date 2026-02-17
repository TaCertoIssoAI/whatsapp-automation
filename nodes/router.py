"""Roteamento por tipo de mensagem."""

import logging

from state import WorkflowState

logger = logging.getLogger(__name__)


def route_direct_message(state: WorkflowState) -> str:
    """Switch6: Roteia mensagens diretas pelo tipo de mensagem.

    Tipos da Cloud API: audio, text, image, sticker, video, document,
    interactive, button, reaction, location, contacts, order, system.
    """
    tipo = state.get("tipo_mensagem", "")

    mapping = {
        "audio": "process_audio",
        "text": "process_text",
        "image": "process_image",
        "sticker": "process_image",
        "video": "process_video",
        "document": "handle_document_unsupported",
        "interactive": "process_text",
        "button": "process_text",
    }

    route = mapping.get(tipo)
    if not route:
        logger.info("Tipo de mensagem não suportado: '%s'", tipo)
        route = "handle_document_unsupported"

    return route
