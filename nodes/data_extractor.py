"""Extração de dados do webhook da WhatsApp Business Cloud API."""

import logging
from typing import Any

from state import WorkflowState

logger = logging.getLogger(__name__)


def _get_message_data(body: dict[str, Any]) -> dict[str, Any]:
    """Extrai o objeto de mensagem do payload da Cloud API.

    Estrutura: body.entry[0].changes[0].value
    """
    entries = body.get("entry", [])
    if not entries:
        return {}
    changes = entries[0].get("changes", [])
    if not changes:
        return {}
    return changes[0].get("value", {})


def _get_message(value: dict[str, Any]) -> dict[str, Any]:
    """Extrai o primeiro objeto de mensagem."""
    messages = value.get("messages", [])
    if not messages:
        return {}
    return messages[0]


def _get_contact_name(value: dict[str, Any]) -> str:
    """Extrai o nome do contato do payload."""
    contacts = value.get("contacts", [])
    if not contacts:
        return ""
    profile = contacts[0].get("profile", {})
    return profile.get("name", "")


def _extract_text(message: dict[str, Any]) -> str:
    """Extrai o texto da mensagem, seja text, interactive ou button."""
    msg_type = message.get("type", "")

    if msg_type == "text":
        return message.get("text", {}).get("body", "")
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        # Button reply ou list reply
        button = interactive.get("button_reply", {})
        if button:
            return button.get("title", "")
        list_reply = interactive.get("list_reply", {})
        if list_reply:
            return list_reply.get("title", "")
    if msg_type == "button":
        return message.get("button", {}).get("text", "")

    return ""


def _extract_media_id(message: dict[str, Any]) -> str:
    """Extrai o media_id da mensagem de mídia (audio, image, video, sticker, document)."""
    msg_type = message.get("type", "")
    media_obj = message.get(msg_type, {})
    return media_obj.get("id", "")


def _extract_caption(message: dict[str, Any]) -> str:
    """Extrai a legenda de mensagens de imagem/vídeo."""
    msg_type = message.get("type", "")
    media_obj = message.get(msg_type, {})
    return media_obj.get("caption", "")


def extract_data(state: WorkflowState) -> WorkflowState:
    """Extrai os dados relevantes do payload do webhook da WhatsApp Cloud API.

    Formato da Cloud API:
    {
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{
              "from": "5511999999999",
              "id": "wamid.xxx",
              "type": "text|audio|image|video|sticker|document",
              "text": {"body": "..."},
              ...
            }],
            "contacts": [{"profile": {"name": "..."}}]
          }
        }]
      }]
    }
    """
    body = state["raw_body"]
    value = _get_message_data(body)
    message = _get_message(value)

    if not message:
        logger.warning("Nenhuma mensagem encontrada no payload")
        return {}  # type: ignore[return-value]

    # Contexto de citação (reply)
    context = message.get("context", {})
    stanza_id = context.get("id", "")

    # Tipo de mensagem da Cloud API (text, audio, image, video, sticker, document)
    tipo_mensagem = message.get("type", "")

    # Extrair texto (para text, interactive, button)
    mensagem = _extract_text(message)

    # Extrair media_id (para audio, image, video, sticker, document)
    media_id = _extract_media_id(message)

    # Extrair caption (para image, video)
    caption = _extract_caption(message)

    extracted = {
        "endpoint_api": state.get("endpoint_api", ""),
        "numero_quem_enviou": message.get("from", ""),
        "nome_quem_enviou": _get_contact_name(value),
        "mensagem": mensagem,
        "id_mensagem": message.get("id", ""),
        "stanza_id": stanza_id,
        "tipo_mensagem": tipo_mensagem,
        "media_id": media_id,
        "caption": caption,
    }

    return extracted  # type: ignore[return-value]
