# Tá Certo Isso AI? - WhatsApp Integration v5.0.0

<p align="center">
  <a href="https://tacertoissoai.com.br/">
    <img alt="Acesse o site" src="https://img.shields.io/badge/ACESSAR%20O%20SITE-tacertoissoai.com.br-22C55E?style=for-the-badge" />
  </a>
  <a href="https://github.com/TaCertoIssoAI/whatsapp-automation">
    <img alt="GitHub" src="https://img.shields.io/badge/GITHUB-whatsapp--automation-181717?style=for-the-badge&logo=github" />
  </a>
</p>

> **Bot de verificação de fake news para WhatsApp usando WhatsApp Business Cloud API (Oficial da Meta)**

Este repositório contém o bot **Tá Certo Isso AI?** implementado em Python com **FastAPI**, **LangGraph** e **Google Gemini**, usando a **API Oficial do WhatsApp** da Meta.

## 🆕 Novidades da v5.0.0

### ⚡ Prioridade ABSOLUTA ao Webhook da Meta
- **Middleware de interceptação**: Processa POST /webhook ANTES de qualquer outro código
- **Fire-and-forget**: Enfileira em background task (não espera)
- **Resposta < 1ms**: Body pré-serializado, sem parse JSON, sem HMAC no hot path
- **Garantia**: Meta **NUNCA** espera, mesmo com servidor sob alta carga

### 🎯 Arquitetura "ACK-first, process-later"
- **Camada 1**: Middleware intercepta e retorna 200 OK instantaneamente
- **Camada 2**: Background task enfileira (payload, HMAC) sem bloquear
- **Camada 3**: Workers processam HMAC, JSON, dedup e LangGraph **depois**
- **Resultado**: 0% timeouts, 0% exponential backoff da Meta

### 🔧 Tunning para VPS 1-core
- **3 queue workers** (ao invés de 5)
- **8 threads** no pool (ao invés de 32)
- **10 max concurrent** (ao invés de 30)
- **4 concurrent Gemini calls** (ao invés de 10)
- Fila de **500 itens** (ao invés de 2000)

### 🛑 Shutdown Robusto
- **Lifespan context manager** (padrão moderno FastAPI)
- **Timeouts em cada etapa** do shutdown (nunca trava)
- **Flag `_shutting_down`** impede enfileiramentos durante shutdown

📚 **[Leia a documentação completa da arquitetura de prioridade](docs/WEBHOOK_PRIORITY.md)**

---

## 📖 Sobre o Projeto

**Tá Certo Isso AI?** é um bot de WhatsApp que combate a desinformação usando inteligência artificial multimodal e fact-checking. Qualquer pessoa pode verificar se uma mensagem é verdadeira, enganosa ou fora de contexto **sem sair do WhatsApp**.

Esta implementação oferece:
- ✅ **API Oficial do WhatsApp** (Cloud API da Meta)
- 🚀 **Performance otimizada** com FastAPI e asyncio
- ⚡ **Webhook instantâneo** (< 1ms de resposta para a Meta)
- 🔧 **Fácil manutenção** com código modular e tipado
- 📦 **Deploy simplificado** em VPS 1-core

---

## 🏗️ Arquitetura

```
┌─────────────────┐
│   WhatsApp      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Meta Cloud    │ ◄─── Webhook: POST /webhook (200 OK < 1ms)
│      API        │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│           FastAPI (main.py)                     │
│  ┌───────────────────────────────────────────┐  │
│  │    asyncio.Queue (ACK-first, process-later)│  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  3 Queue Workers:                   │  │  │
│  │  │   - HMAC validation (off hot path)  │  │  │
│  │  │   - JSON parse                      │  │  │
│  │  │   - Deduplication                   │  │  │
│  │  │   - Dispatch to LangGraph           │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  │                                            │  │
│  │        LangGraph Workflow (graph.py)      │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  Data Extraction → Filters →        │  │  │
│  │  │  Routing (tipo de mensagem) →       │  │  │
│  │  │  Media Processing → Fact-check →    │  │  │
│  │  │  Response Sender                    │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
         │
         ▼
┌────────────────────────────────────┐
│  AI Services & External APIs       │
│  • Google Gemini (Áudio, Imagem,   │
│    Vídeo, TTS)                     │
│  • Google Vision (Reverse Search)  │
│  • Fact-check API (Custom)         │
└────────────────────────────────────┘
```

---

## 🔥 Funcionalidades

### Processamento Multimodal
- **📝 Texto**: Análise direta via fact-checking API
- **🎤 Áudio**: Transcrição com Google Gemini → Fact-check → Resposta em áudio (Gemini TTS)
- **🖼️ Imagem**: Análise com Google Gemini + Busca reversa (Google Vision) → Fact-check
- **🎥 Vídeo**: Análise com Google Gemini (até 2 minutos) → Fact-check

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
| **IA - Transcrição** | [Google Gemini](https://ai.google.dev/) | Conversão de áudio em texto |
| **IA - Análise de Imagem** | [Google Gemini](https://ai.google.dev/) | Análise de imagens para fact-checking |
| **IA - Análise de Vídeo** | [Google Gemini](https://ai.google.dev/) | Processamento multimodal de vídeos |
| **IA - TTS** | [Google Gemini TTS](https://ai.google.dev/) | Geração de áudio (text-to-speech) |
| **Visão Computacional** | [Google Cloud Vision API](https://cloud.google.com/vision) | Busca reversa de imagens |
| **WhatsApp Gateway** | [Evolution API](https://evolution-api.com/) | Integração com WhatsApp |
| **Fact-checking** | API Proprietária | Verificação de veracidade |

---

## 📦 Instalação

### Pré-requisitos
- Python 3.12+
- Conta Evolution API configurada
- API keys: Google Gemini, Google Cloud Vision

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

# Google Gemini (transcrição, imagem, vídeo, TTS)
GOOGLE_GEMINI_API_KEY=...

# Google Cloud Vision (reverse image search)
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
│   ├── ai_services.py      # Google Gemini AI integrations
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
