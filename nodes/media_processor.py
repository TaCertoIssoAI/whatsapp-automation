"""Processamento de mídia: áudio, imagem e vídeo.

Cada função de processamento é um nó do LangGraph que:
1. Envia mensagem de status ("Estou analisando...")
2. Obtém a mídia em base64
3. Processa a mídia (transcrição/análise)
4. Chama a API de fact-checking
5. Retorna o rationale

Cobre ambos os caminhos do n8n:
- Mensagens diretas (Switch6)
- Mensagens citadas em grupos (Switch9)
"""

import base64
import logging
import struct

from nodes import ai_services, evolution_api, fact_checker
from nodes.data_extractor import get_context_info
from state import WorkflowState

logger = logging.getLogger(__name__)


# ──────────────────────── Utilitários ────────────────────────


def get_video_duration_from_base64(video_base64: str) -> float:
    """Extrai a duração de um MP4 do base64 (mesmo código JS do n8n)."""
    buffer = base64.b64decode(video_base64)
    offset = 0

    while offset < len(buffer):
        if offset + 8 > len(buffer):
            break

        size = struct.unpack(">I", buffer[offset : offset + 4])[0]
        box_type = buffer[offset + 4 : offset + 8].decode("ascii", errors="replace")

        if box_type == "moov":
            moov_offset = offset + 8
            moov_end = offset + size

            while moov_offset < moov_end:
                if moov_offset + 8 > len(buffer):
                    break

                box_size = struct.unpack(
                    ">I", buffer[moov_offset : moov_offset + 4]
                )[0]
                inner_type = buffer[moov_offset + 4 : moov_offset + 8].decode(
                    "ascii", errors="replace"
                )

                if inner_type == "mvhd":
                    version = buffer[moov_offset + 8]

                    if version == 0:
                        timescale = struct.unpack(
                            ">I", buffer[moov_offset + 20 : moov_offset + 24]
                        )[0]
                        duration = struct.unpack(
                            ">I", buffer[moov_offset + 24 : moov_offset + 28]
                        )[0]
                    else:
                        timescale = struct.unpack(
                            ">I", buffer[moov_offset + 28 : moov_offset + 32]
                        )[0]
                        duration = struct.unpack(
                            ">Q", buffer[moov_offset + 32 : moov_offset + 40]
                        )[0]

                    return duration / timescale

                moov_offset += box_size

        if size == 0:
            break
        offset += size

    raise ValueError("mvhd not found in MP4")


# ──────────────────────── Áudio (direto) ────────────────────────


async def process_audio(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de áudio direta.

    Flow: Enviar status → Obter mídia → Transcrever → Fact-check.
    """
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")

    # 1. Enviar mensagem de status
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # 1b. Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    # 2. Obter mídia em base64
    media = await evolution_api.get_media_base64(instancia, msg_id, chave_api)
    audio_b64 = media.get("data", {}).get("base64", media.get("base64", ""))

    # 3. Transcrever áudio
    transcription = await ai_services.transcribe_audio(audio_b64)

    # 4. Fact-check
    result = await fact_checker.check_text(
        state["endpoint_api"],
        transcription.replace("\n", " "),
        content_type="audio",
    )

    return {
        "transcription": transcription,
        "media_base64": audio_b64,
        "rationale": result.get("rationale", ""),
        "response_without_links": result.get("responseWithoutLinks", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Texto (direto) ────────────────────────


async def process_text(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de texto direta.

    Flow: Enviar status → Fact-check direto.
    """
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    mensagem = state.get("mensagem", "")
    chave_api = state.get("chave_api")

    # 1. Enviar mensagem de status
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando a mensagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # 1b. Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    # 2. Fact-check
    result = await fact_checker.check_text(
        state["endpoint_api"],
        mensagem.replace("\n", " "),
        content_type="text",
    )

    return {
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Imagem (direto) ────────────────────────


async def process_image(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de imagem direta.

    Flow: Enviar status → Obter mídia → Analisar imagem (sub-workflow) +
          Reverse search (sub-workflow) → Merge → Verificar legenda → Fact-check.
    """
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})

    # 1. Enviar mensagem de status
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # 1b. Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    # 2. Obter mídia em base64
    media = await evolution_api.get_media_base64(instancia, msg_id, chave_api)
    image_b64 = media.get("data", {}).get("base64", media.get("base64", ""))

    # 3. Analisar imagem (analyze-image sub-workflow)
    image_analysis = await ai_services.analyze_image_content(image_b64)

    # 4. Reverse search (reverse-search sub-workflow)
    reverse_result = await ai_services.reverse_image_search(image_b64)

    # 5. Montar descrição (merge dos resultados)
    description = (
        f"{image_analysis}\n\n"
        f"Informações de pesquisa reversa da imagem em sites da web: \n"
        f"{reverse_result}"
    )

    # 6. Verificar legenda
    key = data.get("key", {})
    is_direct = key.get("remoteJid", "").endswith("@s.whatsapp.net")
    if is_direct:
        caption = data.get("message", {}).get("imageMessage", {}).get("caption", "")
    else:
        context_info = get_context_info(data)
        caption = (
            context_info.get("quotedMessage", {})
            .get("imageMessage", {})
            .get("caption", "")
        )

    # 7. Fact-check
    content_parts = [{"textContent": description, "type": "image"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    result = await fact_checker.check_content(state["endpoint_api"], content_parts)

    return {
        "description": description,
        "caption": caption,
        "media_base64": image_b64,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Vídeo (direto) ────────────────────────


async def process_video(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de vídeo direta.

    Flow: Enviar status → Obter mídia → Verificar duração → Analisar vídeo →
          Verificar legenda → Fact-check.
    """
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})

    # 1. Enviar mensagem de status
    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # 1b. Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    # 2. Obter mídia em base64
    media = await evolution_api.get_media_base64(instancia, msg_id, chave_api)
    video_b64 = media.get("data", {}).get("base64", media.get("base64", ""))

    # 3. Verificar duração (máx 2 minutos = 120 segundos)
    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        await evolution_api.send_text(
            instancia,
            remote_jid,
            "Para que eu possa analizar o conteúdo do vídeo, "
            "ele precisa ter uma duração máxima de 2 minutos.",
            quoted_message_id=msg_id,
            api_key=chave_api,
        )
        return {"rationale": "", "duration": duration}  # type: ignore[return-value]

    # 4. Analisar vídeo com Gemini
    description = await ai_services.analyze_video(video_b64)

    # 5. Verificar legenda
    key = data.get("key", {})
    is_direct = key.get("remoteJid", "").endswith("@s.whatsapp.net")
    if is_direct:
        caption = data.get("message", {}).get("videoMessage", {}).get("caption", "")
    else:
        context_info = get_context_info(data)
        caption = (
            context_info.get("quotedMessage", {})
            .get("videoMessage", {})
            .get("caption", "")
        )

    # 6. Fact-check
    content_parts = [{"textContent": description, "type": "video"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    result = await fact_checker.check_content(state["endpoint_api"], content_parts)

    return {
        "description": description,
        "caption": caption,
        "media_base64": video_b64,
        "duration": duration,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Áudio citado (grupo) ────────────────────────


async def process_quoted_audio(state: WorkflowState) -> WorkflowState:
    """Processa áudio citado em grupo (Switch9 → audioMessage)."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")

    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    audio_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    transcription = await ai_services.transcribe_audio(audio_b64)

    result = await fact_checker.check_text(
        state["endpoint_api"],
        transcription.replace("\n", " "),
        content_type="audio",
    )

    return {
        "transcription": transcription,
        "media_base64": audio_b64,
        "rationale": result.get("rationale", ""),
        "response_without_links": result.get("responseWithoutLinks", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Texto citado (grupo) ────────────────────────


async def process_quoted_text(state: WorkflowState) -> WorkflowState:
    """Processa texto citado em grupo (Switch9 → conversation)."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)
    quoted_text = context_info.get("quotedMessage", {}).get("conversation", "")

    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando a mensagem para verificar se é uma fake news ou não.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    result = await fact_checker.check_text(
        state["endpoint_api"],
        quoted_text.replace("\n", " "),
        content_type="text",
    )

    return {
        "mensagem": quoted_text,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Imagem citada (grupo) ────────────────────────


async def process_quoted_image(state: WorkflowState) -> WorkflowState:
    """Processa imagem citada em grupo (Switch9 → imageMessage/stickerMessage)."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)

    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    image_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    image_analysis = await ai_services.analyze_image_content(image_b64)
    reverse_result = await ai_services.reverse_image_search(image_b64)

    description = (
        f"{image_analysis}\n\n"
        f"Informações de pesquisa reversa da imagem em sites da web: \n"
        f"{reverse_result}"
    )

    caption = (
        context_info.get("quotedMessage", {})
        .get("imageMessage", {})
        .get("caption", "")
    )

    content_parts = [{"textContent": description, "type": "image"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    result = await fact_checker.check_content(state["endpoint_api"], content_parts)

    return {
        "description": description,
        "caption": caption,
        "media_base64": image_b64,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


# ──────────────────────── Vídeo citado (grupo) ────────────────────────


async def process_quoted_video(state: WorkflowState) -> WorkflowState:
    """Processa vídeo citado em grupo (Switch9 → videoMessage)."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)

    await evolution_api.send_text(
        instancia,
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
        api_key=chave_api,
    )

    # Enviar presença "digitando" (fire-and-forget, como n8n)
    evolution_api.send_presence_fire_and_forget(
        instancia, remote_jid, "composing", chave_api
    )

    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    video_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    # Verificar duração
    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        await evolution_api.send_text(
            instancia,
            remote_jid,
            "Para que eu possa analizar o conteúdo do vídeo, "
            "ele precisa ter uma duração máxima de 2 minutos.",
            quoted_message_id=msg_id,
            api_key=chave_api,
        )
        return {"rationale": "", "duration": duration}  # type: ignore[return-value]

    description = await ai_services.analyze_video(video_b64)

    caption = (
        context_info.get("quotedMessage", {})
        .get("videoMessage", {})
        .get("caption", "")
    )

    content_parts = [{"textContent": description, "type": "video"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    result = await fact_checker.check_content(state["endpoint_api"], content_parts)

    return {
        "description": description,
        "caption": caption,
        "media_base64": video_b64,
        "duration": duration,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]
