"""Nó de extração de dados do webhook (equivalente ao nó 'Pegar dados' do n8n)."""

import logging

from state import WorkflowState

logger = logging.getLogger(__name__)


def extract_data(state: WorkflowState) -> WorkflowState:
    """Extrai os dados relevantes do payload do webhook da Evolution API."""
    body = state["raw_body"]
    data = body.get("data", {})
    key = data.get("key", {})

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
        "stanza_id": data.get("contextInfo", {}).get("stanzaId", ""),
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
