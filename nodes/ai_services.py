"""Serviços de inteligência artificial — 100% Google Gemini.

- Gemini (transcrição de áudio)
- Gemini TTS (text-to-speech)
- Gemini (análise de imagem — sub-workflow analyze-image)
- Gemini (análise de vídeo)
- Google Cloud Vision API (reverse image search — sub-workflow reverse-search)
"""

import asyncio
import base64
import io
import logging
import tempfile
from pathlib import Path

import httpx

import config

logger = logging.getLogger(__name__)


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
    """Transcreve áudio usando Google Gemini.

    Recebe o áudio em base64, envia inline para o Gemini e retorna a transcrição.
    Equivalente ao nó 'Transcribe a recording2' do n8n.
    """
    from google.genai import types

    client = _get_gemini_client()
    audio_bytes = base64.b64decode(audio_base64)

    response = client.models.generate_content(
        model=config.GEMINI_TRANSCRIPTION_MODEL,
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type="audio/mp3"),
            TRANSCRIPTION_PROMPT,
        ],
    )

    text = response.text or ""
    logger.info("Áudio transcrito com sucesso via Gemini (%d chars)", len(text))
    return text


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
    """Gera áudio via Gemini TTS.

    Retorna os bytes do áudio em OGG/Opus (compatível com WhatsApp Cloud API).
    """
    from google.genai import types

    client = _get_gemini_client()

    response = client.models.generate_content(
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

    audio_data = response.candidates[0].content.parts[0].inline_data.data

    # Converter PCM bruto → OGG/Opus para compatibilidade com WhatsApp Cloud API
    ogg_bytes = await asyncio.to_thread(_pcm_to_ogg_opus, audio_data)

    logger.info("TTS gerado com sucesso via Gemini (%d bytes OGG/Opus)", len(ogg_bytes))
    return ogg_bytes


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
    """Analisa vídeo usando Google Gemini.

    Recebe o vídeo em base64, envia para o Gemini e retorna a descrição.
    Equivalente ao nó 'Analyze video2' do n8n.
    """
    client = _get_gemini_client()

    video_bytes = base64.b64decode(video_base64)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(video_bytes)
        tmp_path = Path(tmp.name)

    try:
        uploaded_file = client.files.upload(file=tmp_path)

        # Aguardar até o arquivo ficar ACTIVE (processamento do Gemini)
        max_wait = 60  # segundos
        poll_interval = 2  # segundos
        waited = 0
        while uploaded_file.state and uploaded_file.state.name != "ACTIVE":
            if uploaded_file.state.name == "FAILED":
                raise RuntimeError(
                    f"Upload do vídeo falhou: {uploaded_file.state.name}"
                )
            if waited >= max_wait:
                raise RuntimeError(
                    f"Timeout aguardando processamento do vídeo "
                    f"(estado: {uploaded_file.state.name})"
                )
            logger.info(
                "Aguardando processamento do vídeo... (estado: %s, %ds)",
                uploaded_file.state.name,
                waited,
            )
            await asyncio.sleep(poll_interval)
            waited += poll_interval
            uploaded_file = client.files.get(name=uploaded_file.name)

        logger.info("Arquivo de vídeo pronto (estado: ACTIVE)")

        response = client.models.generate_content(
            model=config.GEMINI_VIDEO_MODEL,
            contents=[uploaded_file, VIDEO_ANALYSIS_PROMPT],
        )
        description = response.text or ""
        logger.info("Vídeo analisado com sucesso (%d chars)", len(description))
        return description
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
    """Analisa imagem usando Google Gemini.

    Equivalente ao sub-workflow 'analyze-image' do n8n.
    Usa o mesmo prompt exato do n8n.
    """
    from google.genai import types

    client = _get_gemini_client()

    image_bytes = base64.b64decode(image_base64)

    response = client.models.generate_content(
        model=config.GEMINI_IMAGE_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            IMAGE_ANALYSIS_PROMPT,
        ],
    )

    analysis = response.text or ""
    logger.info("Imagem analisada com sucesso via Gemini (%d chars)", len(analysis))
    return analysis


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

        parsed = _parse_web_detection(result)
        logger.info("Reverse image search concluída (%d chars)", len(parsed))
        return parsed

    except Exception as e:
        logger.warning("Reverse image search falhou: %s", e)
        return "Não foi possível realizar a pesquisa reversa da imagem."


# ──────────────────────── Gemini — Classificação de Mensagem ────────────────────────

CLASSIFIER_PROMPT = """Você é um classificador de mensagens para um bot de verificação de fake news.

Analise a(s) mensagem(ns) do usuário abaixo e decida se o usuário quer VERIFICAR alguma informação/notícia ou se está apenas CONVERSANDO.

Mensagens que devem ser classificadas como VERIFICAR:
- Notícias, afirmações, claims, rumores que o usuário quer saber se são verdadeiros
- Links para artigos/notícias
- Textos encaminhados com informações duvidosas
- Perguntas como "isso é verdade?", "é fake?", "pode verificar isso?"
- Qualquer conteúdo que pareça uma notícia ou informação que pode ser verificada

Mensagens que devem ser classificadas como CONVERSAR:
- Saudações (oi, olá, bom dia, etc.)
- Perguntas sobre o bot (como funciona, o que você faz, etc.)
- Agradecimentos, elogios, reclamações sobre o bot
- Conversas gerais que não contêm informações para verificar
- Perguntas genéricas que não são sobre verificar uma notícia específica

Responda APENAS com uma das duas palavras: VERIFICAR ou CONVERSAR

Mensagem(ns) do usuário:
{messages}"""


async def classify_message(messages: list[str]) -> str:
    """Classifica se as mensagens do usuário são para verificar ou conversar.

    Args:
        messages: Lista de textos das mensagens do usuário.

    Returns:
        'VERIFICAR' ou 'CONVERSAR'
    """
    client = _get_gemini_client()
    messages_text = "\n".join(f"- {msg}" for msg in messages)
    prompt = CLASSIFIER_PROMPT.format(messages=messages_text)

    try:
        response = client.models.generate_content(
            model=config.GEMINI_CLASSIFIER_MODEL,
            contents=prompt,
        )
        result = (response.text or "").strip().upper()
        logger.info("Classificação Gemini: %s", result)

        if "VERIFICAR" in result:
            return "VERIFICAR"
        return "CONVERSAR"
    except Exception as e:
        logger.error("Erro na classificação Gemini: %s", e)
        # Em caso de erro, assume que é para verificar (comportamento seguro)
        return "VERIFICAR"


# ──────────────────────── Gemini — Resposta Conversacional ────────────────────────

CHAT_SYSTEM_PROMPT = """Você é o assistente do TaCertoIssoAI, uma ferramenta de verificação de fake news pelo WhatsApp.

Sobre o TaCertoIssoAI:
Tá Certo Isso AI é uma ferramenta criada por estudantes de Ciência da Computação da Universidade Federal de Goiás (UFG) para combater a desinformação. Ela utiliza inteligência artificial para verificar se notícias, imagens, vídeos e áudios compartilhados no WhatsApp são verdadeiros ou falsos. O objetivo é oferecer uma forma simples e acessível de checar informações, ajudando as pessoas a identificar fake news antes de compartilhá-las. O projeto é sem fins lucrativos e voltado ao interesse público.

Seu objetivo principal é ajudar os usuários a verificarem informações, notícias e conteúdos que possam ser fake news.

Regras:
1. Seja simpático, educado e conciso nas respostas.
2. Se o usuário pedir para você verificar alguma informação que ele mencionou anteriormente na conversa, peça para ele enviar novamente a informação (texto, imagem, vídeo, áudio ou link) em uma nova mensagem separada, pois você só consegue verificar informações enviadas diretamente para análise.
3. Explique que para verificar algo, o usuário deve enviar o conteúdo diretamente (texto, imagem, vídeo, link ou áudio).
4. Não invente informações sobre notícias ou fatos. Você não é um verificador de fatos — apenas direcione o usuário a enviar o conteúdo para verificação.
5. Responda em português brasileiro.
6. Não use formatação markdown (como **, ##, etc.) pois a mensagem será enviada via WhatsApp."""


async def generate_chat_response(
    user_messages: list[str],
    chat_history: list[dict],
) -> str:
    """Gera uma resposta conversacional usando o Gemini.

    Args:
        user_messages: Mensagens atuais do usuário a responder.
        chat_history: Histórico de chat dos últimos 5 minutos (lista de dicts com 'role' e 'content').

    Returns:
        Texto da resposta do bot.
    """
    client = _get_gemini_client()

    # Montar o contexto com histórico
    context_parts = [CHAT_SYSTEM_PROMPT + "\n\n"]

    if chat_history:
        context_parts.append("Histórico recente da conversa:\n")
        for entry in chat_history:
            role = "Usuário" if entry["role"] == "user" else "Bot"
            context_parts.append(f"{role}: {entry['content']}\n")
        context_parts.append("\n")

    context_parts.append("Mensagem(ns) atual(is) do usuário:\n")
    for msg in user_messages:
        context_parts.append(f"- {msg}\n")
    context_parts.append("\nSua resposta:")

    prompt = "".join(context_parts)

    try:
        response = client.models.generate_content(
            model=config.GEMINI_CHAT_MODEL,
            contents=prompt,
        )
        text = (response.text or "").strip()
        logger.info("Resposta conversacional gerada (%d chars)", len(text))
        return text
    except Exception as e:
        logger.error("Erro ao gerar resposta conversacional: %s", e)
        return (
            "Desculpe, tive um problema ao processar sua mensagem. "
            "Você pode enviar o conteúdo que deseja verificar (texto, imagem, vídeo, link ou áudio)."
        )
