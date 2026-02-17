"""Definição do grafo LangGraph do workflow de fact-checking."""

import logging

from langgraph.graph import END, StateGraph

from nodes.data_extractor import extract_data
from nodes.media_processor import (
    process_audio,
    process_image,
    process_text,
    process_video,
)
from nodes.response_sender import (
    handle_document_unsupported,
    mark_as_read_node,
    send_audio_response,
    send_rationale_text,
)
from nodes.router import (
    route_direct_message,
)
from state import WorkflowState

logger = logging.getLogger(__name__)


def build_graph() -> StateGraph:
    """Constrói e retorna o grafo LangGraph do workflow."""
    graph = StateGraph(WorkflowState)

    # ─── Nós de extração ───
    graph.add_node("extract_data", extract_data)

    # ─── Nó de marcar como lida ───
    graph.add_node("mark_as_read_direct", mark_as_read_node)

    # ─── Nós de processamento direto (Switch6) ───
    graph.add_node("process_audio", process_audio)
    graph.add_node("process_text", process_text)
    graph.add_node("process_image", process_image)
    graph.add_node("process_video", process_video)

    # ─── Nós de resposta ───
    graph.add_node("handle_document_unsupported", handle_document_unsupported)
    graph.add_node("send_rationale_text", send_rationale_text)
    graph.add_node("send_audio_response", send_audio_response)

    # ════════════════════════════════════
    #  ARESTAS — Caminho DM
    # ════════════════════════════════════

    # Entrada → Extrair dados → Marcar como lida → Switch6
    graph.set_entry_point("extract_data")
    graph.add_edge("extract_data", "mark_as_read_direct")

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

    return graph


def _route_after_rationale(state: WorkflowState) -> str:
    """Após enviar o rationale, verifica se deve gerar áudio.

    O áudio é gerado apenas quando a mensagem original era de áudio.
    Tipos da Cloud API: 'audio' (em vez de 'audioMessage' da Evolution API).
    """
    tipo = state.get("tipo_mensagem", "")

    if tipo == "audio":
        return "send_audio_response"
    return "__end__"


def compile_graph():
    """Compila o grafo LangGraph e retorna o executor."""
    graph = build_graph()
    return graph.compile()
