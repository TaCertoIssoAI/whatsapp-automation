"""Configurações do projeto carregadas de variáveis de ambiente."""

import os
from dotenv import load_dotenv

load_dotenv()

# Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Google Gemini
GOOGLE_GEMINI_API_KEY = os.getenv("GOOGLE_GEMINI_API_KEY", "")

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
