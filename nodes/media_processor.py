"""Processamento de mídia: áudio, texto, imagem e vídeo."""

import base64
import logging
import struct

from nodes import ai_services, whatsapp_api, fact_checker
from state import WorkflowState

logger = logging.getLogger(__name__)

_ERROR_MSG = (
    "⚠️ Desculpe, ocorreu um erro ao processar sua mensagem. "
    "Por favor, tente enviar novamente."
)


async def _send_error(remote_jid: str, msg_id: str, detail: str = "") -> None:
    """Envia mensagem de erro para o usuário."""
    text = _ERROR_MSG
    if detail:
        text += f"\n\nDetalhes: {detail}"
    try:
        await whatsapp_api.send_text(remote_jid, text, quoted_message_id=msg_id)
    except Exception:
        logger.error("Falha ao enviar mensagem de erro para %s", remote_jid)


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
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    media_id = state.get("media_id", "")

    if not remote_jid or not media_id:
        logger.error("process_audio: dados insuficientes (jid=%s, media=%s)", remote_jid, media_id)
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Estou analisando o áudio para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto.",
            quoted_message_id=msg_id,
        )
    except Exception:
        pass  # Status message is not critical

    try:
        audio_b64 = await whatsapp_api.download_media_as_base64(media_id)
    except Exception:
        logger.exception("Falha ao baixar áudio media_id=%s", media_id)
        await _send_error(remote_jid, msg_id, "Não consegui baixar o áudio.")
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        transcription = await ai_services.transcribe_audio(audio_b64)
    except Exception:
        logger.exception("Falha ao transcrever áudio")
        await _send_error(remote_jid, msg_id, "Não consegui transcrever o áudio.")
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        result = await fact_checker.check_text(
            state.get("endpoint_api", ""),
            transcription.replace("\n", " "),
            content_type="audio",
        )
    except Exception:
        logger.exception("Falha no fact-check do áudio")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": ""}  # type: ignore[return-value]

    return {
        "transcription": transcription,
        "media_base64": audio_b64,
        "rationale": result.get("rationale", ""),
        "response_without_links": result.get("responseWithoutLinks", ""),
    }  # type: ignore[return-value]


async def process_text(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de texto: fact-check direto."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    mensagem = state.get("mensagem", "")

    if not remote_jid or not mensagem:
        logger.error("process_text: dados insuficientes (jid=%s, msg=%s)", remote_jid, bool(mensagem))
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Estou analisando a mensagem para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto.",
            quoted_message_id=msg_id,
        )
    except Exception:
        pass

    try:
        result = await fact_checker.check_text(
            state.get("endpoint_api", ""),
            mensagem.replace("\n", " "),
            content_type="text",
        )
    except Exception:
        logger.exception("Falha no fact-check do texto")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": ""}  # type: ignore[return-value]

    return {"rationale": result.get("rationale", "")}  # type: ignore[return-value]


async def process_image(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de imagem: download → análise + reverse search → fact-check."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    media_id = state.get("media_id", "")

    if not remote_jid or not media_id:
        logger.error("process_image: dados insuficientes (jid=%s, media=%s)", remote_jid, media_id)
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Estou analisando a imagem para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto.",
            quoted_message_id=msg_id,
        )
    except Exception:
        pass

    try:
        image_b64 = await whatsapp_api.download_media_as_base64(media_id)
    except Exception:
        logger.exception("Falha ao baixar imagem media_id=%s", media_id)
        await _send_error(remote_jid, msg_id, "Não consegui baixar a imagem.")
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        image_analysis = await ai_services.analyze_image_content(image_b64)
    except Exception:
        logger.exception("Falha ao analisar imagem")
        await _send_error(remote_jid, msg_id, "Não consegui analisar a imagem.")
        return {"rationale": ""}  # type: ignore[return-value]

    # Reverse search pode falhar sem impedir o fluxo
    try:
        reverse_result = await ai_services.reverse_image_search(image_b64)
    except Exception:
        logger.warning("Reverse image search falhou, continuando sem ela")
        reverse_result = "Pesquisa reversa indisponível."

    description = (
        f"{image_analysis}\n\n"
        f"Informações de pesquisa reversa da imagem em sites da web: \n"
        f"{reverse_result}"
    )

    caption = state.get("caption", "")

    content_parts = [{"textContent": description, "type": "image"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    try:
        result = await fact_checker.check_content(state.get("endpoint_api", ""), content_parts)
    except Exception:
        logger.exception("Falha no fact-check da imagem")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": ""}  # type: ignore[return-value]

    return {
        "description": description,
        "caption": caption,
        "media_base64": image_b64,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]


async def process_video(state: WorkflowState) -> WorkflowState:
    """Processa mensagem de vídeo: download → duração → análise → fact-check."""
    remote_jid = state.get("numero_quem_enviou", "")
    msg_id = state.get("id_mensagem", "")
    media_id = state.get("media_id", "")

    if not remote_jid or not media_id:
        logger.error("process_video: dados insuficientes (jid=%s, media=%s)", remote_jid, media_id)
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        await whatsapp_api.send_text(
            remote_jid,
            "Estou analisando o vídeo para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto.",
            quoted_message_id=msg_id,
        )
    except Exception:
        pass

    try:
        video_b64 = await whatsapp_api.download_media_as_base64(media_id)
    except Exception:
        logger.exception("Falha ao baixar vídeo media_id=%s", media_id)
        await _send_error(remote_jid, msg_id, "Não consegui baixar o vídeo.")
        return {"rationale": ""}  # type: ignore[return-value]

    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        try:
            await whatsapp_api.send_text(
                remote_jid,
                "Para que eu possa analizar o conteúdo do vídeo, "
                "ele precisa ter uma duração máxima de 2 minutos.",
                quoted_message_id=msg_id,
            )
        except Exception:
            pass
        return {"rationale": "", "duration": duration}  # type: ignore[return-value]

    try:
        description = await ai_services.analyze_video(video_b64)
    except Exception:
        logger.exception("Falha ao analisar vídeo")
        await _send_error(remote_jid, msg_id, "Não consegui analisar o vídeo.")
        return {"rationale": ""}  # type: ignore[return-value]

    caption = state.get("caption", "")

    content_parts = [{"textContent": description, "type": "video"}]
    if caption:
        content_parts.append({"textContent": caption, "type": "text"})

    try:
        result = await fact_checker.check_content(state.get("endpoint_api", ""), content_parts)
    except Exception:
        logger.exception("Falha no fact-check do vídeo")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": ""}  # type: ignore[return-value]

    return {
        "description": description,
        "caption": caption,
        "media_base64": video_b64,
        "duration": duration,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]
