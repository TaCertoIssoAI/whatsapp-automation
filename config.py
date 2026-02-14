"""Configurações do projeto carregadas de variáveis de ambiente."""

import os
from dotenv import load_dotenv

load_dotenv()

# Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

# Google Gemini
GOOGLE_GEMINI_API_KEY = os.getenv("GOOGLE_GEMINI_API_KEY", "")

# Modelos Gemini (editáveis via .env)
GEMINI_TRANSCRIPTION_MODEL = os.getenv("GEMINI_TRANSCRIPTION_MODEL", "gemini-3-flash-preview")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-flash-preview")
GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "gemini-3-flash-preview")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")

# Google Cloud (Vision API para reverse image search)
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")

# Fact-checking API
FACT_CHECK_API_URL = os.getenv(
    "FACT_CHECK_API_URL",
    "https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app",
)

# Bot
BOT_MENTION_JID = os.getenv("BOT_MENTION_JID", "117558187450509@lid")

# Server
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
