# 📋 Pesquisa: Migração da Evolution API para a API Oficial do WhatsApp Business

> **Data da pesquisa:** Julho 2025  
> **Status:** Documento de análise — nenhuma alteração de código foi realizada  
> **Pré-requisito:** BM (Business Manager) verificada no Facebook ✅

---

## 📑 Índice

1. [Resumo Executivo](#1-resumo-executivo)
2. [Arquitetura Atual (Evolution API)](#2-arquitetura-atual-evolution-api)
3. [API Oficial do WhatsApp Business (Cloud API)](#3-api-oficial-do-whatsapp-business-cloud-api)
4. [Comparação Detalhada: Feature por Feature](#4-comparação-detalhada-feature-por-feature)
5. [Mudanças no Webhook (Recebimento de Mensagens)](#5-mudanças-no-webhook-recebimento-de-mensagens)
6. [Mudanças no Envio de Mensagens](#6-mudanças-no-envio-de-mensagens)
7. [Mudanças no Download/Upload de Mídia](#7-mudanças-no-downloadupload-de-mídia)
8. [Grupos — Análise Detalhada](#8-grupos--análise-detalhada)
9. [Modelo de Preços](#9-modelo-de-preços)
10. [Limitações e Pontos de Atenção](#10-limitações-e-pontos-de-atenção)
11. [Arquivos que Precisam ser Modificados](#11-arquivos-que-precisam-ser-modificados)
12. [Plano de Migração Sugerido](#12-plano-de-migração-sugerido)
13. [Configuração Inicial (Setup)](#13-configuração-inicial-setup)
14. [Conclusão](#14-conclusão)

---

## 1. Resumo Executivo

### ✅ É possível migrar?
**SIM**, a grande maioria das funcionalidades atuais pode ser replicada com a API oficial. Porém, existem diferenças significativas na forma como as operações são executadas e **uma limitação crítica relacionada a Grupos**.

### ⚡ Principais diferenças:
| Aspecto | Evolution API | API Oficial |
|---------|--------------|-------------|
| Tipo | API não-oficial (wrapper do WhatsApp Web) | API oficial da Meta/Facebook |
| Hospedagem | Self-hosted (sua infra) | Cloud API (Meta hospeda) ou On-Premises |
| Autenticação | `apiKey` header | Bearer Token (OAuth / System User Token) |
| Custo da API | Grátis (open-source) | Grátis (Cloud API), cobra por mensagem template |
| Estabilidade | Sujeita a bloqueios do WhatsApp | Oficial, sem risco de bloqueio |
| Webhook | Formato proprietário da Evolution | Formato Graph API da Meta |
| Mídia | Base64 direto | Upload/Download via Media API (binário) |
| Grupos | Funciona como WhatsApp Web normal | API de Grupos nova (documentação ainda sendo publicada) |
| Janela de atendimento | Sem restrição | 24h após última mensagem do usuário |

### 🔴 Limitação Crítica Identificada:
A **API de Grupos do WhatsApp Business** é uma funcionalidade muito recente e a documentação ainda está sendo publicada pela Meta. As páginas de documentação sobre criação/gerenciamento de grupos e envio/recebimento de mensagens em grupos retornaram "página não disponível" durante esta pesquisa. O overview da API menciona suporte a grupos, mas os detalhes de implementação ainda não estão completamente documentados.

---

## 2. Arquitetura Atual (Evolution API)

### 2.1 Stack Tecnológico
- **Python + FastAPI** — Servidor web recebendo webhooks
- **LangGraph** — Motor de workflow (StateGraph)
- **Evolution API** — Integração com WhatsApp (self-hosted)
- **Google Gemini** — IA para transcrição, TTS, análise de imagem/vídeo
- **Google Cloud Vision API** — Busca reversa de imagens
- **httpx** — Cliente HTTP assíncrono
- **pydub** — Conversão de áudio

### 2.2 Funções da Evolution API Utilizadas

O arquivo `nodes/evolution_api.py` contém todas as chamadas à Evolution API:

| Função | Endpoint Evolution API | Descrição |
|--------|----------------------|-----------|
| `send_text()` | `POST /message/sendText/{instance}` | Envia mensagem de texto (com quote opcional via `options.quoted.key.id`) |
| `send_audio()` | `POST /message/sendWhatsAppAudio/{instance}` | Envia áudio como base64 |
| `mark_as_read()` | `POST /chat/markMessageAsRead/{instance}` | Marca mensagem como lida |
| `get_media_base64()` | `POST /chat/getBase64FromMediaMessage/{instance}` | Obtém mídia da mensagem em base64 |
| `get_base64_from_quoted_message()` | Reutiliza endpoint acima | Obtém mídia da mensagem citada (quoted) em base64 |
| `send_presence()` | `PUT /chat/sendPresence/{instance}` | Envia status "digitando"/"gravando" |

### 2.3 Fluxo de Dados Atual do Webhook

```
Evolution API envia POST /messages-upsert com:
{
  "instance": "nome_instancia",
  "event": "messages-upsert",
  "data": {
    "key": {
      "remoteJid": "5511999999999@s.whatsapp.net",
      "fromMe": false,
      "id": "ABCDEF123456"
    },
    "pushName": "Nome do Contato",
    "message": {
      "conversation": "texto da mensagem",
      // ou "audioMessage": {...},
      // ou "imageMessage": {...},
      // etc.
    },
    "messageType": "conversation",
    "contextInfo": {
      "stanzaId": "ID_MSG_CITADA",
      "mentionedJid": ["bot_jid@lid"],
      "quotedMessage": { ... }
    }
  }
}
```

### 2.4 Tipos de Mensagem Processados
- **Texto** (`conversation`, `extendedTextMessage`)
- **Áudio** (`audioMessage`)
- **Imagem** (`imageMessage`, `stickerMessage` → tratado como imagem)
- **Vídeo** (`videoMessage`) — limite de 2 minutos
- **Documento** (`documentMessage`) — retorna "não suportado"

### 2.5 Funcionalidades Especiais
- **Grupos**: Detecta se `remoteJid` termina com `@g.us`
- **Menção do bot**: Verifica se `contextInfo.mentionedJid[]` contém `BOT_MENTION_JID`
- **Resposta a mensagem citada**: Verifica `contextInfo.stanzaId` e processa a mídia citada
- **Saudações**: Detecta "oi", "olá", "bom dia", etc. e responde com mensagem padrão

---

## 3. API Oficial do WhatsApp Business (Cloud API)

### 3.1 Base URL
```
https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages
```

### 3.2 Autenticação
```
Authorization: Bearer {ACCESS_TOKEN}
```

O token pode ser:
- **Token temporário** (expira em ~24h) — para testes
- **System User Token** (permanente) — para produção
  - Criado no Business Manager → System Users → Generate Token
  - Necessita permissão `whatsapp_business_messaging`

### 3.3 Variáveis de Ambiente Necessárias (novas)
```env
# Substituem EVOLUTION_API_URL e EVOLUTION_API_KEY
WHATSAPP_ACCESS_TOKEN=       # System User Token permanente
WHATSAPP_PHONE_NUMBER_ID=    # ID do número de telefone no Meta
WHATSAPP_BUSINESS_ACCOUNT_ID= # ID da conta Business
WHATSAPP_VERIFY_TOKEN=       # Token para verificação de webhook (você define)
WHATSAPP_APP_SECRET=         # App Secret para validar X-Hub-Signature-256
```

### 3.4 Throughput e Rate Limits
- **80 mensagens/segundo** de throughput (Cloud API)
- **1 mensagem a cada 6 segundos** por par business↔user (não oficial, mas observado)
- **Limite de 256KB** para payloads de texto
- **Mídia**: Áudio max 16MB, Vídeo max 16MB, Imagem max 5MB, Documentos max 100MB

---

## 4. Comparação Detalhada: Feature por Feature

### 4.1 Enviar Mensagem de Texto

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Endpoint** | `POST /message/sendText/{instance}` | `POST /{PHONE_NUMBER_ID}/messages` |
| **Body** | `{ "number": "...", "text": "...", "options": { "quoted": { "key": { "id": "..." } } } }` | `{ "messaging_product": "whatsapp", "to": "...", "type": "text", "text": { "body": "..." }, "context": { "message_id": "wamid...." } }` |
| **Quote/Reply** | `options.quoted.key.id` | `context.message_id` |
| **Status** | ✅ Equivalente | ✅ Equivalente |

**Mudanças necessárias:**
- Trocar o campo `number` por `to`
- Trocar `options.quoted.key.id` por `context.message_id`
- Adicionar `"messaging_product": "whatsapp"` em todo request
- O ID de mensagem muda de formato: Evolution usa IDs curtos, API oficial usa WAMIDs (`wamid.xxx`)

---

### 4.2 Enviar Áudio (Voice Message)

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Formato** | Envia base64 diretamente | Precisa: 1) Upload da mídia → obtém `media_id`, 2) Envia mensagem com `media_id` |
| **Codec** | Aceita MP3/qualquer formato | Voice messages PRECISAM ser `.ogg` com codec **Opus** |
| **Endpoint envio** | `POST /message/sendWhatsAppAudio/{instance}` | `POST /{PHONE_NUMBER_ID}/messages` com `type: "audio"` |
| **Endpoint upload** | N/A (base64 direto) | `POST /{PHONE_NUMBER_ID}/media` (multipart/form-data) |

**Mudanças necessárias:**
- O `ai_services.py` já converte PCM → MP3 via pydub. Precisará converter para **OGG/Opus** ao invés de MP3
- Implementar upload de mídia como etapa intermediária antes do envio
- O áudio precisa ser enviado como arquivo binário, não base64

**Exemplo do fluxo novo:**
```python
# 1. Upload da mídia
response = POST /{PHONE_NUMBER_ID}/media
  Content-Type: multipart/form-data
  file: (arquivo .ogg binário)
  type: "audio/ogg"
  messaging_product: "whatsapp"
→ Retorna: { "id": "MEDIA_ID" }

# 2. Enviar mensagem de áudio
response = POST /{PHONE_NUMBER_ID}/messages
  {
    "messaging_product": "whatsapp",
    "to": "5511999999999",
    "type": "audio",
    "audio": { "id": "MEDIA_ID" }
  }
```

**Formatos de áudio suportados pela API oficial:**
- AAC, AMR, MP3, MP4 Audio, OGG (somente com codec Opus para voice messages)
- Tamanho máximo: 16MB

---

### 4.3 Marcar como Lida (Mark as Read)

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Endpoint** | `POST /chat/markMessageAsRead/{instance}` | `POST /{PHONE_NUMBER_ID}/messages` |
| **Body** | `{ "readMessages": [{ "remoteJid": "...", "id": "..." }] }` | `{ "messaging_product": "whatsapp", "status": "read", "message_id": "wamid.xxx" }` |

**Mudanças necessárias:**
- Usar o mesmo endpoint de mensagens, mas com `status: "read"`
- Usar o `message_id` (WAMID) ao invés do ID da Evolution

---

### 4.4 Status de Presença (Typing Indicator)

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Endpoint** | `PUT /chat/sendPresence/{instance}` | `POST /{PHONE_NUMBER_ID}/messages` |
| **Tipos** | `composing` (digitando), `recording` (gravando) | Apenas `typing_indicator: { type: "text" }` |
| **Duração** | Manual (envia start/stop) | **Auto-dismiss após 25 segundos** |
| **Body** | `{ "number": "...", "presence": "composing" }` | `{ "messaging_product": "whatsapp", "to": "...", "typing_indicator": { type: "text" } }` |

**⚠️ Limitação:** A API oficial **NÃO tem indicador "gravando áudio"** — apenas "digitando". Isso é uma diferença estética menor.

**⚠️ Comportamento diferente:** O typing indicator da API oficial **desaparece automaticamente após 25 segundos**. Se o processamento demorar mais, precisa enviar novamente. Na Evolution API, o `composing` persiste até ser explicitamente parado ou até enviar uma mensagem.

---

### 4.5 Download de Mídia (Receber áudio/imagem/vídeo do usuário)

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Mecanismo** | Chamada única retorna base64 | Processo de 2 etapas (ou 1 com URL direto) |
| **Endpoint** | `POST /chat/getBase64FromMediaMessage/{instance}` | 1) `GET /{MEDIA_ID}` → obtém URL, 2) `GET {URL}` → download binário |
| **Retorno** | String base64 | Dados binários (bytes) |

**Como funciona na API oficial:**

O webhook de mensagem recebida inclui:
```json
{
  "image": {
    "id": "MEDIA_ID",
    "mime_type": "image/jpeg",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/whatsapp_business/...",
    "caption": "legenda opcional"
  }
}
```

**Opção 1 — Usar `url` diretamente (mais novo):**
```python
# A partir de versões recentes, o webhook já inclui a URL
response = httpx.get(
    message["image"]["url"],
    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
)
binary_data = response.content
base64_data = base64.b64encode(binary_data).decode()
```

**Opção 2 — Usar `media_id` (método clássico):**
```python
# Etapa 1: Obter URL de download
response = httpx.get(
    f"https://graph.facebook.com/v22.0/{media_id}",
    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
)
download_url = response.json()["url"]

# Etapa 2: Download do binário
response = httpx.get(
    download_url,
    headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
)
binary_data = response.content
base64_data = base64.b64encode(binary_data).decode()
```

**Mudanças necessárias:**
- `get_media_base64()` precisa ser reescrita para usar o fluxo de 2 etapas (ou URL direto)
- O retorno será binário que precisa ser convertido para base64 (para manter compatibilidade com o restante do código)
- `get_base64_from_quoted_message()` precisa de lógica completamente diferente — na API oficial, o webhook para mensagens citadas funciona diferentemente

---

### 4.6 Mensagem Citada (Quoted Message) — Obter Mídia

**Este é um ponto que exige atenção especial.**

Na **Evolution API**, a mensagem citada vem completa no webhook:
```json
{
  "contextInfo": {
    "stanzaId": "ID_DA_MSG_CITADA",
    "quotedMessage": {
      "imageMessage": { ... },
      "audioMessage": { ... }
    }
  }
}
```
E pode-se chamar `getBase64FromMediaMessage` passando o `stanzaId` para obter a mídia.

Na **API oficial**, o webhook de uma mensagem que cita outra inclui:
```json
{
  "context": {
    "from": "SENDER_PHONE",
    "id": "wamid.QUOTED_MSG_ID",
    "referred_product": { ... }  // apenas para product messages
  }
}
```

**⚠️ Limitação potencial:** A API oficial NÃO inclui o conteúdo completo da mensagem citada no webhook. Ela inclui apenas o `id` da mensagem citada. Para obter a mídia da mensagem citada, você precisará:

1. **Armazenar os `media_id` das mensagens recebidas** — quando uma mensagem com mídia chega, salvar o `media_id` associado ao `message_id` (em memória, Redis, banco de dados, etc.)
2. **Quando uma mensagem citar outra**, usar o `context.id` para buscar o `media_id` armazenado e então fazer o download

**Isso é uma mudança arquitetural significativa** — atualmente o sistema é stateless (não armazena nada entre requests). Com a API oficial, precisará de algum tipo de cache/storage para mídias citadas.

**Alternativas:**
- Redis com TTL (ex: 24h, coincidindo com a janela de atendimento)
- Dicionário em memória (simples, mas perde dados ao reiniciar)
- SQLite local
- Salvar arquivos de mídia temporariamente no disco

---

### 4.7 Resposta Contextual (Reply/Quote)

| | Evolution API | API Oficial |
|-|--------------|-------------|
| **Como fazer** | `options.quoted.key.id` no payload | `context.message_id` no payload |
| **Status** | ✅ Funciona | ✅ Funciona |

Exemplo API oficial:
```json
{
  "messaging_product": "whatsapp",
  "to": "5511999999999",
  "type": "text",
  "context": {
    "message_id": "wamid.HBgMNTUxMTk5OTk5OTkVAgASGCA1..."
  },
  "text": {
    "body": "Esta é uma resposta à sua mensagem"
  }
}
```

---

## 5. Mudanças no Webhook (Recebimento de Mensagens)

### 5.1 Verificação de Webhook (NOVO — Não existe na Evolution API)

A API oficial exige um **endpoint GET** para verificação do webhook. Isso precisa ser implementado no `main.py`:

```python
# NOVO endpoint necessário
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    
    raise HTTPException(status_code=403, detail="Verification failed")
```

### 5.2 Validação de Assinatura (NOVO — Recomendado)

A API oficial envia um header `X-Hub-Signature-256` com assinatura SHA256 do payload usando o App Secret:

```python
import hmac
import hashlib

def validate_signature(payload: bytes, signature: str, app_secret: str) -> bool:
    expected = hmac.new(
        app_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

### 5.3 Estrutura do Webhook — Comparação

**Evolution API (atual):**
```json
{
  "instance": "nome_instancia",
  "event": "messages-upsert",
  "data": {
    "key": {
      "remoteJid": "5511999999999@s.whatsapp.net",
      "fromMe": false,
      "id": "ABCDEF123456"
    },
    "pushName": "Nome do Contato",
    "message": {
      "conversation": "Olá!"
    },
    "messageType": "conversation"
  }
}
```

**API Oficial (novo):**
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "5511888888888",
              "phone_number_id": "PHONE_NUMBER_ID"
            },
            "contacts": [
              {
                "profile": { "name": "Nome do Contato" },
                "wa_id": "5511999999999"
              }
            ],
            "messages": [
              {
                "from": "5511999999999",
                "id": "wamid.HBgMNTUxMTk5OTk5OTkVAgASGCA1...",
                "timestamp": "1677000000",
                "type": "text",
                "text": { "body": "Olá!" }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

### 5.4 Mapeamento dos Campos do Webhook

| Dado | Evolution API | API Oficial |
|------|-------------|-------------|
| Instância | `body["instance"]` | `body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]` |
| Número remetente | `data["key"]["remoteJid"]` (formato `5511...@s.whatsapp.net`) | `messages[0]["from"]` (formato `5511...`) |
| Nome do contato | `data["pushName"]` | `contacts[0]["profile"]["name"]` |
| ID da mensagem | `data["key"]["id"]` | `messages[0]["id"]` (formato `wamid.xxx`) |
| Tipo da mensagem | `data["messageType"]` | `messages[0]["type"]` |
| Texto | `data["message"]["conversation"]` ou `data["message"]["extendedTextMessage"]["text"]` | `messages[0]["text"]["body"]` |
| É grupo? | `remoteJid.endswith("@g.us")` | **A ser determinado** (provavelmente via campo `group_id` ou JID similar) |
| fromMe | `data["key"]["fromMe"]` | Webhook só entrega mensagens recebidas (não `fromMe`) |
| Stanza ID (citação) | `data["contextInfo"]["stanzaId"]` | `messages[0]["context"]["id"]` |
| Menções | `data["contextInfo"]["mentionedJid"]` | **Não documentado para a Cloud API padrão** |

### 5.5 Webhooks por Tipo de Mensagem

**Texto:**
```json
{
  "type": "text",
  "text": { "body": "conteúdo da mensagem" }
}
```

**Imagem:**
```json
{
  "type": "image",
  "image": {
    "id": "MEDIA_ID",
    "mime_type": "image/jpeg",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/...",
    "caption": "legenda opcional"
  }
}
```

**Áudio:**
```json
{
  "type": "audio",
  "audio": {
    "id": "MEDIA_ID",
    "mime_type": "audio/ogg; codecs=opus",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/...",
    "voice": true
  }
}
```
> O campo `voice: true` indica que é uma mensagem de voz (gravada no WhatsApp), enquanto `voice: false` ou ausente indica um arquivo de áudio.

**Vídeo:**
```json
{
  "type": "video",
  "video": {
    "id": "MEDIA_ID",
    "mime_type": "video/mp4",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/..."
  }
}
```

**Sticker:**
```json
{
  "type": "sticker",
  "sticker": {
    "id": "MEDIA_ID",
    "mime_type": "image/webp",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/...",
    "animated": false
  }
}
```

**Documento:**
```json
{
  "type": "document",
  "document": {
    "id": "MEDIA_ID",
    "mime_type": "application/pdf",
    "sha256": "...",
    "url": "https://lookaside.fbsbx.com/...",
    "filename": "arquivo.pdf"
  }
}
```

---

## 6. Mudanças no Envio de Mensagens

### 6.1 Headers

**Evolution API:**
```python
headers = {
    "Content-Type": "application/json",
    "apiKey": EVOLUTION_API_KEY
}
```

**API Oficial:**
```python
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"
}
```

### 6.2 Base URL

**Evolution API:**
```
{EVOLUTION_API_URL}/message/sendText/{instancia}
{EVOLUTION_API_URL}/message/sendWhatsAppAudio/{instancia}
{EVOLUTION_API_URL}/chat/markMessageAsRead/{instancia}
...
```

**API Oficial:**
```
https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages    # Para tudo
https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/media       # Para upload de mídia
https://graph.facebook.com/v22.0/{MEDIA_ID}                    # Para download de mídia
```

> **Simplificação:** Na API oficial, quase tudo usa o **mesmo endpoint** (`/messages`), diferenciando pelo `type` no body. Isso é mais simples que a Evolution API que tem endpoints diferentes para cada operação.

---

## 7. Mudanças no Download/Upload de Mídia

### 7.1 Download de Mídia Recebida

**Fluxo Evolution API (atual):**
```
Webhook → get_media_base64(message) → retorna base64 string → pronto
```

**Fluxo API Oficial (novo):**
```
Webhook (inclui media_id + url) → GET url com Bearer token → recebe binário → base64.b64encode() → pronto
```

### 7.2 Upload de Mídia para Envio

**Fluxo Evolution API (atual):**
```
Gera áudio MP3 → base64 encode → send_audio(base64_string) → pronto
```

**Fluxo API Oficial (novo):**
```
Gera áudio OGG/Opus → POST /media (multipart, binário) → recebe media_id → POST /messages com media_id → pronto
```

### 7.3 Formatos Suportados

| Tipo | Formatos Aceitos | Tamanho Máximo |
|------|-----------------|----------------|
| Áudio | AAC, AMR, MP3, MP4 Audio, OGG (Opus only) | 16MB |
| Imagem | JPEG, PNG | 5MB |
| Vídeo | MP4, 3GPP (somente com H.264 e AAC) | 16MB |
| Documento | PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, TXT | 100MB |
| Sticker | WebP | 100KB (estático), 500KB (animado) |

---

## 8. Grupos — Análise Detalhada

### 8.1 Situação Atual

No sistema atual com a Evolution API:
1. Bot está num grupo do WhatsApp
2. Alguém no grupo **menciona o bot** (usando @)
3. O webhook recebe a mensagem com `contextInfo.mentionedJid` contendo o JID do bot
4. O bot verifica se é mencionado comparando com `BOT_MENTION_JID` (`117558187450509@lid`)
5. Se mencionado, processa a mensagem (ou a mensagem citada se houver)
6. Responde no grupo

### 8.2 API de Grupos — Status

A **WhatsApp Business Platform Groups API** foi anunciada pela Meta, e a página de overview da API menciona suporte a grupos. Contudo:

- ✅ A página de **overview** da API confirma que grupos são suportados
- ❌ As páginas de documentação detalhada (**Create and Manage**, **Send and Receive Messages**) retornaram "página não disponível" durante esta pesquisa
- ⚠️ Isso indica que a funcionalidade está em fase de rollout ou beta

### 8.3 Riscos e Considerações para Grupos

1. **Menções (`@bot`):** Na API oficial padrão (sem grupos), não há documentação clara sobre `mentionedJid` equivalente. Em grupos, presumivelmente existirá um mecanismo similar, mas sem documentação disponível não é possível confirmar.

2. **Identificação de grupo:** Na Evolution API, grupos são identificados pelo JID terminando em `@g.us`. Na API oficial, o mecanismo pode ser diferente.

3. **Mensagens citadas em grupo:** O comportamento de quote/reply em grupos na API oficial pode diferir do que temos hoje.

### 8.4 Recomendação para Grupos

> **⚠️ AGUARDAR** a documentação completa da Groups API antes de migrar a funcionalidade de grupos. A migração pode ser feita em fases:
> - **Fase 1:** Migrar mensagens diretas (DM) — totalmente possível hoje
> - **Fase 2:** Migrar funcionalidade de grupos — quando a documentação estiver disponível

---

## 9. Modelo de Preços

### 9.1 Mudança de Modelo (Julho 2025)

Desde **1 de julho de 2025**, o modelo mudou de cobrança por conversa para **cobrança por mensagem**:

### 9.2 O que é cobrado e o que é grátis

| Tipo de Mensagem | Custo | Condição |
|------------------|-------|----------|
| **Mensagens do usuário → negócio** | 🟢 **GRÁTIS** | Sempre |
| **Mensagens não-template** (`text`, `image`, `audio`, etc.) | 🟢 **GRÁTIS** | Dentro da janela de atendimento (24h) |
| **Template Utility** | 🟢 **GRÁTIS** | Dentro da janela de atendimento (24h) |
| **Template Utility** | 🟡 **COBRADO** | Fora da janela de atendimento |
| **Template Marketing** | 🔴 **SEMPRE COBRADO** | Qualquer momento |
| **Template Authentication** | 🔴 **SEMPRE COBRADO** | Qualquer momento |

### 9.3 Impacto no Projeto TáCertoIssoAI

**Boa notícia:** O bot TáCertoIssoAI é **reativo** — ele só responde quando o usuário envia uma mensagem. Isso significa que:

1. ✅ O usuário envia mensagem → **abre janela de 24h** → GRÁTIS
2. ✅ O bot responde com texto, áudio, etc. → **mensagem não-template dentro da janela** → GRÁTIS
3. ✅ Todas as respostas do bot são mensagens não-template (`type: "text"`, `type: "audio"`) → **GRÁTIS**

**Custo estimado: R$ 0,00 para o uso atual** (exceto se quiser enviar mensagens proativas fora da janela, o que o bot não faz).

### 9.4 Janela de Atendimento (Customer Service Window)

**Isso é uma novidade importante que não existe com a Evolution API:**

- A janela de 24h **se abre quando o usuário envia uma mensagem** ao negócio
- Dentro da janela, o bot pode enviar **qualquer tipo de mensagem** (texto, áudio, imagem, etc.)
- **Fora da janela**, o bot SÓ pode enviar **Template Messages** (pré-aprovadas pela Meta)
- A janela **reinicia** a cada nova mensagem do usuário

**Impacto prático:** Como o bot só responde a mensagens do usuário, a janela sempre estará aberta. Não há impacto negativo.

### 9.5 Free Entry Point (Anúncios)

Se o usuário clicar em um anúncio "Click to WhatsApp" e iniciar conversa:
- Janela estendida para **72 horas** (ao invés de 24h)
- **Todas as mensagens são gratuitas** durante essas 72 horas, incluindo templates

---

## 10. Limitações e Pontos de Atenção

### 🔴 Limitações Críticas

| # | Limitação | Impacto | Mitigação |
|---|-----------|---------|-----------|
| 1 | **Groups API em fase de rollout** | A funcionalidade de responder quando mencionado em grupos pode não estar disponível imediatamente | Migrar em fases: primeiro DMs, depois grupos quando a API estiver pronta |
| 2 | **Sem indicador "gravando áudio"** | Esteticamente, quando o bot grava áudio, mostrará "digitando" ao invés de "gravando" | Impacto menor, apenas visual |
| 3 | **Mensagens citadas sem mídia no webhook** | Precisa armazenar media_ids para poder baixar mídia de mensagens citadas | Implementar cache (Redis/memória) de media_ids |
| 4 | **Webhook exige HTTPS público** | O servidor precisa ter certificado SSL válido (não self-signed) | Usar proxy reverso (nginx/caddy) ou plataforma com HTTPS (Railway, Render, etc.) |

### 🟡 Diferenças Importantes

| # | Diferença | Detalhes |
|---|-----------|---------|
| 5 | **Formato de IDs** | Evolution usa IDs curtos; API oficial usa WAMIDs (`wamid.HBgM...`) — longos e opacos |
| 6 | **Formato de número** | Evolution: `5511999999999@s.whatsapp.net`; API oficial: `5511999999999` (sem sufixo) |
| 7 | **Mídia em binário** | Evolution retorna base64; API oficial retorna binário que precisa ser convertido |
| 8 | **Envio de áudio** | Evolution aceita base64 de qualquer formato; API oficial precisa upload multipart + formato OGG/Opus |
| 9 | **Typing indicator auto-dismiss** | Evolution persiste até parar manualmente; API oficial desaparece em 25s |
| 10 | **Estrutura do webhook** | Completamente diferente — aninhado em `entry[].changes[].value.messages[]` |

### 🟢 Vantagens da Migração

| # | Vantagem | Detalhes |
|---|----------|---------|
| 1 | **Sem risco de banimento** | API oficial da Meta, sem risco de bloqueio do número |
| 2 | **Infraestrutura mais simples** | Não precisa manter servidor Evolution API |
| 3 | **Webhook simplificado** | Mesmo endpoint para tudo, endpoint de envio unificado |
| 4 | **Custo zero para o caso de uso** | Mensagens reativas dentro da janela de 24h são gratuitas |
| 5 | **Suporte oficial** | Documentação, comunidade e suporte da Meta |
| 6 | **Escalabilidade** | 80 msg/s throughput, sem preocupação com infraestrutura |
| 7 | **Segurança** | HTTPS obrigatório, assinatura SHA256, tokens OAuth |

---

## 11. Arquivos que Precisam ser Modificados

### 11.1 Impacto por Arquivo

| Arquivo | Impacto | Tipo de Mudança |
|---------|---------|----------------|
| `config.py` | 🔴 **Alto** | Trocar variáveis Evolution por variáveis da API oficial |
| `main.py` | 🔴 **Alto** | Adicionar endpoint GET para verificação, mudar parsing do webhook POST, adicionar validação de assinatura |
| `nodes/evolution_api.py` | 🔴 **Reescrever** | Substituir completamente por `whatsapp_api.py` com todas as funções adaptadas |
| `nodes/data_extractor.py` | 🔴 **Alto** | Reescrever o parsing do webhook para o novo formato da API oficial |
| `nodes/filters.py` | 🟡 **Médio** | Adaptar detecção de grupo (formato diferente), adaptar detecção de menção do bot |
| `nodes/media_processor.py` | 🟡 **Médio** | Adaptar download de mídia (binário ao invés de base64), adaptar lógica de mensagens citadas |
| `nodes/response_sender.py` | 🟡 **Médio** | Adaptar chamadas de envio de texto e áudio |
| `nodes/ai_services.py` | 🟡 **Médio** | Mudar conversão de áudio de MP3 para OGG/Opus |
| `nodes/router.py` | 🟢 **Baixo** | Adaptar nomes dos tipos de mensagem (se necessário) |
| `state.py` | 🟢 **Baixo** | Possivelmente adicionar campos para media_id, etc. |
| `graph.py` | 🟢 **Baixo** | Estrutura do workflow permanece a mesma |
| `nodes/fact_checker.py` | ⚪ **Nenhum** | Não usa a Evolution API |
| `requirements.txt` | 🟢 **Baixo** | Sem mudanças (httpx já é usado) |

### 11.2 Estimativa de Complexidade

- **Arquivos a reescrever:** 2 (`evolution_api.py` → `whatsapp_api.py`, `data_extractor.py`)
- **Arquivos a adaptar significativamente:** 3 (`main.py`, `config.py`, `media_processor.py`)
- **Arquivos a adaptar levemente:** 4 (`filters.py`, `response_sender.py`, `ai_services.py`, `router.py`)
- **Arquivos sem mudança:** 3 (`graph.py`, `fact_checker.py`, `state.py`)
- **Novo arquivo necessário:** 1 (cache de media_ids para mensagens citadas)

---

## 12. Plano de Migração Sugerido

### Fase 1 — Preparação (sem código)
- [ ] Criar app no Meta Developers Portal
- [ ] Configurar WhatsApp Business API no app
- [ ] Gerar System User Token permanente
- [ ] Configurar webhook URL no painel da Meta
- [ ] Testar webhook com número de teste do Meta

### Fase 2 — Infraestrutura Base
- [ ] Criar `config.py` novo com variáveis da API oficial
- [ ] Implementar endpoint GET `/webhook` para verificação
- [ ] Implementar validação de assinatura X-Hub-Signature-256
- [ ] Criar `whatsapp_api.py` com funções básicas: `send_text()`, `mark_as_read()`

### Fase 3 — Recebimento de Mensagens
- [ ] Reescrever `data_extractor.py` para parser o formato do webhook oficial
- [ ] Adaptar `main.py` para o novo formato de webhook (POST)
- [ ] Testar recebimento de mensagens de texto

### Fase 4 — Envio de Mensagens
- [ ] Implementar `send_text()` com suporte a quote (context.message_id)
- [ ] Implementar `send_presence()` (typing indicator)
- [ ] Testar envio de respostas de texto

### Fase 5 — Mídia
- [ ] Implementar download de mídia (GET media URL → binário → base64)
- [ ] Implementar upload de mídia (POST multipart → media_id)
- [ ] Adaptar `ai_services.py` para gerar OGG/Opus ao invés de MP3
- [ ] Implementar `send_audio()` (upload + envio)
- [ ] Implementar cache de media_ids para mensagens citadas
- [ ] Adaptar `media_processor.py` para novo fluxo
- [ ] Testar processamento de áudio, imagem, vídeo

### Fase 6 — Funcionalidades Completas
- [ ] Adaptar `filters.py` para formato de número da API oficial
- [ ] Adaptar `response_sender.py` 
- [ ] Adaptar `router.py` se tipos de mensagem mudarem
- [ ] Testar flow completo de DM

### Fase 7 — Grupos (quando disponível)
- [ ] Aguardar documentação completa da Groups API
- [ ] Implementar detecção de grupos
- [ ] Implementar detecção de menção do bot
- [ ] Testar flow completo de grupos

---

## 13. Configuração Inicial (Setup)

### 13.1 Pré-requisitos
- ✅ BM verificada no Facebook
- [ ] App criado no Meta Developers Portal (type: Business)
- [ ] WhatsApp Business Account (WABA) vinculada ao app
- [ ] Número de telefone verificado e registrado na WABA
- [ ] System User Token com permissão `whatsapp_business_messaging`

### 13.2 Configuração do Webhook no Meta

1. No App Dashboard → WhatsApp → Configuration
2. **Callback URL:** `https://seu-dominio.com/webhook`
3. **Verify Token:** String definida por você (ex: `meu_token_secreto_123`)
4. **Webhook Fields:** Subscrever em `messages` (mínimo necessário)

### 13.3 Endpoint do Servidor

O servidor precisa:
- Ter **HTTPS válido** (certificado SSL real, não self-signed)
- Responder **GET /webhook** com `hub.challenge` para verificação
- Responder **POST /webhook** com `200 OK` para notificações
- Idealmente validar `X-Hub-Signature-256`

### 13.4 Números de Teste

A Meta fornece um número de teste gratuito no sandbox. Para produção:
- Comprar/migrar um número de telefone real
- O número precisa ser registrado no WhatsApp Business Account
- Não pode estar registrado no WhatsApp pessoal simultaneamente

---

## 14. Conclusão

### ✅ Viabilidade Geral: **ALTA**

A migração é totalmente viável para **mensagens diretas (DM)**, que representam a maior parte do uso do bot. Todas as funcionalidades core (texto, áudio, imagem, vídeo, quote, typing) têm equivalentes na API oficial.

### ⚠️ Risco Principal: **Grupos**

A funcionalidade de grupos (responder quando mencionado) é o único ponto onde há incerteza, pois a documentação da Groups API ainda está sendo publicada pela Meta.

### 💰 Custo: **Zero para uso atual**

Como o bot é reativo (só responde a mensagens do usuário), todas as respostas ficam dentro da janela de 24h e são mensagens não-template, portanto **gratuitas**.

### 🏗️ Esforço de Migração: **Médio**

Estimativa de 2-4 dias de desenvolvimento para a migração completa (sem grupos):
- ~1 dia: Setup + config + webhook
- ~1 dia: `whatsapp_api.py` + `data_extractor.py`
- ~1-2 dias: Adaptação de `media_processor.py`, `ai_services.py`, testes

### 📋 Decisão Recomendada

| Opção | Prós | Contras |
|-------|------|---------|
| **Migrar agora (sem grupos)** | Elimina risco de banimento, infraestrutura mais simples | Grupos ficam sem funcionar temporariamente |
| **Esperar Groups API** | Migração completa de uma vez | Mantém risco de banimento, data incerta |
| **Migração parcial** | DMs migram agora, grupos ficam na Evolution API | Complexidade de manter dois sistemas |

**Recomendação:** Opção 1 (migrar agora sem grupos) se a funcionalidade de grupos não for crítica, ou Opção 3 (migração parcial) se grupos forem essenciais.

---

> **Documento gerado por pesquisa na documentação oficial da Meta/WhatsApp**  
> **Última atualização:** Julho 2025  
> **Fontes consultadas:**  
> - https://developers.facebook.com/docs/whatsapp/cloud-api  
> - https://developers.facebook.com/documentation/business-messaging/whatsapp  
> - https://developers.facebook.com/docs/whatsapp/pricing  
> - https://developers.facebook.com/docs/graph-api/webhooks  
> - https://business.whatsapp.com/products/platform-pricing
