# Tá Certo Isso AI? - WhatsApp Integration (Python)

<p align="center">
  <a href="https://tacertoissoai.com.br/">
    <img alt="Acesse o site" src="https://img.shields.io/badge/ACESSAR%20O%20SITE-tacertoissoai.com.br-22C55E?style=for-the-badge" />
  </a>
  <a href="https://github.com/TaCertoIssoAI/whatsapp-automation">
    <img alt="GitHub" src="https://img.shields.io/badge/GITHUB-whatsapp--automation-181717?style=for-the-badge&logo=github" />
  </a>
</p>

> **Implementação Python do bot de verificação de fake news para WhatsApp usando FastAPI e LangGraph**

Este repositório contém a **implementação Python** do bot **Tá Certo Isso AI?**, replicando fielmente a lógica originalmente construída em [n8n](https://github.com/TaCertoIssoAI/n8n-workflows). Utilizamos **LangGraph** para orquestração de workflows, **FastAPI** para o webhook, e integrações com **OpenAI**, **Google Gemini** e **Evolution API**.

---

## 📖 Sobre o Projeto

**Tá Certo Isso AI?** é um bot de WhatsApp que combate a desinformação usando inteligência artificial multimodal e fact-checking. Qualquer pessoa pode verificar se uma mensagem é verdadeira, enganosa ou fora de contexto **sem sair do WhatsApp**.

Esta implementação Python oferece:
- ✅ **100% compatível** com o workflow n8n original
- 🚀 **Performance otimizada** com FastAPI e asyncio
- 🔧 **Fácil manutenção** com código modular e tipado
- 📦 **Deploy simplificado** com ambiente virtual Python

---

## 🏗️ Arquitetura

```
┌─────────────────┐
│   WhatsApp      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Evolution API  │ ◄─── Webhook: /messages-upsert
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           FastAPI (main.py)                     │
│  ┌───────────────────────────────────────────┐  │
│  │        LangGraph Workflow (graph.py)      │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  Data Extraction → Filters →        │  │  │
│  │  │  Routing (Switch6/9) →              │  │  │
│  │  │  Media Processing → Fact-check →    │  │  │
│  │  │  Response Sender                    │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│  AI Services & External APIs      │
│  • OpenAI (Whisper, GPT-4o, TTS)  │
│  • Google Gemini (Video Analysis) │
│  • Google Vision (Reverse Search) │
│  • Fact-check API (Custom)        │
└────────────────────────────────────┘
```

---

## 🔥 Funcionalidades

### Processamento Multimodal
- **📝 Texto**: Análise direta via fact-checking API
- **🎤 Áudio**: Transcrição com OpenAI Whisper → Fact-check → Resposta em áudio (TTS)
- **🖼️ Imagem**: Análise com GPT-4o-mini + Busca reversa (Google Vision) → Fact-check
- **🎥 Vídeo**: Análise com Gemini 2.5 Flash (até 2 minutos) → Fact-check

### Comportamento Inteligente
- ✅ Detecta mensagens diretas vs. menções em grupos
- ✅ Suporte a mensagens citadas (quoted messages)
- ✅ Indicadores de presença ("digitando", "gravando")
- ✅ Respostas contextualizadas com links e fontes
- ✅ Saudações personalizadas

---

## 🛠️ Tecnologias

| Categoria | Tecnologia | Uso |
|-----------|-----------|-----|
| **Backend** | [FastAPI](https://fastapi.tiangolo.com/) | Webhook HTTP para Evolution API |
| **Orquestração** | [LangGraph](https://langchain-ai.github.io/langgraph/) | Gerenciamento de workflow e estado |
| **IA - Transcrição** | [OpenAI Whisper](https://openai.com/research/whisper) | Conversão de áudio em texto |
| **IA - Análise de Texto** | [OpenAI GPT-4o-mini](https://openai.com/gpt-4) | Análise de imagens e raciocínio |
| **IA - Análise de Vídeo** | [Google Gemini 2.5 Flash](https://ai.google.dev/) | Processamento multimodal de vídeos |
| **IA - TTS** | [OpenAI TTS](https://platform.openai.com/docs/guides/text-to-speech) | Geração de áudio (voz "onyx") |
| **Visão Computacional** | [Google Cloud Vision API](https://cloud.google.com/vision) | Busca reversa de imagens |
| **WhatsApp Gateway** | [Evolution API](https://evolution-api.com/) | Integração com WhatsApp |
| **Fact-checking** | API Proprietária | Verificação de veracidade |

---

## 📦 Instalação

### Pré-requisitos
- Python 3.12+
- Conta Evolution API configurada
- API keys: OpenAI, Google Gemini, Google Cloud Vision

### 1. Clonar o repositório
```bash
git clone https://github.com/TaCertoIssoAI/whatsapp-automation.git
cd whatsapp-automation
```

### 2. Criar ambiente virtual
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# ou
venv\Scripts\activate  # Windows
```

### 3. Instalar dependências
```bash
pip install -r requirements.txt
```

### 4. Configurar variáveis de ambiente
Crie um arquivo `.env` na raiz do projeto:

```env
# Evolution API
EVOLUTION_API_URL=https://sua-evolution-api.com
EVOLUTION_API_KEY=sua_api_key

# OpenAI
OPENAI_API_KEY=sk-...

# Google APIs
GOOGLE_GEMINI_API_KEY=...
GOOGLE_CLOUD_API_KEY=...

# Fact-check API
FACT_CHECK_API_URL=https://sua-api-factcheck.com

# Bot Config
BOT_MENTION_JID=5511999999999@s.whatsapp.net

# Server
WEBHOOK_PORT=5000
```

> [!WARNING]
> **Nunca** versione o arquivo `.env` com credenciais reais! Use `.env.example` como template.

---

## 🚀 Uso

### Iniciar o servidor
```bash
source venv/bin/activate
python main.py
```

O servidor iniciará em `http://localhost:5000` com o endpoint webhook em `/messages-upsert`.

### Expor localmente (desenvolvimento)
Para testar localmente com a Evolution API, use **ngrok**:

```bash
ngrok http 5000
```

Configure o webhook na Evolution API com a URL fornecida:
```
https://your-ngrok-url.ngrok.io/messages-upsert
```

### Deploy em produção
Consulte o arquivo [`DEPLOY.md`](DEPLOY.md) para instruções completas de deploy em servidores ou plataformas cloud.

---

## 📁 Estrutura do Projeto

```
whatsapp-integration/
├── main.py                 # FastAPI app & webhook endpoint
├── graph.py                # LangGraph workflow definition
├── state.py                # WorkflowState TypedDict
├── config.py               # Environment variables loader
├── requirements.txt        # Python dependencies
├── nodes/                  # Workflow nodes (modular)
│   ├── data_extractor.py   # Parse webhook payload
│   ├── filters.py          # Filters (group, mention, greeting)
│   ├── router.py           # Switch6 & Switch9 routing
│   ├── media_processor.py  # Audio/Image/Video/Text processing
│   ├── ai_services.py      # OpenAI & Gemini integrations
│   ├── fact_checker.py     # Fact-check API client
│   ├── evolution_api.py    # Evolution API client
│   └── response_sender.py  # Send text/audio responses
├── n8n/                    # Original n8n workflow JSONs (reference)
│   ├── n8n-workflow.json
│   ├── analyze-image.json
│   ├── reverse-search.json
│   ├── digitando.json
│   └── gravando.json
└── DEPLOY.md               # Deployment guide
```

---

## 🧪 Verificação de Compatibilidade

A implementação Python foi **auditada node-by-node** contra o workflow n8n original. Todos os **30+ pontos de verificação** foram confirmados:

- ✅ Data extraction paths
- ✅ Evolution API endpoints
- ✅ Switch6 & Switch9 routing
- ✅ Status messages (textos idênticos)
- ✅ Fact-check payloads
- ✅ AI prompts (GPT-4o-mini, Gemini)
- ✅ Caption handling logic
- ✅ Presence indicators (fire-and-forget)
- ✅ Audio response flow (TTS)

Consulte o [walkthrough de verificação](https://github.com/TaCertoIssoAI/whatsapp-automation/blob/main/docs/walkthrough.md) para detalhes completos.

---

## 🤝 Contribuindo

Contribuições são bem-vindas! Por favor:

1. Fork o projeto
2. Crie uma branch para sua feature (`git checkout -b feature/MinhaFeature`)
3. Commit suas mudanças (`git commit -m 'feat: adiciona nova feature'`)
4. Push para a branch (`git push origin feature/MinhaFeature`)
5. Abra um Pull Request

---

## 📄 Licença

Este projeto faz parte da iniciativa **Tá Certo Isso AI?** e está disponível sob licença a definir.

---

## 🔗 Links Relacionados

- 🌐 **Website**: [tacertoissoai.com.br](https://tacertoissoai.com.br/)
- 📝 **Notion (Documentação)**: [Anotações do Projeto](https://proximal-zoo-82f.notion.site/tacertoissoai)
- 🎥 **Vídeo de Apresentação**: [YouTube](https://youtu.be/Tr7s_vxDnKk)
- 🔄 **N8N Workflows**: [GitHub - n8n-workflows](https://github.com/TaCertoIssoAI/n8n-workflows)

---

## 📞 Contato

Para dúvidas, sugestões ou parcerias, entre em contato através do site [tacertoissoai.com.br](https://tacertoissoai.com.br/).

---

<p align="center">
  Feito com ❤️ pelo time <strong>Tá Certo Isso AI?</strong>
</p>
