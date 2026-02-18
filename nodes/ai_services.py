"""Serviços de inteligência artificial — Google Gemini + Cloud Vision."""

import asyncio
import base64
import io
import logging
import tempfile
from pathlib import Path

import httpx

import config

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 4, 8]

# Timeout individual para cada chamada Gemini (evita que uma chamada trave tudo)
_GEMINI_CALL_TIMEOUT = 120  # 2 minutos por tentativa


async def _retry_async(func, *args, label: str = ""):
    """Executa função async com retry e backoff para erros transientes."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return await func(*args)
        except (asyncio.TimeoutError, TimeoutError) as e:
            # Timeout do asyncio.wait_for — sempre transiente
            last_exc = e
            if attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "%s timeout (tentativa %d/%d), retry em %ds",
                    label, attempt + 1, _MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            else:
                raise
        except Exception as e:
            last_exc = e
            error_str = str(e).lower()
            is_transient = any(kw in error_str for kw in [
                "500", "503", "429", "overloaded", "resource_exhausted",
                "deadline", "timeout", "unavailable", "rate",
            ])
            if is_transient and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_DELAYS[attempt]
                logger.warning(
                    "%s falhou (tentativa %d/%d), retry em %ds: %s",
                    label, attempt + 1, _MAX_RETRIES, delay, e,
                )
                await asyncio.sleep(delay)
            else:
                raise
    raise last_exc  # type: ignore[misc]


# ──────────────────────── Gemini Client (lazy) ────────────────────────


def _get_gemini_client():
    """Retorna um cliente Gemini (criado sob demanda)."""
    from google import genai

    return genai.Client(api_key=config.GOOGLE_GEMINI_API_KEY)


# ──────────────────────── Gemini — Transcrição de Áudio ────────────────────────

TRANSCRIPTION_PROMPT = (
    "Transcreva o áudio a seguir com precisão. "
    "Retorne APENAS a transcrição do que foi dito, sem comentários, "
    "sem timestamps, sem identificação de falantes, sem formatação extra. "
    "Se o áudio estiver em português, retorne em português."
)


async def transcribe_audio(audio_base64: str) -> str:
    """Transcreve áudio usando Google Gemini com retry."""
    from google.genai import types

    client = _get_gemini_client()
    audio_bytes = base64.b64decode(audio_base64)

    async def _do():
        def _call():
            return client.models.generate_content(
                model=config.GEMINI_TRANSCRIPTION_MODEL,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/mp3"),
                    TRANSCRIPTION_PROMPT,
                ],
            )
        response = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=_GEMINI_CALL_TIMEOUT
        )
        return response.text or ""

    return await _retry_async(_do, label="transcribe_audio")


# ──────────────────────── Gemini — TTS ────────────────────────


def _pcm_to_ogg_opus(pcm_data: bytes, sample_rate: int = 24000) -> bytes:
    """Converte PCM bruto (16-bit mono) para OGG/Opus via pydub.

    A WhatsApp Cloud API exige áudio em OGG/Opus para mensagens de voz.
    """
    from pydub import AudioSegment

    audio = AudioSegment(
        data=pcm_data,
        sample_width=2,  # 16-bit
        frame_rate=sample_rate,
        channels=1,
    )

    ogg_buffer = io.BytesIO()
    audio.export(ogg_buffer, format="ogg", codec="libopus", bitrate="64k")
    return ogg_buffer.getvalue()


async def generate_tts(text: str) -> bytes:
    """Gera áudio via Gemini TTS com retry."""
    from google.genai import types

    client = _get_gemini_client()

    async def _do():
        def _call():
            return client.models.generate_content(
                model=config.GEMINI_TTS_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=config.GEMINI_TTS_VOICE,
                            )
                        )
                    ),
                ),
            )
        response = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=_GEMINI_CALL_TIMEOUT
        )
        audio_data = response.candidates[0].content.parts[0].inline_data.data
        ogg_bytes = await asyncio.to_thread(_pcm_to_ogg_opus, audio_data)
        return ogg_bytes

    return await _retry_async(_do, label="generate_tts")


# ──────────────────────── Google Gemini — Análise de Vídeo ────────────────────────

# Prompt para análise de vídeo (mesmo do n8n)
VIDEO_ANALYSIS_PROMPT = """Você receberá um vídeo enviado pelo usuário. Sua tarefa é:

- Analisar cuidadosamente todas as cenas do vídeo, frame a frame.
- Descrever detalhadamente tudo o que aparece: pessoas, objetos, ações, ambiente, iluminação, cores, expressões, movimentos, mudanças de cena e qualquer elemento visual relevante.
- Transcrever todo o áudio do vídeo, incluindo falas, ruídos, música, textos narrados e sons de fundo.
- Escrever todos os textos que aparecerem na tela, como legendas, placas, banners, telas de computador, mensagens, símbolos ou números.
- Indicar, sempre que possível, os momentos aproximados (timestamps) onde cada evento ocorre.
- Caso ocorram várias cenas, descrevê-las em ordem cronológica.

Retorne no seguinte formato:

Descrição completa do vídeo:
[sua descrição detalhada aqui]"""


async def analyze_video(video_base64: str) -> str:
    """Analisa vídeo usando Google Gemini com retry."""
    client = _get_gemini_client()
    video_bytes = base64.b64decode(video_base64)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = Path(tmp.name)

    try:
        uploaded_file = await asyncio.to_thread(client.files.upload, file=tmp_path)

        max_wait = 60
        poll_interval = 2
        waited = 0
        while uploaded_file.state and uploaded_file.state.name != "ACTIVE":
            if uploaded_file.state.name == "FAILED":
                raise RuntimeError(f"Upload do vídeo falhou: {uploaded_file.state.name}")
            if waited >= max_wait:
                raise RuntimeError(f"Timeout aguardando processamento do vídeo (estado: {uploaded_file.state.name})")
            await asyncio.sleep(poll_interval)
            waited += poll_interval
            uploaded_file = await asyncio.to_thread(client.files.get, name=uploaded_file.name)

        async def _do():
            def _generate():
                return client.models.generate_content(
                    model=config.GEMINI_VIDEO_MODEL,
                    contents=[uploaded_file, VIDEO_ANALYSIS_PROMPT],
                )
            response = await asyncio.wait_for(
                asyncio.to_thread(_generate), timeout=_GEMINI_CALL_TIMEOUT
            )
            return response.text or ""

        return await _retry_async(_do, label="analyze_video")
    finally:
        tmp_path.unlink(missing_ok=True)


# ──────────────────────── Gemini — Análise de Imagem ──────
# Equivalente ao sub-workflow 'analyze-image' do n8n


# Prompt EXATO do nó 'Analyze image2' do n8n
IMAGE_ANALYSIS_PROMPT = (
    "Você receberá uma imagem enviada pelo usuário, seu objetivo é transcrever "
    "a imagem enviada para o fact-checking de fake news seguindo as tarefas "
    "adiantes:  TAREFA 1: Você deve transcrever todo o texto de uma imagem, "
    "focando não apenas no texto mas em como ele está visualmente disposto "
    "(letras grandes, pequenas, CAPS LOCK ,negrito, itálico, cores). "
    'Ex: A imagem tem um título "Político perdeu tudo" em negrito e CAPS LOCK '
    "com letras grandes. TAREFA 2: Foque em transcrever elementos visuais/"
    "não-textuais  da imagem de forma a explicitar pessoas, especialmente "
    "figuras famosas, históricas, importantes, políticos ou celebridades, "
    "caso essas figuras estejam presentes, apenas mencione o NOME delas, "
    "não qualquer status dela como sua posição, emprego, se está vivo ou não. "
    "Também busque descrever entidades humanas e não humanas centrais à imagem."
    "\n\nNão dê tanto importância a detalhes cotidianos e comuns da paisagem, "
    "apenas em detalhes anormais que possam auxiliar no processo de fact-checking."
    "\n\nExemplos de descrições detalhadas que ajudam no fact-checking de fake news:"
    '\n\n"A imagem mostra o político Abraham Lincon numa pose constrangedora, '
    'sendo zombado por uma multidão"'
    "\n\nExemplo de uma descrição que não ajuda no fact-checking:"
    '\n\n"A imagem mostra um homem de terno e cabelo branco, numa festa, '
    'com convidados de smoking."'
    "\n\nRetornar no seguinte formato:"
    '\n\n"Descrição da imagem: [sua descrição detalhada aqui]'
    "\n\nLembre-se de adicionar na descrição da imagem todo o texto contido nela."
)


async def analyze_image_content(image_base64: str) -> str:
    """Analisa imagem usando Google Gemini com retry."""
    from google.genai import types

    client = _get_gemini_client()
    image_bytes = base64.b64decode(image_base64)

    async def _do():
        def _call():
            return client.models.generate_content(
                model=config.GEMINI_IMAGE_MODEL,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    IMAGE_ANALYSIS_PROMPT,
                ],
            )
        response = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=_GEMINI_CALL_TIMEOUT
        )
        return response.text or ""

    return await _retry_async(_do, label="analyze_image")


# ──────────────────────── Google Cloud Vision — Reverse Image Search ──────
# Equivalente ao sub-workflow 'reverse-search' do n8n

_VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"
_VISION_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def _parse_web_detection(response_data: dict) -> str:
    """Parseia a resposta da Google Cloud Vision API WEB_DETECTION.

    Equivalente ao 'Code in JavaScript' do sub-workflow reverse-search do n8n.
    """
    detection = (
        response_data
        .get("responses", [{}])[0]
        .get("webDetection")
    )

    if (
        not detection
        or not detection.get("fullMatchingImages")
        or len(detection["fullMatchingImages"]) == 0
    ):
        return "Nenhuma correspondência completa encontrada para esta imagem."

    # Web Entities
    entities_text = "Entidades Detectadas:\n"
    web_entities = detection.get("webEntities", [])
    if web_entities:
        for entity in web_entities:
            desc = entity.get("description")
            if desc:
                entities_text += f"- {desc}\n"
    else:
        entities_text += "- Nenhuma entidade encontrada.\n"

    # Pages With Matching Images (somente 3 primeiras, igual ao n8n)
    pages_text = "\nPáginas com Imagens Correspondentes:\n"
    pages = detection.get("pagesWithMatchingImages", [])
    if pages:
        for page in pages[:3]:
            title = page.get("pageTitle")
            if title:
                pages_text += f"- {title}\n"
    else:
        pages_text += "- Nenhuma página encontrada.\n"

    return entities_text + pages_text


async def reverse_image_search(image_base64: str) -> str:
    """Realiza pesquisa reversa de imagem usando Google Cloud Vision API.

    Equivalente ao sub-workflow 'reverse-search' do n8n.
    Usa o endpoint WEB_DETECTION da Vision API com OAuth2/API key.
    """
    api_key = config.GOOGLE_CLOUD_API_KEY
    if not api_key:
        logger.warning(
            "GOOGLE_CLOUD_API_KEY não configurada, "
            "pulando reverse image search."
        )
        return "Pesquisa reversa não disponível (API key não configurada)."

    url = f"{_VISION_API_URL}?key={api_key}"

    payload = {
        "requests": [
            {
                "image": {
                    "content": image_base64,
                },
                "features": [
                    {
                        "type": "WEB_DETECTION",
                    }
                ],
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=_VISION_TIMEOUT) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            result = resp.json()

        return _parse_web_detection(result)

    except Exception as e:
        logger.warning("Reverse image search falhou: %s", e)
        return "Não foi possível realizar a pesquisa reversa da imagem."
