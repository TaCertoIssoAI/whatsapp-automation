"""Processamento de mídia: áudio, imagem e vídeo.

Cada função de processamento é um nó do LangGraph que:
1. Envia mensagem de status ("Estou analisando...")
2. Obtém a mídia (via Meta Graph API — Cloud API) ou base64 (Baileys)
3. Processa a mídia (transcrição/análise)
4. Chama a API de fact-checking
5. Retorna o rationale

Cobre ambos os caminhos do n8n:
- Mensagens diretas (Switch6)
- Mensagens citadas em grupos (Switch9)

Nota Cloud API: a mídia vem com um media_id no payload do webhook.
O download é feito em 2 etapas via Meta Graph API com Bearer token.
"""

import base64
import logging
import struct

import httpx

from nodes import ai_services, evolution_api, fact_checker
from nodes.data_extractor import get_context_info
from state import WorkflowState

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


# ──────────────────────── Utilitários ────────────────────────


def _is_send_error(result: dict) -> bool:
    """Verifica se o resultado de um send_text/send_audio indica erro."""
    if not isinstance(result, dict):
        return False
    return result.get("status") == "error"


async def _safe_send_status(
    instancia: str,
    remote_jid: str,
    text: str,
    chave_api: str | None,
) -> bool:
    """Envia mensagem de status com tratamento de erro.
    
    Retorna True se enviou com sucesso, False se falhou.
    """
    try:
        result = await evolution_api.send_text(
            instancia, remote_jid, text, api_key=chave_api
        )
        if _is_send_error(result):
            logger.error(
                "Falha ao enviar mensagem de status: %s", 
                result.get("error", "unknown")
            )
            return False
        return True
    except Exception as e:
        logger.error("Exceção ao enviar mensagem de status: %s", e)
        return False


async def _download_media_from_meta(media_id: str) -> str:
    """Baixa mídia via Meta Graph API (2 etapas) e retorna em base64.

    Etapa 1: GET https://graph.facebook.com/v22.0/{media_id} → obtém URL de download
    Etapa 2: GET {download_url} com Bearer token → obtém bytes da mídia
    """
    import config
    access_token = config.WHATSAPP_ACCESS_TOKEN
    if not access_token:
        raise ValueError("WHATSAPP_ACCESS_TOKEN não configurado no .env")

    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # Etapa 1: obter URL de download
        graph_url = f"https://graph.facebook.com/v22.0/{media_id}"
        logger.info("Meta Graph API — obtendo URL de download para media_id=%s", media_id)
        resp1 = await client.get(graph_url, headers=headers)
        resp1.raise_for_status()
        download_url = resp1.json().get("url", "")
        if not download_url:
            raise ValueError(f"Meta Graph API não retornou URL para media_id={media_id}")

        # Etapa 2: baixar os bytes da mídia
        logger.info("Meta Graph API — baixando mídia de %s", download_url[:80])
        resp2 = await client.get(download_url, headers=headers)
        resp2.raise_for_status()
        return base64.b64encode(resp2.content).decode("utf-8")


def _extract_media_id(data: dict) -> str:
    """Extrai o media_id do payload do webhook (Cloud API).
    
    O campo 'id' dentro do tipo de mídia contém o media_id necessário
    para baixar via Meta Graph API.
    """
    message = data.get("message", {})
    for msg_type in ("imageMessage", "videoMessage", "audioMessage",
                     "documentMessage", "stickerMessage"):
        msg_data = message.get(msg_type, {})
        if msg_data:
            return msg_data.get("id", "")
    return ""


async def _get_media_b64(
    instance: str,
    msg_id: str,
    media_id: str,
    chave_api: str | None,
) -> str:
    """Obtém mídia em base64 via Meta Graph API (Cloud API).

    1. Se media_id fornecido → baixa via Meta Graph API (2 etapas).
    2. Caso contrário → tenta get_media_base64 da Evolution API (Baileys fallback).
    """
    if media_id:
        logger.info("Baixando mídia via Meta Graph API — media_id=%s", media_id)
        return await _download_media_from_meta(media_id)

    logger.info("Tentando get_media_base64 (Baileys/fallback)")
    result = await evolution_api.get_media_base64(instance, msg_id, chave_api)
    return result.get("data", {}).get("base64", result.get("base64", ""))


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
    """Processa mensagem de áudio direta."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})

    # Extrai media_id do payload (Cloud API)
    media_id = _extract_media_id(data)

    # 1. Enviar mensagem de status
    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # 2. Obter mídia (Meta Graph API ou base64)
    try:
        audio_b64 = await _get_media_b64(instancia, msg_id, media_id, chave_api)
    except Exception as exc:
        logger.error("Falha ao baixar áudio: %s", exc)
        audio_b64 = ""

    if not audio_b64:
        logger.error("Não foi possível obter o áudio (msg_id=%s, url=%s)", msg_id, media_url)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar o áudio. Tente reenviar.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

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
    """Processa mensagem de texto direta."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    mensagem = state.get("mensagem", "")
    chave_api = state.get("chave_api")

    # 1. Enviar mensagem de status
    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando a mensagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
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
    """Processa mensagem de imagem direta."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})

    # Extrai media_id do payload (Cloud API)
    media_id = _extract_media_id(data)

    # 1. Enviar mensagem de status
    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # 2. Obter mídia (Meta Graph API ou base64)
    try:
        image_b64 = await _get_media_b64(instancia, msg_id, media_id, chave_api)
    except Exception as exc:
        logger.error("Falha ao baixar imagem: %s", exc)
        image_b64 = ""

    if not image_b64:
        logger.error("Não foi possível obter a imagem (msg_id=%s, url=%s)", msg_id, media_url)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar a imagem. Tente reenviar.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

    # 3. Analisar imagem
    image_analysis = await ai_services.analyze_image_content(image_b64)

    # 4. Reverse search
    reverse_result = await ai_services.reverse_image_search(image_b64)

    # 5. Montar descrição
    description = (
        f"{image_analysis}\n\n"
        f"Informações de pesquisa reversa da imagem em sites da web: \n"
        f"{reverse_result}"
    )

    # 6. Verificar legenda
    caption = data.get("message", {}).get("imageMessage", {}).get("caption", "")

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
    """Processa mensagem de vídeo direta."""
    instancia = state["instancia"]
    remote_jid = state["numero_quem_enviou"]
    msg_id = state["id_mensagem"]
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})

    # Extrai media_id do payload (Cloud API)
    media_id = _extract_media_id(data)

    # 1. Enviar mensagem de status
    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # 2. Obter mídia (Meta Graph API ou base64)
    try:
        video_b64 = await _get_media_b64(instancia, msg_id, media_id, chave_api)
    except Exception as exc:
        logger.error("Falha ao baixar vídeo: %s", exc)
        video_b64 = ""

    if not video_b64:
        logger.error("Não foi possível obter o vídeo (msg_id=%s, url=%s)", msg_id, media_url)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar o vídeo. Tente reenviar.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

    # 3. Verificar duração (máx 2 minutos = 120 segundos)
    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        await _safe_send_status(
            instancia,
            remote_jid,
            "Para que eu possa analizar o conteúdo do vídeo, "
            "ele precisa ter uma duração máxima de 2 minutos.",
            chave_api,
        )
        return {"rationale": "", "duration": duration}  # type: ignore[return-value]

    # 4. Analisar vídeo com Gemini
    description = await ai_services.analyze_video(video_b64)

    # 5. Verificar legenda
    caption = data.get("message", {}).get("videoMessage", {}).get("caption", "")

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
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")

    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando o áudio para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # Tenta obter o áudio da mensagem citada (pode não funcionar com Cloud API)
    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    audio_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    if not audio_b64:
        logger.warning("Não foi possível obter áudio citado (stanzaId=%s) — Cloud API limitação", stanza_id)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar o áudio citado. Com a API oficial, "
            "mensagens citadas de mídia podem não ser acessíveis diretamente.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

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
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)
    quoted_msg = context_info.get("quotedMessage", {})
    # Texto pode vir em "conversation" ou "extendedTextMessage.text"
    quoted_text = (
        quoted_msg.get("conversation", "")
        or quoted_msg.get("extendedTextMessage", {}).get("text", "")
    )

    if not quoted_text:
        await _safe_send_status(
            instancia,
            remote_jid,
            "Não consegui identificar o texto citado. Tente encaminhar a mensagem diretamente.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando a mensagem para verificar se é uma fake news ou não.",
        chave_api,
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
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)

    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando a imagem para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # Tenta obter a imagem da mensagem citada (pode não funcionar com Cloud API)
    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    image_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    if not image_b64:
        logger.warning("Não foi possível obter imagem citada (stanzaId=%s) — Cloud API limitação", stanza_id)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar a imagem citada. Com a API oficial, "
            "mensagens citadas de mídia podem não ser acessíveis diretamente.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

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
    stanza_id = state.get("stanza_id", "")
    chave_api = state.get("chave_api")
    body = state.get("raw_body", {})
    data = body.get("data", {})
    context_info = get_context_info(data)

    await _safe_send_status(
        instancia,
        remote_jid,
        "Estou analisando o vídeo para verificar se é fake news. "
        "Isso pode levar de 10 segundos a 1 minuto.",
        chave_api,
    )

    # Tenta obter o vídeo da mensagem citada (pode não funcionar com Cloud API)
    media = await evolution_api.get_base64_from_quoted_message(
        instancia, stanza_id, chave_api
    )
    video_b64 = media.get("base64", media.get("data", {}).get("base64", ""))

    if not video_b64:
        logger.warning("Não foi possível obter vídeo citado (stanzaId=%s) — Cloud API limitação", stanza_id)
        await _safe_send_status(
            instancia, remote_jid,
            "Não consegui acessar o vídeo citado. Com a API oficial, "
            "mensagens citadas de mídia podem não ser acessíveis diretamente.",
            chave_api,
        )
        return {}  # type: ignore[return-value]

    # Verificar duração
    try:
        duration = get_video_duration_from_base64(video_b64)
    except Exception:
        duration = 0

    if duration >= 120:
        await _safe_send_status(
            instancia,
            remote_jid,
            "Para que eu possa analizar o conteúdo do vídeo, "
            "ele precisa ter uma duração máxima de 2 minutos.",
            chave_api,
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
