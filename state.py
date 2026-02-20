"""Definição do estado do LangGraph para o workflow."""

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    """Estado compartilhado entre todos os nós do grafo."""

    # Dados do webhook (raw)
    raw_body: dict[str, Any]

    # Dados extraídos (nó "Pegar dados")
    numero_quem_enviou: str
    nome_quem_enviou: str
    mensagem: str
    id_mensagem: str
    stanza_id: str
    tipo_mensagem: str
    endpoint_api: str

    # Mídia (Cloud API usa media_id para download)
    media_id: str

    # Flags de roteamento
    is_group: bool
    is_greeting: bool
    is_initial_message: bool
    rate_limited: bool
    daily_count: int
    is_new_user: bool
    is_reset_command: bool

    # ── Flags de grupo (comentados — funcionalidade de grupo desativada) ──
    # is_mention_of_bot: bool
    # is_response_to_message: bool
    # quoted_message_type: str

    # Dados processados
    transcription: str
    media_base64: str
    description: str
    caption: str
    duration: float

    # Resultado do fact-checking
    rationale: str
    response_without_links: str
