"""Nós de filtragem e verificação de condições do workflow.

Adaptado para a WhatsApp Business Cloud API.
Funcionalidades de grupo comentadas (migração apenas para DM).
"""

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
    Grupos não são suportados diretamente. Mantemos a verificação por
    compatibilidade, mas sempre retornará False no fluxo DM.
    """
    # Na Cloud API, o número de quem enviou é simplesmente o telefone (ex: '5511999999999')
    # Não há sufixo @g.us como na Evolution API
    is_group = False
    logger.info("isOnGroup: %s", is_group)
    return {"is_group": is_group}  # type: ignore[return-value]


def route_is_on_group(state: WorkflowState) -> str:
    """Decide a rota baseado em se é grupo ou não.

    Como grupos estão desativados, sempre vai para o caminho direto (DM).
    """
    if state.get("is_group"):
        # Caminho de grupo desativado — vai para END
        return "__end__"
    return "check_initial_message"


# ══════════════════════════════════════════════════════════════════════
# FUNCIONALIDADES DE GRUPO — Comentadas (migração apenas para DM)
# ══════════════════════════════════════════════════════════════════════

# def check_is_mention_of_bot(state: WorkflowState) -> WorkflowState:
#     """Verifica se o bot foi mencionado na mensagem do grupo."""
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#     mentioned_jids = context_info.get("mentionedJid", [])
#
#     if not isinstance(mentioned_jids, list):
#         mentioned_jids = []
#
#     if mentioned_jids:
#         logger.info(
#             "mentionedJid encontrados: %s (BOT_MENTION_JID configurado: %s)",
#             mentioned_jids,
#             config.BOT_MENTION_JID,
#         )
#
#     bot_jid = config.BOT_MENTION_JID
#     bot_number = bot_jid.split("@")[0] if bot_jid else ""
#
#     is_mention = False
#     if mentioned_jids:
#         for jid in mentioned_jids:
#             jid_number = jid.split("@")[0] if isinstance(jid, str) else ""
#             if jid == bot_jid or (bot_number and jid_number == bot_number):
#                 is_mention = True
#                 break
#
#     logger.info("isMentionOfTheBot: %s", is_mention)
#     return {"is_mention_of_bot": is_mention}


# def route_is_mention_of_bot(state: WorkflowState) -> str:
#     """Decide a rota baseado em se o bot foi mencionado."""
#     if state.get("is_mention_of_bot"):
#         return "check_response_to_message"
#     return "__end__"


# def check_response_to_message(state: WorkflowState) -> WorkflowState:
#     """Verifica se a mensagem é uma resposta (tem quotedMessage)."""
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#     quoted_message = context_info.get("quotedMessage")
#
#     has_quoted = quoted_message is not None and isinstance(quoted_message, dict)
#     logger.info("isResponseToMessage: %s", has_quoted)
#     return {"is_response_to_message": has_quoted}


# def route_response_to_message(state: WorkflowState) -> str:
#     """Decide a rota: se tem quoted → Switch9, senão → Switch6."""
#     if state.get("is_response_to_message"):
#         return "mark_as_read_quoted"
#     return "mark_as_read_direct"

# ══════════════════════════════════════════════════════════════════════
# FIM — Funcionalidades de grupo
# ══════════════════════════════════════════════════════════════════════


def check_initial_message(state: WorkflowState) -> WorkflowState:
    """Verifica se é a mensagem inicial do bot (contém link de termos)."""
    mensagem = state.get("mensagem", "")
    is_initial = "tacertoissoai.com.br/termos-e-privacidade" in mensagem
    logger.info("isInitialMessage: %s", is_initial)
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
    logger.info("isGreeting: %s (normalized='%s')", is_greeting, normalized)
    return {"is_greeting": is_greeting}  # type: ignore[return-value]


def route_greeting(state: WorkflowState) -> str:
    """Decide a rota: se é saudação → responder instruções, senão → Switch6 (processar)."""
    if state.get("is_greeting"):
        return "handle_greeting"
    return "mark_as_read_direct"
