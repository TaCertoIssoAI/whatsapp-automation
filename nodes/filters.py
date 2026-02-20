"""Nós de filtragem e verificação de condições do workflow."""

import logging
import unicodedata

from state import WorkflowState

logger = logging.getLogger(__name__)

# Lista de saudações (mesma do n8n Code in JavaScript1)
GREETINGS = [
    "oi",
    "ola",
    "eai",
    "iae",
    "iai",
    "fala",
    "fala ai",
    "fala ae",
    "bom dia",
    "boa tarde",
    "boa noite",
    "opa",
    "salve",
    "alo",
    "oii",
    "oiii",
    "ola tudo bem",
    "oi tudo bem",
    "bom dia tudo bem",
    "boa tarde tudo bem",
    "boa noite tudo bem",
]


def _normalize_text(text: str) -> str:
    """Normaliza texto removendo acentos e convertendo para minúsculas."""
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn").strip()


# ──────────────────────── Routing functions ────────────────────────


def check_is_on_group(state: WorkflowState) -> WorkflowState:
    """Verifica se a mensagem veio de um grupo.

    Na Cloud API, apenas mensagens DM são recebidas no webhook por padrão.
    Também interrompe o fluxo se não houver remetente (payload sem mensagem válida).
    """
    # Se não tem remetente, marca como grupo para interromper fluxo no route
    sender = state.get("numero_quem_enviou", "")
    if not sender:
        return {"is_group": True}  # type: ignore[return-value]

    is_group = False
    return {"is_group": is_group}  # type: ignore[return-value]


def route_is_on_group(state: WorkflowState) -> str:
    """Decide a rota baseado em se é grupo ou não.

    Como grupos estão desativados, sempre vai para o caminho direto (DM).
    """
    if state.get("is_group"):
        # Caminho de grupo desativado — vai para END
        return "__end__"
    return "check_initial_message"


def check_initial_message(state: WorkflowState) -> WorkflowState:
    """Verifica se é a mensagem inicial do bot (contém link de termos)."""
    mensagem = state.get("mensagem", "")
    is_initial = "tacertoissoai.com.br/termos-e-privacidade" in mensagem
    return {"is_initial_message": is_initial}  # type: ignore[return-value]


def route_initial_message(state: WorkflowState) -> str:
    """Decide a rota: se é mensagem inicial → marcar como lida, senão → verificar saudação."""
    if state.get("is_initial_message"):
        return "mark_as_read_initial"
    return "check_greeting"


def check_greeting(state: WorkflowState) -> WorkflowState:
    """Verifica se a mensagem é uma saudação."""
    mensagem = state.get("mensagem", "")
    normalized = _normalize_text(mensagem)
    is_greeting = normalized in GREETINGS
    return {"is_greeting": is_greeting}  # type: ignore[return-value]


def route_greeting(state: WorkflowState) -> str:
    """Decide a rota: se é saudação → responder instruções, senão → processar mensagem."""
    if state.get("is_greeting"):
        return "handle_greeting"
    return "mark_as_read_direct"
