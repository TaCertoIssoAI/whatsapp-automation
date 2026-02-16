"""Configurações do projeto carregadas de variáveis de ambiente."""

import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────── WhatsApp Business Cloud API ────────────────────────
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

WHATSAPP_API_BASE_URL = (
    f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}"
)

# ──────────────────────── Redis ────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ──────────────────────── Google Gemini ────────────────────────
GOOGLE_GEMINI_API_KEY = os.getenv("GOOGLE_GEMINI_API_KEY", "")

# Modelos Gemini (editáveis via .env)
GEMINI_TRANSCRIPTION_MODEL = os.getenv("GEMINI_TRANSCRIPTION_MODEL", "gemini-3-flash-preview")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-flash-preview")
GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "gemini-3-flash-preview")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")
GEMINI_CLASSIFIER_MODEL = os.getenv("GEMINI_CLASSIFIER_MODEL", "gemini-3-flash-preview")
GEMINI_CHAT_MODEL = os.getenv("GEMINI_CHAT_MODEL", "gemini-3-flash-preview")

# ──────────────────────── Google Cloud Vision API ────────────────────────
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")

# ──────────────────────── Fact-checking API ────────────────────────
FACT_CHECK_API_URL = os.getenv(
    "FACT_CHECK_API_URL",
    "https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app",
)

# ──────────────────────── Bot (grupo — desativado por enquanto) ────────────────────────
# BOT_MENTION_JID = os.getenv("BOT_MENTION_JID", "117558187450509@lid")

# ──────────────────────── Servidor ────────────────────────
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))

# ──────────────────────── Debounce ────────────────────────
MESSAGE_DEBOUNCE_SECONDS = float(os.getenv("MESSAGE_DEBOUNCE_SECONDS", "1.0"))

# ──────────────────────── Histórico de chat ────────────────────────
CHAT_HISTORY_TTL_SECONDS = int(os.getenv("CHAT_HISTORY_TTL_SECONDS", "300"))  # 5 minutos
