"""Definição do grafo LangGraph que replica o workflow n8n fake-news-detector.

O grafo conecta todos os nós na mesma ordem e lógica de roteamento do n8n,
cobrindo os dois caminhos principais:
1. Mensagem direta (DM) → filtros → Switch6 → processamento → resposta
2. Grupo → menção do bot → quoted message → Switch9 → processamento → resposta
"""

import logging

from langgraph.graph import END, StateGraph

from nodes.data_extractor import extract_data
from nodes.filters import (
    check_greeting,
    check_initial_message,
    check_is_mention_of_bot,
    check_is_on_group,
    check_response_to_message,
    route_greeting,
    route_initial_message,
    route_is_mention_of_bot,
    route_is_on_group,
    route_response_to_message,
)
from nodes.media_processor import (
    process_audio,
    process_image,
    process_quoted_audio,
    process_quoted_image,
    process_quoted_text,
    process_quoted_video,
    process_text,
    process_video,
)
from nodes.response_sender import (
    handle_document_unsupported,
    handle_greeting,
    mark_as_read_node,
    send_audio_response,
    send_rationale_text,
)
from nodes.router import (
    detect_quoted_message_type,
    route_direct_message,
    route_quoted_message,
)
from state import WorkflowState

logger = logging.getLogger(__name__)


def build_graph() -> StateGraph:
    """Constrói e retorna o grafo LangGraph do workflow."""
    graph = StateGraph(WorkflowState)

    # ─── Nós de extração e filtragem ───
    graph.add_node("extract_data", extract_data)
    graph.add_node("check_is_on_group", check_is_on_group)
    graph.add_node("is_mention_of_bot", check_is_mention_of_bot)
    graph.add_node("check_response_to_message", check_response_to_message)
    graph.add_node("check_initial_message", check_initial_message)
    graph.add_node("check_greeting", check_greeting)

    # ─── Nós de marcar como lida e saudação ───
    graph.add_node("mark_as_read_initial", mark_as_read_node)
    graph.add_node("mark_as_read_direct", mark_as_read_node)
    graph.add_node("mark_as_read_quoted", mark_as_read_node)
    graph.add_node("handle_greeting", handle_greeting)

    # ─── Nó de detecção de tipo de quoted message ───
    graph.add_node("detect_quoted_type", detect_quoted_message_type)

    # ─── Nós de processamento direto (Switch6) ───
    graph.add_node("process_audio", process_audio)
    graph.add_node("process_text", process_text)
    graph.add_node("process_image", process_image)
    graph.add_node("process_video", process_video)

    # ─── Nós de processamento quoted (Switch9) ───
    graph.add_node("process_quoted_audio", process_quoted_audio)
    graph.add_node("process_quoted_text", process_quoted_text)
    graph.add_node("process_quoted_image", process_quoted_image)
    graph.add_node("process_quoted_video", process_quoted_video)

    # ─── Nós de resposta ───
    graph.add_node("handle_document_unsupported", handle_document_unsupported)
    graph.add_node("send_rationale_text", send_rationale_text)
    graph.add_node("send_audio_response", send_audio_response)

    # ════════════════════════════════════
    #  ARESTAS (reproduzem o fluxo n8n)
    # ════════════════════════════════════

    # Entrada → Extrair dados → Verificar grupo
    graph.set_entry_point("extract_data")
    graph.add_edge("extract_data", "check_is_on_group")

    # isOnGroup → (grupo) isMentionOfTheBot | (direto) check_initial_message
    graph.add_conditional_edges("check_is_on_group", route_is_on_group)

    # ── Caminho GRUPO ──
    # isMentionOfTheBot → (sim) check_response_to_message | (não) END
    graph.add_conditional_edges("is_mention_of_bot", route_is_mention_of_bot)

    # isResponseToMessage → (sim/tem quoted) mark_as_read_quoted | (não) mark_as_read_direct
    graph.add_conditional_edges(
        "check_response_to_message", route_response_to_message
    )

    # Quoted path: mark_as_read_quoted → detect_quoted_type → route_quoted_message
    graph.add_edge("mark_as_read_quoted", "detect_quoted_type")
    graph.add_conditional_edges("detect_quoted_type", route_quoted_message)

    # ── Caminho DIRETO (DM) ──
    # check_initial_message → (sim) mark_as_read_initial (END) | (não) check_greeting
    graph.add_conditional_edges("check_initial_message", route_initial_message)
    graph.add_edge("mark_as_read_initial", END)

    # check_greeting → (sim) handle_greeting (END) | (não) mark_as_read_direct → Switch6
    graph.add_conditional_edges("check_greeting", route_greeting)
    graph.add_edge("handle_greeting", END)

    # mark_as_read_direct → Switch6 (roteamento por tipo de mensagem)
    graph.add_conditional_edges("mark_as_read_direct", route_direct_message)

    # ── Processamento direto (Switch6) → Resposta ──
    graph.add_edge("process_audio", "send_rationale_text")
    graph.add_edge("process_text", "send_rationale_text")
    graph.add_edge("process_image", "send_rationale_text")
    graph.add_edge("process_video", "send_rationale_text")

    # ── Processamento quoted (Switch9) → Resposta ──
    graph.add_edge("process_quoted_audio", "send_rationale_text")
    graph.add_edge("process_quoted_text", "send_rationale_text")
    graph.add_edge("process_quoted_image", "send_rationale_text")
    graph.add_edge("process_quoted_video", "send_rationale_text")

    # ── Documento não suportado → END ──
    graph.add_edge("handle_document_unsupported", END)

    # ── Enviar rationale texto → Verificar se precisa enviar áudio ──
    graph.add_conditional_edges(
        "send_rationale_text",
        _route_after_rationale,
    )

    # ── Enviar áudio → END ──
    graph.add_edge("send_audio_response", END)

    return graph


def _route_after_rationale(state: WorkflowState) -> str:
    """Após enviar o rationale, verifica se deve gerar áudio.

    No n8n, o áudio é gerado apenas quando a mensagem original era de áudio.
    """
    tipo = state.get("tipo_mensagem", "")
    quoted_type = state.get("quoted_message_type", "")

    if tipo == "audioMessage" or quoted_type == "audioMessage":
        return "send_audio_response"
    return "__end__"


def compile_graph():
    """Compila o grafo LangGraph e retorna o executor."""
    graph = build_graph()
    return graph.compile()
