"""Definição do grafo LangGraph que replica o workflow n8n fake-news-detector.

Adaptado para a WhatsApp Business Cloud API.
Apenas o caminho de mensagem direta (DM) está ativo.
O caminho de grupo está comentado (migração apenas para DM).

Fluxo DM:
  extract_data → save_message_count →
    (/reset)       handle_reset_command → END
    (novo usuário) send_welcome_message → check_rate_limit → ...
    (normal)       check_rate_limit →
      (bloqueado) END
      (ok)        check_is_on_group → check_initial_message → check_greeting →
        (saudação) handle_greeting → END
        (normal)   mark_as_read_direct → Switch6 → processamento → resposta

O rate limit roda ANTES de qualquer outro filtro para que saudações,
mensagens iniciais, etc. também sejam contadas e limitadas.
"""

import logging

from langgraph.graph import END, StateGraph

from nodes.data_extractor import extract_data
from nodes.filters import (
    check_greeting,
    check_initial_message,
    check_is_on_group,
    route_greeting,
    route_initial_message,
    route_is_on_group,
    # ── Grupo (comentado) ──
    # check_is_mention_of_bot,
    # check_response_to_message,
    # route_is_mention_of_bot,
    # route_response_to_message,
)
from nodes.media_processor import (
    process_audio,
    process_image,
    process_text,
    process_video,
    # ── Grupo (comentado) ──
    # process_quoted_audio,
    # process_quoted_image,
    # process_quoted_text,
    # process_quoted_video,
)
from nodes.rate_limiter import (
    check_rate_limit,
    route_rate_limit,
    save_message_count,
)
from nodes.response_sender import (
    handle_document_unsupported,
    handle_greeting,
    handle_reset_command,
    mark_as_read_node,
    send_audio_response,
    send_rationale_text,
    send_welcome_message,
)
from nodes.router import (
    route_direct_message,
    # ── Grupo (comentado) ──
    # detect_quoted_message_type,
    # route_quoted_message,
)
from state import WorkflowState

logger = logging.getLogger(__name__)


def build_graph() -> StateGraph:
    """Constrói e retorna o grafo LangGraph do workflow."""
    graph = StateGraph(WorkflowState)

    # ─── Nós de extração e filtragem ───
    graph.add_node("extract_data", extract_data)
    graph.add_node("save_message_count", save_message_count)
    graph.add_node("send_welcome_message", send_welcome_message)
    graph.add_node("handle_reset_command", handle_reset_command)
    graph.add_node("check_is_on_group", check_is_on_group)
    graph.add_node("check_initial_message", check_initial_message)
    graph.add_node("check_greeting", check_greeting)
    graph.add_node("check_rate_limit", check_rate_limit)

    # ── Nós de grupo (comentados) ──
    # graph.add_node("is_mention_of_bot", check_is_mention_of_bot)
    # graph.add_node("check_response_to_message", check_response_to_message)

    # ─── Nós de marcar como lida e saudação ───
    graph.add_node("mark_as_read_initial", mark_as_read_node)
    graph.add_node("mark_as_read_direct", mark_as_read_node)
    graph.add_node("handle_greeting", handle_greeting)

    # ── Nó de grupo (comentado) ──
    # graph.add_node("mark_as_read_quoted", mark_as_read_node)
    # graph.add_node("detect_quoted_type", detect_quoted_message_type)

    # ─── Nós de processamento direto (Switch6) ───
    graph.add_node("process_audio", process_audio)
    graph.add_node("process_text", process_text)
    graph.add_node("process_image", process_image)
    graph.add_node("process_video", process_video)

    # ── Nós de grupo (comentados) — processamento quoted (Switch9) ──
    # graph.add_node("process_quoted_audio", process_quoted_audio)
    # graph.add_node("process_quoted_text", process_quoted_text)
    # graph.add_node("process_quoted_image", process_quoted_image)
    # graph.add_node("process_quoted_video", process_quoted_video)

    # ─── Nós de resposta ───
    graph.add_node("handle_document_unsupported", handle_document_unsupported)
    graph.add_node("send_rationale_text", send_rationale_text)
    graph.add_node("send_audio_response", send_audio_response)

    # ════════════════════════════════════
    #  ARESTAS — Caminho DM
    # ════════════════════════════════════

    # Entrada → Extrair dados → Salvar contagem (TODA mensagem)
    graph.set_entry_point("extract_data")
    graph.add_edge("extract_data", "save_message_count")

    # save_message_count → roteamento: reset | welcome+check | check_rate_limit
    graph.add_conditional_edges("save_message_count", _route_after_save_count)

    # send_welcome_message → check_rate_limit (continua o fluxo normalmente)
    graph.add_edge("send_welcome_message", "check_rate_limit")

    # handle_reset_command → END (não processa mais nada)
    graph.add_edge("handle_reset_command", END)

    # check_rate_limit → (bloqueado) END | (ok) check_is_on_group
    graph.add_conditional_edges("check_rate_limit", route_rate_limit)

    # isOnGroup → (grupo) END | (direto) check_initial_message
    graph.add_conditional_edges("check_is_on_group", route_is_on_group)

    # ── Caminho DIRETO (DM) ──
    # check_initial_message → (sim) mark_as_read_initial (END) | (não) check_greeting
    graph.add_conditional_edges("check_initial_message", route_initial_message)
    graph.add_edge("mark_as_read_initial", END)

    # check_greeting → (sim) handle_greeting (END) | (não) mark_as_read_direct
    graph.add_conditional_edges("check_greeting", route_greeting)
    graph.add_edge("handle_greeting", END)

    # mark_as_read_direct → Switch6 (roteamento por tipo de mensagem)
    graph.add_conditional_edges("mark_as_read_direct", route_direct_message)

    # ── Processamento direto (Switch6) → Resposta ──
    graph.add_edge("process_audio", "send_rationale_text")
    graph.add_edge("process_text", "send_rationale_text")
    graph.add_edge("process_image", "send_rationale_text")
    graph.add_edge("process_video", "send_rationale_text")

    # ── Documento não suportado → END ──
    graph.add_edge("handle_document_unsupported", END)

    # ── Enviar rationale texto → Verificar se precisa enviar áudio ──
    graph.add_conditional_edges(
        "send_rationale_text",
        _route_after_rationale,
    )

    # ── Enviar áudio → END ──
    graph.add_edge("send_audio_response", END)

    # ════════════════════════════════════
    #  ARESTAS — Caminho GRUPO (comentado)
    # ════════════════════════════════════

    # # isMentionOfTheBot → (sim) check_response_to_message | (não) END
    # graph.add_conditional_edges("is_mention_of_bot", route_is_mention_of_bot)
    #
    # # isResponseToMessage → (sim) mark_as_read_quoted | (não) mark_as_read_direct
    # graph.add_conditional_edges(
    #     "check_response_to_message", route_response_to_message
    # )
    #
    # # Quoted path: mark_as_read_quoted → detect_quoted_type → route_quoted_message
    # graph.add_edge("mark_as_read_quoted", "detect_quoted_type")
    # graph.add_conditional_edges("detect_quoted_type", route_quoted_message)
    #
    # # Processamento quoted (Switch9) → Resposta
    # graph.add_edge("process_quoted_audio", "send_rationale_text")
    # graph.add_edge("process_quoted_text", "send_rationale_text")
    # graph.add_edge("process_quoted_image", "send_rationale_text")
    # graph.add_edge("process_quoted_video", "send_rationale_text")

    return graph


def _route_after_save_count(state: WorkflowState) -> str:
    """Rota após salvar contagem:
    - /reset → handle_reset_command → END
    - Usuário novo → send_welcome_message → check_rate_limit
    - Normal → check_rate_limit
    """
    if state.get("is_reset_command"):
        logger.info("[route-save-count] /reset detectado → handle_reset_command")
        return "handle_reset_command"
    if state.get("is_new_user"):
        logger.info("[route-save-count] Novo usuário → send_welcome_message")
        return "send_welcome_message"
    return "check_rate_limit"


def _route_after_rationale(state: WorkflowState) -> str:
    """Após enviar o rationale, verifica se deve gerar áudio.

    O áudio é gerado apenas quando:
    1. A mensagem original era de áudio
    2. Existe rationale para converter em áudio
    """
    tipo = state.get("tipo_mensagem", "")
    rationale = state.get("rationale", "")

    if tipo == "audio" and rationale:
        return "send_audio_response"
    return "__end__"


def compile_graph():
    """Compila o grafo LangGraph e retorna o executor.

    Se falhar, loga o erro e re-lança para que o startup saiba.
    """
    logger.info("Compilando grafo LangGraph...")
    graph = build_graph()
    compiled = graph.compile()
    logger.info("Grafo LangGraph compilado com sucesso")
    return compiled
