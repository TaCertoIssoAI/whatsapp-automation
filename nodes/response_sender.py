"""Nós de envio de resposta ao usuário.

Equivalente aos nós 'Enviar texto' + 'Enviar audio' + sub-workflows
'digitando' e 'gravando' do n8n.
"""

import logging

from nodes import ai_services, evolution_api
from state import WorkflowState

logger = logging.getLogger(__name__)


async def send_rationale_text(state: WorkflowState) -> WorkflowState:
    """Envia o rationale como texto citando a mensagem original.

    Aplicável para: texto direto, texto citado, imagem, vídeo.
    """
    rationale = state.get("rationale", "")
    if not rationale:
        logger.info("Sem rationale para enviar, pulando.")
        return {}  # type: ignore[return-value]

    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")

    # Enviar status "digitando" (fire-and-forget)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    await evolution_api.send_text(
        instancia,
        remote_jid,
        rationale,
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    logger.info("Rationale enviado como texto para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def send_audio_response(state: WorkflowState) -> WorkflowState:
    """Gera áudio TTS do rationale e envia como áudio no WhatsApp.

    Usado apenas para mensagens de áudio (o n8n responde com áudio quando
    a mensagem original era áudio).
    """
    response_text = state.get("response_without_links", state.get("rationale", ""))
    if not response_text:
        logger.info("Sem texto para gerar áudio, pulando.")
        return {}  # type: ignore[return-value]

    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    chave_api = state.get("chave_api")

    # 1. Enviar mensagem de status
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "🗣️🎤 Estou gravando o aúdio da resposta...",
        api_key=chave_api,
    )

    # 2. Enviar status "gravando" (fire-and-forget)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "recording", chave_api
    )

    # 3. Gerar áudio via TTS
    audio_b64 = await ai_services.generate_tts(response_text)

    # 4. Enviar áudio
    await evolution_api.send_audio(
        instancia,
        remote_jid,
        audio_b64,
        api_key=chave_api,
    )

    logger.info("Áudio de resposta enviado para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def handle_greeting(state: WorkflowState) -> WorkflowState:
    """Responde a uma saudação com instruções de uso."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")

    # Marcar como lida
    await evolution_api.mark_as_read(
        instancia, remote_jid, msg_id, chave_api
    )

    # Enviar instrução
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Vc pode enviar a mensagem, imagem, vídeo, link ou áudio que quer verificar.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    logger.info("Saudação respondida para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def handle_document_unsupported(state: WorkflowState) -> WorkflowState:
    """Responde que documentos não são suportados."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")

    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Eu não consigo analisar documentos, você pode enviar um texto, "
        "um áudio, uma imagem ou um vídeo para eu analisar.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    logger.info("Documento não suportado — respondido para %s", remote_jid)
    return {}  # type: ignore[return-value]


async def mark_as_read_node(state: WorkflowState) -> WorkflowState:
    """Marca a mensagem como lida (nó genérico)."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")

    await evolution_api.mark_as_read(instancia, remote_jid, msg_id, chave_api)
    return {}  # type: ignore[return-value]
