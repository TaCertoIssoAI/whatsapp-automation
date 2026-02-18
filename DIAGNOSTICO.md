# Diagnóstico: Evolution API não envia webhooks

## ✅ O que já está funcionando:
1. Servidor FastAPI rodando na porta 4000
2. ngrok expondo `https://corky-luci-cosmonautically.ngrok-free.dev`
3. Endpoint `/` recebendo webhooks de teste local

## ❌ O que NÃO está funcionando:
- Evolution API não está enviando webhooks quando você manda mensagens no WhatsApp

## 🔍 Possíveis causas:

### 1. Webhook URL incorreta na Evolution API
Acesse o painel da Evolution API e verifique:
- **URL deve ser**: `https://corky-luci-cosmonautically.ngrok-free.dev`
- **SEM** barra no final
- **SEM** `/webhook` ou qualquer path
- Webhook by Events: **OFF**

### 2. Token OAuth expirado ou inválido
A instância `api-oficial` precisa de um **Access Token válido do WhatsApp Business**.

**Como obter um novo token:**
1. Acesse: https://developers.facebook.com/apps/
2. Selecione seu app WhatsApp Business
3. Menu: WhatsApp > API Setup
4. Copie o **Temporary access token** (ou crie um permanente)
5. Cole na configuração da instância `api-oficial` na Evolution API

**IMPORTANTE**: O token expira. Para produção, você precisa gerar um **System User Token** permanente.

### 3. Número de telefone não verificado
O número `1024353610754394` precisa estar verificado no Meta Business Manager.

### 4. Evolution API não conectada
No painel, verifique se o status da instância está **"Connected"** (verde).

## 🧪 Teste manual de webhook

Execute este comando para testar se a Evolution API consegue enviar webhooks:

```bash
# Na Evolution API, vá em Settings > Webhook > Test Webhook
# Ou use a API:
curl -X POST "https://ta-certo-isso-ai-evolution-api.598vvv.easypanel.host/webhook/test/api-oficial" \
  -H "apikey: SEU_API_KEY_AQUI"
```

## 📋 Checklist de configuração:

- [ ] Instância `api-oficial` existe e está **Connected**
- [ ] Access Token do WhatsApp está configurado e **válido**
- [ ] Phone Number ID está correto: `1024353610754394`
- [ ] Webhook URL: `https://corky-luci-cosmonautically.ngrok-free.dev`
- [ ] Webhook by Events: **OFF**
- [ ] Evento `MESSAGES_UPSERT` está **marcado**
- [ ] Webhook Base64: **ON** (para receber mídia)

## 🔧 Comando para ver configuração da instância:

```bash
curl -X GET "https://ta-certo-isso-ai-evolution-api.598vvv.easypanel.host/instance/connectionState/api-oficial" \
  -H "apikey: 2F2E88FC028B-40E1-8857-C41665327052"
```

## 🚨 Se nada funcionar:

**Delete e recrie a instância `api-oficial`** no painel web da Evolution API com:
- Integration: **WhatsApp Business (Cloud API)**
- Phone Number ID: `1024353610754394`
- Access Token: (cole o token do Meta Developer Console)
- Business ID: `680801461726132`
- Webhook: `https://corky-luci-cosmonautically.ngrok-free.dev`
