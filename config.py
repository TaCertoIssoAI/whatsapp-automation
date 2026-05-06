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

# ──────────────────────── Vertex AI (Gemini) ────────────────────────
# Auth is via Application Default Credentials (ADC). For local/dev, set:
#   GOOGLE_APPLICATION_CREDENTIALS=./credentials.json
PROJECT_ID = os.getenv("PROJECT_ID", os.getenv("GOOGLE_CLOUD_PROJECT", ""))
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", os.getenv("GOOGLE_CLOUD_LOCATION", ""))

# Modelos Gemini (editáveis via .env) — Vertex defaults
GEMINI_TRANSCRIPTION_MODEL = os.getenv("GEMINI_TRANSCRIPTION_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-lite")
GEMINI_VIDEO_MODEL = os.getenv("GEMINI_VIDEO_MODEL", "gemini-2.5-flash")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-tts")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")

# ──────────────────────── Redis ────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ──────────────────────── Google Cloud Vision API ────────────────────────
GOOGLE_CLOUD_API_KEY = os.getenv("GOOGLE_CLOUD_API_KEY", "")

# ──────────────────────── Deep-fake Detection Service ────────────────────────
DEEP_FAKE_SERVICE_URL = os.getenv("DEEP_FAKE_SERVICE_URL", "")
DEEP_FAKE_VIDEO = os.getenv("DEEP_FAKE_VIDEO", "true").lower() == "true"
DEEP_FAKE_IMAGE = os.getenv("DEEP_FAKE_IMAGE", "true").lower() == "true"
DEEP_FAKE_AUDIO = os.getenv("DEEP_FAKE_AUDIO", "true").lower() == "true"

# ──────────────────────── Fact-checking API ────────────────────────
FACT_CHECK_API_URL = os.getenv(
    "FACT_CHECK_API_URL",
    "https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app",
)

# ──────────────────────── Bot (grupo — desativado por enquanto) ────────────────────────
# BOT_MENTION_JID = os.getenv("BOT_MENTION_JID", "117558187450509@lid")

# ──────────────────────── Firebase / Rate Limiting ────────────────────────
# Caminho para o arquivo JSON da Service Account do Firebase.
# No servidor (Docker/EasyPanel): montado via volume em /app/firebase-credentials.json
# Local: caminho relativo ou absoluto (ex: ./firebase-credentials.json)
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "")

HASH_SALT = os.getenv("HASH_SALT", "")
DAILY_MESSAGE_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", "5"))

# ──────────────────────── Servidor ────────────────────────
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
