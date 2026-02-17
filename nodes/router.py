"""Roteamento por tipo de mensagem."""

from state import WorkflowState


def route_direct_message(state: WorkflowState) -> str:
    """Switch6: Roteia mensagens diretas pelo tipo de mensagem.

    Tipos da Cloud API: audio, text, image, sticker, video, document.
    Sticker é tratado como imagem.
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

    return mapping.get(tipo, "handle_document_unsupported")
