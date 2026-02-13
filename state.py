"""Definição do estado do LangGraph para o workflow."""

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    """Estado compartilhado entre todos os nós do grafo."""

    # Dados do webhook (raw)
    raw_body: dict[str, Any]

    # Dados extraídos (nó "Pegar dados")
    instancia: str
    numero_quem_enviou: str
    nome_quem_enviou: str
    mensagem: str
    id_mensagem: str
    stanza_id: str
    tipo_mensagem: str
    chave_api: str
    endpoint_api: str

    # Flags de roteamento
    is_group: bool
    is_mention_of_bot: bool
    is_response_to_message: bool
    is_greeting: bool
    is_initial_message: bool

    # Tipo da mensagem citada (para grupo com menção)
    quoted_message_type: str

    # Dados processados
    transcription: str
    media_base64: str
    description: str
    caption: str
    duration: float

    # Resultado do fact-checking
    rationale: str
    response_without_links: str
