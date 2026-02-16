# 🔘 Botões Interativos no WhatsApp Business Cloud API

## ✅ Resumo Executivo

**SIM, é possível adicionar botões interativos!** A API oficial do WhatsApp suporta **Interactive Reply Buttons** (botões de resposta rápida) que aparecem abaixo da mensagem para o usuário clicar.

**NÃO precisa de conta OBA** (Official Business Account) para usar botões interativos. Funciona com contas normais do WhatsApp Business Cloud API.

---

## 📱 Tipos de Mensagens Interativas Disponíveis

A WhatsApp Cloud API oferece vários tipos de mensagens interativas:

### 1. **Interactive Reply Buttons** (Botões de Resposta) ⭐ **IDEAL PARA SEU CASO**

- **Limite**: Até **3 botões** por mensagem
- **Texto do botão**: Máximo 20 caracteres
- **ID único**: Cada botão tem um identificador para rastrear a escolha do usuário
- **Aparência**: Botões fixos abaixo da mensagem

**Exemplo visual:**
```
┌────────────────────────────────┐
│ Você gostaria de verificar     │
│ essa notícia?                  │
│                                │
│ ┌──────────────────────────┐  │
│ │         ✅ Sim            │  │
│ └──────────────────────────┘  │
│ ┌──────────────────────────┐  │
│ │         ❌ Não            │  │
│ └──────────────────────────┘  │
└────────────────────────────────┘
```

### 2. **Interactive List Messages** (Mensagens com Lista)

- **Limite**: Até **10 opções** em uma lista
- **Ideal para**: Múltiplas escolhas (mais de 3 opções)
- **Funcionalidade**: Usuário clica em um botão que abre uma lista de opções

### 3. **Interactive CTA URL Button** (Botão com Link)

- Permite adicionar um botão que abre uma URL
- Útil para redirecionar para sites

### 4. **WhatsApp Flows** (Formulários Interativos)

- Permite criar formulários complexos dentro do WhatsApp
- Ideal para: agendamentos, coleta de dados, questionários

---

## 🛠️ Como Implementar Botões de Resposta (Reply Buttons)

### **Estrutura da Requisição**

```json
POST https://graph.facebook.com/v24.0/{PHONE_NUMBER_ID}/messages

Headers:
- Content-Type: application/json
- Authorization: Bearer {ACCESS_TOKEN}

Body:
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "{NUMERO_DO_USUARIO}",
  "type": "interactive",
  "interactive": {
    "type": "button",
    "header": {
      "type": "text",
      "text": "Verificação de Fake News"
    },
    "body": {
      "text": "Você gostaria de verificar essa notícia?"
    },
    "footer": {
      "text": "TaCertoIssoAI - Detector de Fake News"
    },
    "action": {
      "buttons": [
        {
          "type": "reply",
          "reply": {
            "id": "btn_sim",
            "title": "✅ Sim"
          }
        },
        {
          "type": "reply",
          "reply": {
            "id": "btn_nao",
            "title": "❌ Não"
          }
        }
      ]
    }
  }
}
```

### **Parâmetros Importantes**

| Campo | Obrigatório? | Descrição | Exemplo |
|-------|--------------|-----------|---------|
| `type` | ✅ | Sempre `"interactive"` para mensagens interativas | `"interactive"` |
| `interactive.type` | ✅ | Tipo de interação: `"button"` ou `"list"` | `"button"` |
| `header` | ❌ | Cabeçalho da mensagem (opcional) | Texto, imagem, vídeo ou documento |
| `body.text` | ✅ | Texto principal da mensagem (máx 1024 caracteres) | "Você gostaria de verificar essa notícia?" |
| `footer.text` | ❌ | Rodapé da mensagem (máx 60 caracteres) | "TaCertoIssoAI" |
| `action.buttons` | ✅ | Array de botões (máx 3) | Ver estrutura acima |
| `buttons[].reply.id` | ✅ | Identificador único do botão (máx 256 caracteres) | `"btn_sim"`, `"btn_nao"` |
| `buttons[].reply.title` | ✅ | Texto do botão (máx 20 caracteres) | "✅ Sim", "❌ Não" |

---

## 📥 Como Receber a Resposta do Usuário (Webhook)

Quando o usuário clica em um botão, o webhook recebe um evento como este:

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "102290129340398",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15550783881",
              "phone_number_id": "106540352242922"
            },
            "contacts": [
              {
                "profile": {
                  "name": "João Silva"
                },
                "wa_id": "5511999999999"
              }
            ],
            "messages": [
              {
                "from": "5511999999999",
                "id": "wamid.HBgLMTY0NjcwNDM1OTUVAgASGBQzQThBREYwNzc2RDc2QjA1QTIwMgA=",
                "timestamp": "1714510003",
                "type": "interactive",
                "interactive": {
                  "type": "button_reply",
                  "button_reply": {
                    "id": "btn_sim",
                    "title": "✅ Sim"
                  }
                }
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

### **Campos Importantes no Webhook**

- `messages[0].type`: Será `"interactive"`
- `messages[0].interactive.type`: Será `"button_reply"`
- `messages[0].interactive.button_reply.id`: O ID do botão clicado (`"btn_sim"` ou `"btn_nao"`)
- `messages[0].interactive.button_reply.title`: O texto do botão clicado

---

## 💡 Exemplo Prático: Implementação no Seu Bot

### **1. Modificar `nodes/response_sender.py`**

Criar uma nova função para enviar mensagem com botões:

```python
async def send_interactive_button_message(state: WorkflowState) -> WorkflowState:
    """Envia mensagem interativa com botões de Sim/Não."""
    
    phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
    access_token = config.WHATSAPP_ACCESS_TOKEN
    recipient = state["numero_quem_enviou"]
    
    url = f"https://graph.facebook.com/v24.0/{phone_number_id}/messages"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {
                "type": "text",
                "text": "🔍 Verificação de Fake News"
            },
            "body": {
                "text": "Gostaria que eu verifique essa informação para você?"
            },
            "footer": {
                "text": "TaCertoIssoAI"
            },
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": "verify_yes",
                            "title": "✅ Sim, verificar"
                        }
                    },
                    {
                        "type": "reply",
                        "reply": {
                            "id": "verify_no",
                            "title": "❌ Não, obrigado"
                        }
                    }
                ]
            }
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        
        if response.status_code != 200:
            logger.error(f"Erro ao enviar botões: {response.text}")
        else:
            logger.info("Mensagem com botões enviada com sucesso")
    
    return state
```

### **2. Processar Resposta do Botão em `nodes/data_extractor.py`**

```python
def extract_data(state: WorkflowState) -> WorkflowState:
    """Extrai dados do webhook, incluindo respostas de botões."""
    
    body = state["raw_body"]
    entry = body.get("entry", [{}])[0]
    changes = entry.get("changes", [{}])[0]
    value = changes.get("value", {})
    messages = value.get("messages", [])
    
    if not messages:
        return state
    
    message = messages[0]
    msg_type = message.get("type", "")
    
    # ... código existente para outros tipos ...
    
    # Processar resposta de botão interativo
    if msg_type == "interactive":
        interactive = message.get("interactive", {})
        interactive_type = interactive.get("type", "")
        
        if interactive_type == "button_reply":
            button_reply = interactive.get("button_reply", {})
            button_id = button_reply.get("id", "")
            button_title = button_reply.get("title", "")
            
            # Armazenar a escolha do usuário
            state["button_response"] = button_id
            state["mensagem"] = button_title  # Ou processar de outra forma
            
            logger.info(f"Usuário clicou no botão: {button_id}")
    
    return state
```

### **3. Adicionar Lógica de Roteamento**

Criar função para decidir o que fazer baseado na resposta:

```python
def route_button_response(state: WorkflowState) -> str:
    """Roteia baseado na resposta do botão."""
    
    button_id = state.get("button_response", "")
    
    if button_id == "verify_yes":
        return "process_verification"  # Continuar com fact-checking
    elif button_id == "verify_no":
        return "send_goodbye_message"  # Agradecer e finalizar
    else:
        return "handle_unknown"  # Mensagem padrão
```

---

## ⚠️ Limitações e Restrições

### **1. Limite de Botões**
- **Máximo 3 botões** por mensagem
- Se precisar de mais opções, use **Interactive List Messages** (até 10 opções)

### **2. Tamanho do Texto**
- **Título do botão**: Máximo 20 caracteres
- **Corpo da mensagem**: Máximo 1024 caracteres
- **Rodapé**: Máximo 60 caracteres

### **3. Janela de Atendimento (24 horas)**
- Mensagens interativas **só podem ser enviadas dentro da janela de 24 horas**
- Fora da janela, precisa usar **Template Messages** (que precisam de aprovação prévia)

### **4. Rate Limits**
- Mesmos limites da API: ~80 mensagens/segundo por número
- Limite de 1 mensagem a cada 6 segundos para o **mesmo usuário**

---

## 🎯 Diferenças: Conta Normal vs OBA (Official Business Account)

| Recurso | Conta Normal | Conta OBA |
|---------|--------------|-----------|
| **Botões Interativos** | ✅ Disponível | ✅ Disponível |
| **Listas Interativas** | ✅ Disponível | ✅ Disponível |
| **WhatsApp Flows** | ✅ Disponível | ✅ Disponível |
| **Selo Verde** | ❌ Não | ✅ Sim |
| **Throughput Maior** | Até 80 msg/s | Até 1000 msg/s |
| **Prioridade de Entrega** | Normal | Alta |

### **O que é OBA?**

**OBA (Official Business Account)** é um status especial concedido pela Meta para empresas verificadas. **Vantagens:**
- Selo verde ao lado do nome da empresa
- Maior limite de throughput
- Prioridade na entrega de mensagens
- Mais credibilidade com usuários

**Requisitos para OBA:**
- Business Portfolio verificado
- Histórico consistente de mensagens de alta qualidade
- Display name aprovado e único
- Não há solicitação manual - é concedido automaticamente pela Meta

**Importante:** Você **NÃO precisa de OBA** para usar botões interativos! Funciona com qualquer conta do WhatsApp Business Cloud API.

---

## 📊 Exemplo Completo de Fluxo

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Usuário envia mensagem: "Fulano roubou R$ 1 milhão"     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. Bot processa e envia botões:                            │
│    "Gostaria que eu verifique essa informação?"            │
│    [ ✅ Sim, verificar ]  [ ❌ Não, obrigado ]            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Usuário clica em "✅ Sim, verificar"                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Webhook recebe:                                          │
│    {                                                        │
│      "type": "interactive",                                 │
│      "interactive": {                                       │
│        "button_reply": {                                    │
│          "id": "verify_yes",                                │
│          "title": "✅ Sim, verificar"                      │
│        }                                                    │
│      }                                                      │
│    }                                                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ 5. Bot executa fact-checking e envia resultado             │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Próximos Passos

1. **Implementar função de envio de botões** em `nodes/response_sender.py`
2. **Adicionar processamento de respostas** em `nodes/data_extractor.py`
3. **Criar lógica de roteamento** baseada na escolha do usuário
4. **Testar localmente** com ngrok + WhatsApp test number
5. **Deploy** no EasyPanel

---

## 📚 Documentação Oficial

- [Interactive Reply Buttons](https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages)
- [Interactive List Messages](https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages)
- [WhatsApp Flows](https://developers.facebook.com/docs/whatsapp/flows)
- [Sending Messages Guide](https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages)
- [Webhooks Overview](https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/components)

---

## ✅ Checklist de Implementação

- [ ] Criar função `send_interactive_button_message()` em `response_sender.py`
- [ ] Adicionar processamento de `type: "interactive"` em `data_extractor.py`
- [ ] Criar função de roteamento `route_button_response()`
- [ ] Adicionar campo `button_response` no `WorkflowState` (`state.py`)
- [ ] Integrar no grafo (`graph.py`)
- [ ] Testar com número de teste do WhatsApp
- [ ] Deploy e teste em produção

---

**🎉 Conclusão**: Sim, você pode adicionar botões interativos sem precisar de conta OBA! A implementação é simples e funciona perfeitamente com a WhatsApp Cloud API.
