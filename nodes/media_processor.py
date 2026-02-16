"""Processamento de mídia: áudio, imagem e vídeo.

Cada função de processamento é um nó do LangGraph que:
1. Envia mensagem de status ("Estou analisando...")
2. Inicia indicador de digitação contínuo
3. Obtém a mídia (download via Cloud API)
4. Processa a mídia (transcrição/análise) — paralelizado quando possível
5. Chama a API de fact-checking
6. Cancela o indicador de digitação
7. Retorna o rationale

Adaptado para a WhatsApp Business Cloud API.
Funções de mensagens citadas em grupo (Switch9) comentadas.
"""

import asyncio
import base64
import logging
import struct

from nodes import ai_services, whatsapp_api, fact_checker
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

    Flow: Enviar status → Typing loop → Download mídia → Transcrever → Fact-check → Cancelar typing.
    """
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    # 1. Enviar mensagem de status
    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )

    # 1b. Iniciar indicador de digitação contínuo
    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        # 2. Download da mídia e converter para base64
        audio_b64 = await whatsapp_api.download_media_as_base64(media_id)

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
    finally:
        typing_task.cancel()


# ──────────────────────── Texto (direto) ────────────────────────


async def process_text(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de texto direta.

    Flow: Enviar status → Typing loop → Fact-check → Cancelar typing.
    """
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    mensagem = state.get("mensagem", "")

    # 1. Enviar mensagem de status
    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando a mensagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )

    # 1b. Iniciar indicador de digitação contínuo
    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        # 2. Fact-check
        result = await fact_checker.check_text(
            state["endpoint_api"],
            mensagem.replace("\n", " "),
            content_type="text",
        )

        return {
            "rationale": result.get("rationale", ""),
        }  # type: ignore[return-value]
    finally:
        typing_task.cancel()


# ──────────────────────── Imagem (direto) ────────────────────────


async def process_image(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de imagem direta.

    Flow: Enviar status → Typing loop → Download mídia →
          Analisar imagem + Reverse search (PARALELO) → Merge → Fact-check → Cancelar typing.
    """
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    # 1. Enviar mensagem de status
    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )

    # 1b. Iniciar indicador de digitação contínuo
    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        # 2. Download da mídia e converter para base64
        image_b64 = await whatsapp_api.download_media_as_base64(media_id)

        # 3+4. Analisar imagem e Reverse search em PARALELO
        image_analysis, reverse_result = await asyncio.gather(
            ai_services.analyze_image_content(image_b64),
            ai_services.reverse_image_search(image_b64),
        )

        # 5. Montar descrição (merge dos resultados)
        description = (
            f"{image_analysis}\n\n"
            f"Informações de pesquisa reversa da imagem em sites da web: \n"
            f"{reverse_result}"
        )

        # 6. Legenda extraída no data_extractor (campo caption no state)
        caption = state.get("caption", "")

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
    finally:
        typing_task.cancel()


# ──────────────────────── Vídeo (direto) ────────────────────────


async def process_video(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de vídeo direta.

    Flow: Enviar status → Typing loop → Download mídia → Verificar duração →
          Analisar vídeo → Fact-check → Cancelar typing.
    """
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    # 1. Enviar mensagem de status
    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )

    # 1b. Iniciar indicador de digitação contínuo
    typing_task = await whatsapp_api.start_typing_loop(msg_id)

    try:
        # 2. Download da mídia e converter para base64
        video_b64 = await whatsapp_api.download_media_as_base64(media_id)

        # 3. Verificar duração (máx 2 minutos = 120 segundos)
        try:
            duration = get_video_duration_from_base64(video_b64)
        except Exception:
            duration = 0

        if duration >= 120:
            typing_task.cancel()
            await whatsapp_api.send_text(
                remote_jid,
                "Para que eu possa analizar o conteúdo do vídeo, "
                "ele precisa ter uma duração máxima de 2 minutos.",
                quoted_message_id=msg_id,
            )
            return {"rationale": "", "duration": duration}  # type: ignore[return-value]

        # 4. Analisar vídeo com Gemini
        description = await ai_services.analyze_video(video_b64)

        # 5. Legenda extraída no data_extractor (campo caption no state)
        caption = state.get("caption", "")

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
    finally:
        typing_task.cancel()


# ══════════════════════════════════════════════════════════════════════
# FUNCIONALIDADES DE GRUPO — Comentadas (migração apenas para DM)
# Processamento de mensagens citadas (Switch9)
# ══════════════════════════════════════════════════════════════════════

# async def process_quoted_audio(state: WorkflowState) -> WorkflowState:
#     """Processa áudio citado em grupo (Switch9 → audioMessage)."""
#     instancia = state["instancia"]
#     remote_jid = state["numero_quem_enviou"]
#     msg_id = state["id_mensagem"]
#     stanza_id = state.get("stanza_id", "")
#     chave_api = state.get("chave_api")
#
#     await evolution_api.send_text(
#         instancia, remote_jid,
#         "Estou analisando o áudio para verificar se é fake news. "
#         "Isso pode levar de 10 segundos a 1 minuto.",
#         quoted_message_id=msg_id, api_key=chave_api,
#     )
#     evolution_api.send_presence_fire_and_forget(
#         instancia, remote_jid, "composing", chave_api
#     )
#     media = await evolution_api.get_base64_from_quoted_message(
#         instancia, stanza_id, chave_api
#     )
#     audio_b64 = media.get("base64", media.get("data", {}).get("base64", ""))
#     transcription = await ai_services.transcribe_audio(audio_b64)
#     result = await fact_checker.check_text(
#         state["endpoint_api"], transcription.replace("\n", " "),
#         content_type="audio",
#     )
#     return {
#         "transcription": transcription, "media_base64": audio_b64,
#         "rationale": result.get("rationale", ""),
#         "response_without_links": result.get("responseWithoutLinks", ""),
#     }


# async def process_quoted_text(state: WorkflowState) -> WorkflowState:
#     """Processa texto citado em grupo (Switch9 → conversation)."""
#     instancia = state["instancia"]
#     remote_jid = state["numero_quem_enviou"]
#     msg_id = state["id_mensagem"]
#     chave_api = state.get("chave_api")
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#     quoted_text = context_info.get("quotedMessage", {}).get("conversation", "")
#
#     await evolution_api.send_text(
#         instancia, remote_jid,
#         "Estou analisando a mensagem para verificar se é uma fake news ou não.",
#         quoted_message_id=msg_id, api_key=chave_api,
#     )
#     evolution_api.send_presence_fire_and_forget(
#         instancia, remote_jid, "composing", chave_api
#     )
#     result = await fact_checker.check_text(
#         state["endpoint_api"], quoted_text.replace("\n", " "),
#         content_type="text",
#     )
#     return {"mensagem": quoted_text, "rationale": result.get("rationale", "")}


# async def process_quoted_image(state: WorkflowState) -> WorkflowState:
#     """Processa imagem citada em grupo (Switch9 → imageMessage/stickerMessage)."""
#     instancia = state["instancia"]
#     remote_jid = state["numero_quem_enviou"]
#     msg_id = state["id_mensagem"]
#     stanza_id = state.get("stanza_id", "")
#     chave_api = state.get("chave_api")
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#
#     await evolution_api.send_text(
#         instancia, remote_jid,
#         "Estou analisando a imagem para verificar se é fake news. "
#         "Isso pode levar de 10 segundos a 1 minuto.",
#         quoted_message_id=msg_id, api_key=chave_api,
#     )
#     evolution_api.send_presence_fire_and_forget(
#         instancia, remote_jid, "composing", chave_api
#     )
#     media = await evolution_api.get_base64_from_quoted_message(
#         instancia, stanza_id, chave_api
#     )
#     image_b64 = media.get("base64", media.get("data", {}).get("base64", ""))
#     image_analysis = await ai_services.analyze_image_content(image_b64)
#     reverse_result = await ai_services.reverse_image_search(image_b64)
#     description = (
#         f"{image_analysis}\n\n"
#         f"Informações de pesquisa reversa da imagem em sites da web: \n"
#         f"{reverse_result}"
#     )
#     caption = (
#         context_info.get("quotedMessage", {})
#         .get("imageMessage", {}).get("caption", "")
#     )
#     content_parts = [{"textContent": description, "type": "image"}]
#     if caption:
#         content_parts.append({"textContent": caption, "type": "text"})
#     result = await fact_checker.check_content(state["endpoint_api"], content_parts)
#     return {
#         "description": description, "caption": caption,
#         "media_base64": image_b64, "rationale": result.get("rationale", ""),
#     }


# async def process_quoted_video(state: WorkflowState) -> WorkflowState:
#     """Processa vídeo citado em grupo (Switch9 → videoMessage)."""
#     instancia = state["instancia"]
#     remote_jid = state["numero_quem_enviou"]
#     msg_id = state["id_mensagem"]
#     stanza_id = state.get("stanza_id", "")
#     chave_api = state.get("chave_api")
#     body = state.get("raw_body", {})
#     data = body.get("data", {})
#     context_info = get_context_info(data)
#
#     await evolution_api.send_text(
#         instancia, remote_jid,
#         "Estou analisando o vídeo para verificar se é fake news. "
#         "Isso pode levar de 10 segundos a 1 minuto.",
#         quoted_message_id=msg_id, api_key=chave_api,
#     )
#     evolution_api.send_presence_fire_and_forget(
#         instancia, remote_jid, "composing", chave_api
#     )
#     media = await evolution_api.get_base64_from_quoted_message(
#         instancia, stanza_id, chave_api
#     )
#     video_b64 = media.get("base64", media.get("data", {}).get("base64", ""))
#     try:
#         duration = get_video_duration_from_base64(video_b64)
#     except Exception:
#         duration = 0
#     if duration >= 120:
#         await evolution_api.send_text(
#             instancia, remote_jid,
#             "Para que eu possa analizar o conteúdo do vídeo, "
#             "ele precisa ter uma duração máxima de 2 minutos.",
#             quoted_message_id=msg_id, api_key=chave_api,
#         )
#         return {"rationale": "", "duration": duration}
#     description = await ai_services.analyze_video(video_b64)
#     caption = (
#         context_info.get("quotedMessage", {})
#         .get("videoMessage", {}).get("caption", "")
#     )
#     content_parts = [{"textContent": description, "type": "video"}]
#     if caption:
#         content_parts.append({"textContent": caption, "type": "text"})
#     result = await fact_checker.check_content(state["endpoint_api"], content_parts)
#     return {
#         "description": description, "caption": caption,
#         "media_base64": video_b64, "duration": duration,
#         "rationale": result.get("rationale", ""),
#     }

# ══════════════════════════════════════════════════════════════════════
# FIM — Funcionalidades de grupo
# ══════════════════════════════════════════════════════════════════════
