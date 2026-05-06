"""Processamento de mídia: áudio, imagem e vídeo.

Cada função de processamento é um nó do LangGraph que:
1. Envia mensagem de status ("Estou analisando...")
2. Obtém a mídia (download via Cloud API)
3. Processa a mídia (transcrição/análise)
4. Chama a API de fact-checking
5. Retorna o rationale

Adaptado para a WhatsApp Business Cloud API.
Funções de mensagens citadas em grupo (Switch9) comentadas.
"""

import asyncio
import base64
import logging
import struct

from nodes import ai_services, whatsapp_api, fact_checker
from state import WorkflowState
import config

logger = logging.getLogger(__name__)

_ERROR_MSG = (
    "⚠️ Desculpe, ocorreu um erro ao processar sua mensagem. "
    "Por favor, tente enviar novamente."
)


async def _send_error(remote_jid: str, msg_id: str, detail: str = "") -> None:
    """Envia mensagem de erro para o usuário."""
    if detail == "O serviço de verificação está temporariamente indisponível.":
        text = "⚠️ Desculpe, ocorreu um erro ao processar sua mensagem pois o serviço de verificação está temporariamente indisponível. Em breve o sistema voltará a funcionar normalmente."
    else:
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
        status_msg = (
            "Estou analisando o áudio para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto."
        )
        batch_notes = state.get("batch_status_notes", "")
        if batch_notes:
            status_msg += f"\n\nℹ️ {batch_notes}"
        await whatsapp_api.send_text(
            remote_jid,
            status_msg,
            quoted_message_id=msg_id,
            keep_typing=True,
        )
    except Exception:
        pass  # Status message is not critical

    try:
        audio_b64 = await whatsapp_api.download_media_as_base64(media_id)
    except Exception:
        logger.exception("Falha ao baixar áudio media_id=%s", media_id)
        await _send_error(remote_jid, msg_id, "Não consegui baixar o áudio.")
        return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

    try:
        if config.DEEP_FAKE_AUDIO:
            transcription, deepfake_results = await asyncio.gather(
                ai_services.transcribe_audio(audio_b64),
                ai_services.detect_deepfake(audio_b64, filename="audio.ogg"),
            )
        else:
            transcription = await ai_services.transcribe_audio(audio_b64)
            deepfake_results = None
    except Exception as e:
        logger.error(f"Falha ao transcrever áudio media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui transcrever o áudio.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Transcrição do áudio {media_id[:20]}: {transcription[:100]}...")

    # Montar content_parts para o fact-checker
    batch_extra = state.get("batch_extra_text", "")
    content_parts = [
        {"textContent": transcription.replace("\n", " "), "type": "audio"},
    ]
    if batch_extra:
        content_parts.append({"textContent": batch_extra, "type": "text"})

    try:
        result = await fact_checker.check_content(
            state.get("endpoint_api", ""), content_parts, deepfake_results=deepfake_results
        )
    except Exception as e:
        logger.error(f"Falha no fact-check do áudio media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

    return {
        "transcription": transcription,
        "media_base64": audio_b64,
        "rationale": result.get("rationale", ""),
        "audio_script": result.get("responseWithoutLinks", ""),
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
        status_msg = (
            "Estou analisando a mensagem para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto."
        )
        batch_notes = state.get("batch_status_notes", "")
        if batch_notes:
            status_msg += f"\n\nℹ️ {batch_notes}"
        await whatsapp_api.send_text(
            remote_jid,
            status_msg,
            quoted_message_id=msg_id,
            keep_typing=True,
        )
    except Exception:
        pass

    try:
        result = await fact_checker.check_text(
            state.get("endpoint_api", ""),
            mensagem.replace("\n", " "),
            content_type="text",
        )
    except Exception as e:
        logger.error(f"Falha no fact-check do texto: {e}")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

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
        status_msg = (
            "Estou analisando a imagem para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto."
        )
        batch_notes = state.get("batch_status_notes", "")
        if batch_notes:
            status_msg += f"\n\nℹ️ {batch_notes}"
        await whatsapp_api.send_text(
            remote_jid,
            status_msg,
            quoted_message_id=msg_id,
            keep_typing=True,
        )
    except Exception:
        pass

    try:
        image_b64 = await whatsapp_api.download_media_as_base64(media_id)

        # Analisar imagem + Deep-fake (concorrente)
        if config.DEEP_FAKE_IMAGE:
            image_analysis, deepfake_results = await asyncio.gather(
                ai_services.analyze_image_content(image_b64),
                ai_services.detect_deepfake(image_b64, filename="image.jpg"),
            )
        else:
            image_analysis = await ai_services.analyze_image_content(image_b64)
            deepfake_results = None
    except Exception as e:
        logger.error(f"Falha ao analisar imagem media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui analisar a imagem.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Análise da imagem {media_id[:20]}: {image_analysis[:100]}...")

    description = f"{image_analysis}\n\n"

    caption = state.get("caption", "")

    # Montar content_parts: descrição da imagem + texto extra do batch + legenda
    batch_extra = state.get("batch_extra_text", "")
    content_parts = [{"textContent": description, "type": "image"}]
    # Combinar caption e batch_extra_text como texto adicional
    extra_texts = []
    if batch_extra:
        extra_texts.append(batch_extra)
    if caption:
        extra_texts.append(caption)
    if extra_texts:
        content_parts.append({"textContent": "\n".join(extra_texts), "type": "text"})

    try:
        result = await fact_checker.check_content(
            state["endpoint_api"], content_parts, deepfake_results=deepfake_results
        )
    except Exception as e:
        logger.error(f"Falha no fact-check da imagem media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

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
        status_msg = (
            "Estou analisando o vídeo para verificar se é fake news. "
            "Isso pode levar de 10 segundos a 1 minuto."
        )
        batch_notes = state.get("batch_status_notes", "")
        if batch_notes:
            status_msg += f"\n\nℹ️ {batch_notes}"
        await whatsapp_api.send_text(
            remote_jid,
            status_msg,
            quoted_message_id=msg_id,
            keep_typing=True,
        )
    except Exception:
        pass

    # Verificar se é vídeo pré-baixado via yt-dlp (media_id sintético)
    from nodes.video_link_downloader import get_cached_video, is_ytdlp_media_id
    if is_ytdlp_media_id(media_id):
        cached = get_cached_video(media_id)
        if cached:
            video_b64 = cached
            logger.info("process_video: usando vídeo pré-baixado (yt-dlp) para %s", remote_jid)
        else:
            logger.warning("process_video: cache yt-dlp expirado para media_id=%s", media_id)
            await _send_error(remote_jid, msg_id, "O download do vídeo expirou. Por favor, envie o link novamente.")
            return {"rationale": "", "error_sent": True}  # type: ignore[return-value]
    else:
        try:
            video_b64 = await whatsapp_api.download_media_as_base64(media_id)
        except Exception:
            logger.exception("Falha ao baixar vídeo media_id=%s", media_id)
            await _send_error(remote_jid, msg_id, "Não consegui baixar o vídeo.")
            return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

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
        # Analisar vídeo com Gemini + Deep-fake (concorrente)
        if config.DEEP_FAKE_VIDEO:
            description, deepfake_results = await asyncio.gather(
                ai_services.analyze_video(video_b64),
                ai_services.detect_deepfake(video_b64, filename="video.mp4"),
            )
        else:
            description = await ai_services.analyze_video(video_b64)
            deepfake_results = None
    except Exception as e:
        logger.error(f"Falha ao analisar vídeo media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "Não consegui analisar o vídeo.")
        return {"rationale": "", "error_sent": True}

    logger.info(f"Análise do vídeo {media_id[:20]}: {description[:100]}...")

    caption = state.get("caption", "")

    # Montar content_parts: descrição do vídeo + texto extra do batch + legenda
    batch_extra = state.get("batch_extra_text", "")
    content_parts = [{"textContent": description, "type": "video"}]
    extra_texts = []
    if batch_extra:
        extra_texts.append(batch_extra)
    if caption:
        extra_texts.append(caption)
    if extra_texts:
        content_parts.append({"textContent": "\n".join(extra_texts), "type": "text"})

    try:
        result = await fact_checker.check_content(
            state["endpoint_api"], content_parts, deepfake_results=deepfake_results
        )
    except Exception as e:
        logger.error(f"Falha no fact-check do vídeo media_id={media_id}: {e}")
        await _send_error(remote_jid, msg_id, "O serviço de verificação está temporariamente indisponível.")
        return {"rationale": "", "error_sent": True}  # type: ignore[return-value]

    return {
        "description": description,
        "caption": caption,
        "media_base64": video_b64,
        "duration": duration,
        "rationale": result.get("rationale", ""),
    }  # type: ignore[return-value]
