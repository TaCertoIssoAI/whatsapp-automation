"""Nó de extração de dados do webhook (equivalente ao nó 'Pegar dados' do n8n)."""

import logging
from typing import Any

from state import WorkflowState

logger = logging.getLogger(__name__)


def get_context_info(data: dict[str, Any]) -> dict[str, Any]:
    """Extrai contextInfo do payload, buscando em múltiplas localizações.

    A Evolution API pode colocar contextInfo em:
    1. data.contextInfo (nível superior — campo de conveniência)
    2. data.message.<tipoMensagem>.contextInfo (dentro do tipo de mensagem)

    Retorna o primeiro contextInfo válido encontrado, ou {}.
    """
    # 1. Nível superior (usado pelo n8n)
    ctx = data.get("contextInfo")
    if ctx and isinstance(ctx, dict):
        return ctx

    # 2. Dentro do tipo de mensagem
    message = data.get("message") or {}
    for msg_type_key in (
        "extendedTextMessage",
        "imageMessage",
        "videoMessage",
        "audioMessage",
        "stickerMessage",
        "documentMessage",
    ):
        msg_type_data = message.get(msg_type_key)
        if isinstance(msg_type_data, dict):
            ctx = msg_type_data.get("contextInfo")
            if ctx and isinstance(ctx, dict):
                return ctx

    return {}


def extract_data(state: WorkflowState) -> WorkflowState:
    """Extrai os dados relevantes do payload do webhook da Evolution API."""
    body = state["raw_body"]
    data = body.get("data", {})
    key = data.get("key", {})
    context_info = get_context_info(data)

    extracted = {
        "endpoint_api": state.get("endpoint_api", ""),
        "instancia": body.get("instance", ""),
        "numero_quem_enviou": key.get("remoteJid", ""),
        "nome_quem_enviou": data.get("pushName", ""),
        "mensagem": (
            data.get("message", {}).get("conversation", "")
            or data.get("message", {}).get("extendedTextMessage", {}).get("text", "")
        ),
        "id_mensagem": key.get("id", ""),
        "stanza_id": context_info.get("stanzaId", ""),
        "tipo_mensagem": data.get("messageType", ""),
        "chave_api": body.get("apikey", ""),
    }

    logger.info(
        "Dados extraídos — instância=%s, de=%s, tipo=%s",
        extracted["instancia"],
        extracted["numero_quem_enviou"],
        extracted["tipo_mensagem"],
    )

    return extracted  # type: ignore[return-value]
