# 🚀 Deploy Multi-Serviço no EasyPanel (Bot + Redis)

Guia para migrar do deploy com serviço único (descrito em `DOCKER_EASYPANEL_DEPLOY.md`) para o deploy multi-serviço com **Redis** rodando como serviço separado no EasyPanel.

---

## 📋 Por que multi-serviço?

O bot usa **Redis** para:
- Registro de usuários e aceite de termos
- Debounce de mensagens (1 segundo)
- Histórico de conversa (5 minutos)
- Fila de mensagens pendentes

Sem o Redis, o bot **não funciona**. Com multi-serviço, o Redis roda ao lado do bot no mesmo projeto do EasyPanel.

---

## 🔄 Migrando do Deploy Atual (Serviço Único)

Se você já fez deploy conforme o `DOCKER_EASYPANEL_DEPLOY.md`, siga estes passos para adicionar o Redis:

### 1. Adicionar serviço Redis no EasyPanel

1. Acesse o projeto existente no dashboard do EasyPanel
2. Clique em **"+ Service"** → **"Database"** → **"Redis"**
   - Ou: **"+ Service"** → **"Docker"** e use a imagem `redis:7-alpine`
3. Configure:
   - **Nome do serviço**: `redis`
   - **Imagem**: `redis:7-alpine` (se escolheu Docker)
   - **Comando**: `redis-server --appendonly yes`
   - **Volume**: monte `/data` para persistência
4. Clique em **Deploy**

### 2. Configurar a variável REDIS_URL no bot

1. Vá para o serviço do bot (já existente)
2. Em **Environment Variables**, adicione:

```
REDIS_URL=redis://redis:6379/0
```

> **Nota**: O hostname `redis` é o nome do serviço que você criou no passo anterior. O EasyPanel cria uma rede interna entre serviços do mesmo projeto, então eles se comunicam pelo nome.

3. Clique em **Deploy** / **Redeploy** no serviço do bot

### 3. Verificar a conexão

Nos logs do bot, confirme que ele iniciou sem erros de conexão com Redis. Acesse:

```
https://sua-url.easypanel.host/health
```

Deve retornar `{"status":"ok"}`.

---

## 🆕 Deploy do Zero (Multi-Serviço)

Se está começando um projeto novo no EasyPanel:

### 1. Criar projeto

1. No dashboard do EasyPanel, clique em **"Create Project"**
2. Nome: `whatsapp-bot` (ou outro de sua preferência)
3. Selecione o servidor

### 2. Criar serviço Redis

1. **"+ Service"** → **"Database"** → **"Redis"**
   - Ou: **"+ Service"** → **"Docker"**
2. Configure:
   - **Nome**: `redis`
   - **Imagem**: `redis:7-alpine`
   - **Comando**: `redis-server --appendonly yes`
   - **Volume**: `/data` (persistência)
   - **Restart Policy**: `Always`
3. Deploy

### 3. Criar serviço do Bot

1. **"+ Service"** → **"Docker"** ou **"App"**
2. Configure:
   - **Nome**: `whatsapp-integration`
   - **Imagem**: `seu-usuario/whatsapp-integration:latest` (Docker Hub)
   - **Porta**: `5000`
   - **Restart Policy**: `Always`

### 4. Variáveis de ambiente do bot

Adicione todas as variáveis no serviço do bot:

```env
# WhatsApp Business Cloud API
WHATSAPP_ACCESS_TOKEN=seu_token
WHATSAPP_PHONE_NUMBER_ID=seu_phone_id
WHATSAPP_VERIFY_TOKEN=seu_verify_token
WHATSAPP_APP_SECRET=seu_app_secret

# Redis (nome do serviço no EasyPanel)
REDIS_URL=redis://redis:6379/0

# Google Gemini
GOOGLE_GEMINI_API_KEY=sua_chave_gemini

# Google Cloud Vision API
GOOGLE_CLOUD_API_KEY=sua_chave_cloud_vision

# Fact-checking API
FACT_CHECK_API_URL=https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app

# Servidor
WEBHOOK_PORT=5000
```

Variáveis opcionais (com valores padrão):

```env
GEMINI_TRANSCRIPTION_MODEL=gemini-3-flash-preview
GEMINI_IMAGE_MODEL=gemini-3-flash-preview
GEMINI_VIDEO_MODEL=gemini-3-flash-preview
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
GEMINI_TTS_VOICE=Kore
GEMINI_CLASSIFIER_MODEL=gemini-2.5-flash-lite
GEMINI_CHAT_MODEL=gemini-2.5-flash-lite
MESSAGE_DEBOUNCE_SECONDS=1.0
CHAT_HISTORY_TTL_SECONDS=300
```

### 5. Configurar domínio e SSL

1. No serviço do bot, vá em **"Domains"**
2. Adicione seu domínio ou use o subdomínio do EasyPanel
3. Ative SSL (Let's Encrypt automático)
4. Use a URL final para configurar o webhook no Meta for Developers:
   - **Callback URL**: `https://seu-dominio/webhook`
   - **Verify Token**: o valor de `WHATSAPP_VERIFY_TOKEN`

### 6. Deploy dos dois serviços

1. Faça deploy do Redis primeiro
2. Depois faça deploy do bot
3. Verifique os logs de ambos

---

## ⚡ Atualizando o Projeto na VPS

### Método rápido (recomendado)

Sempre que fizer alterações no código:

```bash
# Na sua máquina local:

# 1. Build da nova imagem
docker build -t seu-usuario/whatsapp-integration:latest .

# 2. Push para o Docker Hub
docker push seu-usuario/whatsapp-integration:latest

# 3. No EasyPanel: clique em "Redeploy" no serviço do bot
#    Ele puxa a imagem nova automaticamente
```

É só isso — **3 comandos + 1 clique**.

### Com versionamento (para controle de releases)

```bash
# Build com tag de versão
docker build -t seu-usuario/whatsapp-integration:v2.1.0 .
docker tag seu-usuario/whatsapp-integration:v2.1.0 seu-usuario/whatsapp-integration:latest

# Push ambas as tags
docker push seu-usuario/whatsapp-integration:v2.1.0
docker push seu-usuario/whatsapp-integration:latest

# No EasyPanel: Redeploy
```

### Script de deploy (opcional)

Crie um arquivo `deploy.sh` na raiz do projeto:

```bash
#!/bin/bash
set -e

DOCKER_USER="seu-usuario"
IMAGE_NAME="whatsapp-integration"
TAG="${1:-latest}"

echo "🔨 Building image..."
docker build -t "$DOCKER_USER/$IMAGE_NAME:$TAG" .

if [ "$TAG" != "latest" ]; then
    docker tag "$DOCKER_USER/$IMAGE_NAME:$TAG" "$DOCKER_USER/$IMAGE_NAME:latest"
fi

echo "📦 Pushing to Docker Hub..."
docker push "$DOCKER_USER/$IMAGE_NAME:$TAG"
[ "$TAG" != "latest" ] && docker push "$DOCKER_USER/$IMAGE_NAME:latest"

echo "✅ Imagem enviada! Agora clique em 'Redeploy' no EasyPanel."
```

Uso:

```bash
chmod +x deploy.sh
./deploy.sh          # Push como :latest
./deploy.sh v2.1.0   # Push como :v2.1.0 + :latest
```

---

## 🔧 Estrutura no EasyPanel

Após a configuração, seu projeto deve ter esta estrutura:

```
Projeto: whatsapp-bot
├── Serviço: redis          (redis:7-alpine)
│   ├── Volume: /data
│   └── Porta interna: 6379
└── Serviço: whatsapp-integration  (seu-usuario/whatsapp-integration:latest)
    ├── Porta: 5000
    ├── Domínio: https://seu-dominio
    └── Env: REDIS_URL=redis://redis:6379/0
```

Os dois serviços compartilham a rede interna do projeto. O bot acessa o Redis pelo hostname `redis` (nome do serviço).

---

## 🐛 Troubleshooting

### Bot não conecta no Redis

- Verifique se `REDIS_URL` usa o nome correto do serviço Redis no EasyPanel
- Confira nos logs do Redis se ele está rodando (`Ready to accept connections`)
- Teste: o serviço Redis deve estar **verde** (healthy) antes de deployar o bot

### Dados perdidos após redeploy do Redis

- Certifique-se de que o volume `/data` está configurado no serviço Redis
- O `--appendonly yes` garante persistência em disco
- Redeploy do **bot** não afeta os dados do Redis

### Redis usando muita memória

- O bot usa TTL em todas as chaves (mensagens: 2min, histórico: 5min, dados de usuário: 24h)
- Se ainda assim for problema, adicione `--maxmemory 256mb --maxmemory-policy allkeys-lru` ao comando do Redis

### Serviços não se comunicam

- Ambos devem estar no **mesmo projeto** no EasyPanel
- O hostname na `REDIS_URL` deve ser o **nome exato do serviço** Redis no EasyPanel
- Não use `localhost` — use o nome do serviço (ex: `redis`)

---

## 📚 Referências

- [Documentação EasyPanel](https://easypanel.io/docs)
- [Docker Hub - Redis](https://hub.docker.com/_/redis)
- [Deploy serviço único](./DOCKER_EASYPANEL_DEPLOY.md)
