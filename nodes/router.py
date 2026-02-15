"""Roteamento por tipo de mensagem (equivalente ao nó Switch6 do n8n).

Adaptado para os tipos da WhatsApp Business Cloud API.
Funcionalidades de grupo (Switch9 / quoted messages) comentadas.
"""

import logging

from state import WorkflowState

logger = logging.getLogger(__name__)


def route_direct_message(state: WorkflowState) -> str:
    """Switch6: Roteia mensagens diretas pelo tipo de mensagem.

    Tipos da Cloud API: audio, text, image, sticker, video, document.
    Sticker é tratado como imagem.
    """
    tipo = state.get("tipo_mensagem", "")
    logger.info("Switch6 — tipo_mensagem: %s", tipo)

    mapping = {
        "audio": "process_audio",
        "text": "process_text",
        "image": "process_image",
        "sticker": "process_image",
        "video": "process_video",
        "document": "handle_document_unsupported",
        # Tipos interativos tratados como texto
        "interactive": "process_text",
        "button": "process_text",
    }

    route = mapping.get(tipo, "handle_document_unsupported")
    logger.info("Switch6 — rota selecionada: %s", route)
    return route


# ══════════════════════════════════════════════════════════════════════
# FUNCIONALIDADES DE GRUPO — Comentadas (migração apenas para DM)
# ══════════════════════════════════════════════════════════════════════

# def detect_quoted_message_type(state: WorkflowState) -> WorkflowState:
#     """Detecta o tipo da mensagem citada (quotedMessage) para o Switch9."""
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#     quoted = context_info.get("quotedMessage") or {}
#
#     if not quoted:
#         return {"quoted_message_type": "unknown"}
#
#     if "audioMessage" in quoted:
#         qtype = "audioMessage"
#     elif "conversation" in quoted:
#         qtype = "conversation"
#     elif "imageMessage" in quoted:
#         qtype = "imageMessage"
#     elif "stickerMessage" in quoted:
#         qtype = "stickerMessage"
#     elif "videoMessage" in quoted:
#         qtype = "videoMessage"
#     elif "documentMessage" in quoted:
#         qtype = "documentMessage"
#     else:
#         qtype = "unknown"
#
#     logger.info("Quoted message type detected: %s", qtype)
#     return {"quoted_message_type": qtype}


# def route_quoted_message(state: WorkflowState) -> str:
#     """Switch9: Roteia mensagens citadas pelo tipo."""
#     qtype = state.get("quoted_message_type", "unknown")
#     logger.info("Switch9 — quoted_message_type: %s", qtype)
#
#     mapping = {
#         "audioMessage": "process_quoted_audio",
#         "conversation": "process_quoted_text",
#         "imageMessage": "process_quoted_image",
#         "stickerMessage": "process_quoted_image",
#         "videoMessage": "process_quoted_video",
#         "documentMessage": "handle_document_unsupported",
#     }
#
#     route = mapping.get(qtype, "handle_document_unsupported")
#     logger.info("Switch9 — rota selecionada: %s", route)
#     return route

# ══════════════════════════════════════════════════════════════════════
# FIM — Funcionalidades de grupo
# ══════════════════════════════════════════════════════════════════════
