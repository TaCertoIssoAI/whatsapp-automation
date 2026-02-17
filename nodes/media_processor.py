"""Processamento de mídia: áudio, texto, imagem e vídeo."""

import base64
import logging
import struct

from nodes import ai_services, whatsapp_api, fact_checker
from state import WorkflowState

logger = logging.getLogger(__name__)


def get_video_duration_from_base64(video_base64: str) -> float:
    """Extrai a duração de um MP4 do base64."""
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


async def process_audio(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de áudio: download → transcrição → fact-check."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )
    whatsapp_api.send_typing_fire_and_forget(msg_id)

    audio_b64 = await whatsapp_api.download_media_as_base64(media_id)
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


async def process_text(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de texto: fact-check direto."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    mensagem = state.get("mensagem", "")

    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando a mensagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )
    whatsapp_api.send_typing_fire_and_forget(msg_id)

    result = await fact_checker.check_text(
        state["endpoint_api"],
        mensagem.replace("\n", " "),
        content_type="text",
    )

    return {"rationale": result.get("rationale", "")}  # type: ignore[return-value]


async def process_image(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de imagem: download → análise + reverse search → fact-check."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )
    whatsapp_api.send_typing_fire_and_forget(msg_id)

    image_b64 = await whatsapp_api.download_media_as_base64(media_id)
    image_analysis = await ai_services.analyze_image_content(image_b64)
    reverse_result = await ai_services.reverse_image_search(image_b64)

    description = (
        f"{image_analysis}\n\n"
        f"Informações de pesquisa reversa da imagem em sites da web: \n"
        f"{reverse_result}"
    )

    caption = state.get("caption", "")

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


async def process_video(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de vídeo: download → duração → análise → fact-check."""
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    media_id = state.get("media_id", "")

    await whatsapp_api.send_text(
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        quoted_message_id=msg_id,
    )
    whatsapp_api.send_typing_fire_and_forget(msg_id)

    video_b64 = await whatsapp_api.download_media_as_base64(media_id)

    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        await whatsapp_api.send_text(
            remote_jid,
            "Para que eu possa analizar o conteúdo do vídeo, "
            "ele precisa ter uma duração máxima de 2 minutos.",
            quoted_message_id=msg_id,
        )
        return {"rationale": "", "duration": duration}  # type: ignore[return-value]

    description = await ai_services.analyze_video(video_b64)
    caption = state.get("caption", "")

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
