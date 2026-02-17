# 🚀 Deploy do WhatsApp Integration Bot no EasyPanel com Docker

Este guia completo explica como containerizar a aplicação e fazer deploy no EasyPanel para manter o bot rodando 24/7.

---

## 📋 Pré-requisitos

Antes de começar, certifique-se de ter:

- ✅ Docker instalado na sua máquina local ([Instalar Docker](https://docs.docker.com/get-docker/))
- ✅ Conta no [EasyPanel](https://easypanel.io/)
- ✅ Conta no Docker Hub ou outro registry de imagens (opcional, mas recomendado)
- ✅ Todas as variáveis de ambiente configuradas (APIs do WhatsApp, Google Gemini, etc.)

---

## 🐳 Parte 1: Containerização Local

### 1.1. Arquivos Docker Criados

Já foram criados os seguintes arquivos no projeto:

- **`Dockerfile`**: Define a imagem Docker da aplicação
- **`.dockerignore`**: Exclui arquivos desnecessários da imagem
- **`docker-compose.yml`**: Facilita testes locais

### 1.2. Criar arquivo .env (se ainda não existe)

Crie um arquivo `.env` na raiz do projeto com todas as variáveis de ambiente necessárias:

```bash
# WhatsApp Business Cloud API
WHATSAPP_ACCESS_TOKEN=seu_token_aqui
WHATSAPP_PHONE_NUMBER_ID=seu_phone_id_aqui
WHATSAPP_VERIFY_TOKEN=seu_verify_token_aqui
WHATSAPP_APP_SECRET=seu_app_secret_aqui

# Google Gemini
GOOGLE_GEMINI_API_KEY=sua_chave_gemini_aqui
GEMINI_TRANSCRIPTION_MODEL=gemini-3-flash-preview
GEMINI_IMAGE_MODEL=gemini-3-flash-preview
GEMINI_VIDEO_MODEL=gemini-3-flash-preview
GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts
GEMINI_TTS_VOICE=Kore

# Google Cloud Vision API
GOOGLE_CLOUD_API_KEY=sua_chave_cloud_vision_aqui

# Fact-checking API
FACT_CHECK_API_URL=https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app

# Servidor
WEBHOOK_PORT=5000
```

⚠️ **IMPORTANTE**: Nunca commite o arquivo `.env` no Git! Adicione ao `.gitignore`.

### 1.3. Testar a imagem Docker localmente

```bash
# Construir a imagem
docker build -t whatsapp-integration:latest .

# Testar com docker-compose
docker-compose up

# Ou rodar diretamente
docker run -p 5000:5000 --env-file .env whatsapp-integration:latest
```

Acesse `http://localhost:5000/health` para verificar se está funcionando.

### 1.4. Testar o webhook localmente (opcional)

Para testar o webhook localmente, use ngrok ou similar:

```bash
# Instalar ngrok
# https://ngrok.com/download

# Criar túnel
ngrok http 5000

# Use a URL fornecida (ex: https://abc123.ngrok.io) 
# para configurar no Facebook App webhook
```

---

## 📦 Parte 2: Publicar Imagem no Docker Hub

### 2.1. Criar conta no Docker Hub

1. Acesse [Docker Hub](https://hub.docker.com/)
2. Crie uma conta gratuita
3. Crie um repositório (ex: `seu-usuario/whatsapp-integration`)

### 2.2. Login no Docker Hub

```bash
docker login
# Digite seu usuário e senha do Docker Hub
```

### 2.3. Fazer tag e push da imagem

```bash
# Tag da imagem (substitua 'seu-usuario' pelo seu usuário do Docker Hub)
docker tag whatsapp-integration:latest seu-usuario/whatsapp-integration:latest

# Push para o Docker Hub
docker push seu-usuario/whatsapp-integration:latest
```

---

## ☁️ Parte 3: Deploy no EasyPanel

### 3.1. Criar conta e configurar servidor no EasyPanel

1. Acesse [EasyPanel](https://easypanel.io/) e faça login
2. **Conecte um servidor** (VPS):
   - Pode usar DigitalOcean, Linode, Vultr, Hetzner, etc.
   - Ou adicione seu próprio servidor VPS via SSH
3. Siga as instruções do EasyPanel para instalar o agente no servidor

### 3.2. Criar novo projeto

1. No dashboard do EasyPanel, clique em **"Create Project"**
2. Dê um nome ao projeto (ex: `whatsapp-bot`)
3. Selecione o servidor onde deseja fazer deploy

### 3.3. Adicionar serviço Docker

1. Dentro do projeto, clique em **"Add Service"** ou **"Create Service"**
2. Escolha **"Docker"** ou **"Custom Docker Image"**
3. Configure:
   - **Service Name**: `whatsapp-integration`
   - **Docker Image**: `seu-usuario/whatsapp-integration:latest` (ou use a imagem local se fez push para o registry do EasyPanel)
   - **Port**: `5000`

### 3.4. Configurar variáveis de ambiente

No EasyPanel, vá para a seção de **Environment Variables** do serviço e adicione todas as variáveis:

```
WHATSAPP_ACCESS_TOKEN=seu_token_aqui
WHATSAPP_PHONE_NUMBER_ID=seu_phone_id_aqui
WHATSAPP_VERIFY_TOKEN=seu_verify_token_aqui
WHATSAPP_APP_SECRET=seu_app_secret_aqui
GOOGLE_GEMINI_API_KEY=sua_chave_gemini_aqui
GOOGLE_CLOUD_API_KEY=sua_chave_cloud_vision_aqui
FACT_CHECK_API_URL=https://ta-certo-isso-ai-767652480333.southamerica-east1.run.app
WEBHOOK_PORT=5000
```

### 3.5. Configurar domínio/DNS (opcional)

1. No EasyPanel, vá para **"Domains"** no seu serviço
2. Adicione um domínio personalizado ou use o domínio fornecido pelo EasyPanel
3. Configure SSL automático (Let's Encrypt)
4. Anote a URL final (ex: `https://whatsapp-bot.sua-instancia.easypanel.host`)

### 3.6. Configurar restart policy

1. Nas configurações do serviço, procure por **"Restart Policy"**
2. Selecione **"Always"** ou **"Unless Stopped"**
3. Isso garante que o container reinicie automaticamente em caso de falhas ou reinicialização do servidor

### 3.7. Deploy!

1. Clique em **"Deploy"** ou **"Start"**
2. Aguarde o container iniciar (pode demorar alguns minutos na primeira vez)
3. Verifique os logs para confirmar que está rodando:
   - Vá para **"Logs"** no painel do serviço
   - Procure por mensagens como "Iniciando servidor na porta 5000..."

---

## ✅ Parte 4: Verificação e Testes

### 4.1. Testar o endpoint de health

```bash
# Substitua pela sua URL do EasyPanel
curl https://whatsapp-bot.sua-instancia.easypanel.host/health
```

Deve retornar: `{"status":"ok"}`

### 4.2. Configurar webhook no Facebook App

1. Acesse o [Meta for Developers](https://developers.facebook.com/)
2. Vá para seu App → WhatsApp → Configuration
3. Em **Webhook**, clique em **"Edit"**
4. Configure:
   - **Callback URL**: `https://whatsapp-bot.sua-instancia.easypanel.host/webhook`
   - **Verify Token**: o valor que você definiu em `WHATSAPP_VERIFY_TOKEN`
5. Clique em **"Verify and Save"**

### 4.3. Subscrever aos eventos

Ainda na configuração do webhook, certifique-se de estar inscrito nos seguintes eventos:
- ✅ `messages`
- ✅ `message_deliveries` (opcional)
- ✅ `message_reads` (opcional)

### 4.4. Testar enviando mensagem

Envie uma mensagem no WhatsApp para o número configurado e verifique:
1. Logs no EasyPanel (deve mostrar "Webhook recebido")
2. Resposta do bot

---

## 🔄 Parte 5: Atualizações e Manutenção

### 5.1. Atualizar a aplicação

Sempre que fizer alterações no código:

```bash
# 1. Rebuildar a imagem localmente
docker build -t whatsapp-integration:latest .

# 2. Fazer tag com a versão
docker tag whatsapp-integration:latest tacertoissoai/whatsapp-integration:v1.1.0
docker tag whatsapp-integration:latest tacertoissoai/whatsapp-integration:latest

# 3. Push para o Docker Hub
docker push tacertoissoai/whatsapp-integration:v1.1.0
docker push tacertoissoai/whatsapp-integration:latest

# 4. No EasyPanel, vá para o serviço e clique em "Redeploy" ou "Restart"
# Ele vai baixar a nova imagem automaticamente
```

### 5.2. Monitoramento

No EasyPanel você pode:
- Ver logs em tempo real
- Monitorar uso de CPU e memória
- Configurar alertas
- Ver histórico de deploys

### 5.3. Backup das variáveis de ambiente

Mantenha um backup seguro das suas variáveis de ambiente em um gerenciador de senhas ou arquivo criptografado.

---

## 🐛 Troubleshooting

### Container não inicia

1. Verifique os logs no EasyPanel
2. Confirme se todas as variáveis de ambiente estão configuradas
3. Teste localmente com `docker-compose up` para reproduzir o erro

### Webhook não recebe mensagens

1. Verifique se a URL do webhook está correta no Facebook App
2. Confirme que o verify token está correto
3. Teste o endpoint `/health` para ver se o container está respondendo
4. Verifique os logs para erros de assinatura (`X-Hub-Signature-256`)

### Bot responde lentamente

1. Verifique os recursos do servidor no EasyPanel (CPU, memória)
2. Considere usar um servidor com mais recursos
3. Verifique se as APIs externas (Gemini, Fact Check) estão respondendo normalmente

### Container para sozinho

1. Verifique se o restart policy está configurado como "Always"
2. Veja os logs antes do crash para identificar o erro
3. Adicione mais memória ou CPU se necessário

---

## 💡 Dicas Extras

### Usar GitHub Actions para CI/CD

Configure GitHub Actions para fazer build e push automático:

```yaml
# .github/workflows/deploy.yml
name: Deploy to Docker Hub

on:
  push:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Login to Docker Hub
        uses: docker/login-action@v1
        with:
          username: ${{ secrets.DOCKER_USERNAME }}
          password: ${{ secrets.DOCKER_PASSWORD }}
      
      - name: Build and push
        uses: docker/build-push-action@v2
        with:
          push: true
          tags: seu-usuario/whatsapp-integration:latest
```

### Usar Docker Compose no EasyPanel

O EasyPanel também suporta deploy via `docker-compose.yml`. Você pode usar o arquivo já criado!

### Monitoramento avançado

Considere adicionar ferramentas de monitoramento como:
- Sentry para tracking de erros
- Prometheus + Grafana para métricas
- Uptime Robot para monitorar disponibilidade

---

## 📚 Recursos Adicionais

- [Documentação Docker](https://docs.docker.com/)
- [Documentação EasyPanel](https://easypanel.io/docs)
- [WhatsApp Business Platform](https://developers.facebook.com/docs/whatsapp)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)

---

## ✨ Resumo dos Comandos Principais

```bash
# Build local
docker build -t whatsapp-integration:latest .

# Testar local
docker-compose up

# Push para Docker Hub
docker tag whatsapp-integration:latest seu-usuario/whatsapp-integration:latest
docker push seu-usuario/whatsapp-integration:latest

# Verificar logs
docker logs -f container_name

# Parar container
docker-compose down
```

---

**🎉 Pronto! Seu bot agora está rodando 24/7 no EasyPanel!**

Se tiver problemas, verifique os logs e as configurações das variáveis de ambiente.
