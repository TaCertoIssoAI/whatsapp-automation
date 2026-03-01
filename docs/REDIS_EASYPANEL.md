# 🟥 Redis no EasyPanel

Guia rápido para subir o Redis no mesmo projeto EasyPanel onde o bot já está rodando, e conectar os dois serviços.

---

## 📋 Pré-requisitos

- ✅ Projeto já criado no EasyPanel (seguindo o `DOCKER_EASYPANEL_DEPLOY.md`)
- ✅ Serviço do bot já configurado no projeto

---

## 🚀 Parte 1: Criar o serviço Redis

### 1.1. Entrar no projeto

1. Acesse [EasyPanel](https://easypanel.io/) e faça login
2. Abra o projeto onde o bot está rodando (ex: `whatsapp-bot`)

### 1.2. Adicionar serviço Redis

1. Clique em **"Add Service"**
2. Escolha **"Redis"** (o EasyPanel tem um template pronto — fica na aba de databases/serviços)
3. Configure:
   - **Service Name**: `redis` (use exatamente este nome, fica mais fácil de referenciar)
   - **Password**: defina uma senha forte — você vai precisar dela depois
   - **Port**: `6379` (padrão, pode deixar assim)

4. Clique em **"Create"** / **"Deploy"**

> O EasyPanel vai subir um container Redis oficial (`redis:alpine`) e mantê-lo rodando automaticamente.

---

## 🔗 Parte 2: Conectar o bot ao Redis

### 2.1. Entender o endereço interno

Dentro do EasyPanel, os serviços do **mesmo projeto** se comunicam pelo nome do serviço como hostname. Como você nomeou o serviço de `redis`, o endereço interno é:

```
redis://:<senha>@redis:6379/0
```

> **Por que funciona?** O EasyPanel coloca os serviços do mesmo projeto na mesma rede Docker interna, então eles se enxergam pelo nome.

### 2.2. Configurar a variável de ambiente no bot

1. Vá para o serviço do bot no EasyPanel
2. Acesse a aba **"Environment Variables"**
3. Adicione ou atualize **apenas** a variável:

```
REDIS_URL=redis://:<sua-senha-aqui>@redis-dev:6379/0
```

Exemplo com senha `minhasenhaforte`:
```
REDIS_URL=redis://:minhasenhaforte@redis-dev:6379/0
```

> **Importante:** só o `REDIS_URL` é lido pelo código. Qualquer outra variável como `REDIS_SERVICE_NAME` é ignorada — não é necessário adicioná-la.

4. Salve e faça **Redeploy** do serviço do bot

### 2.3. Verificar a conexão

Depois do redeploy, veja os logs do bot e procure por algo como:

```
INFO  Conectado ao Redis em redis:6379
```

Se aparecer erro de conexão, verifique:
- Se a senha está correta (sem espaços extras)
- Se o nome do serviço Redis é exatamente `redis`
- Se os dois serviços estão no **mesmo projeto** do EasyPanel

---

## 💾 Parte 3: Persistência em disco — não é necessária

O Redis é usado pelo bot apenas para dados **volatãis e de curtssimo prazo**:

| Chave Redis | TTL | O que perde se reiniciar |
|---|---|---|
| `pending_msgs:{phone}` | 60s | Mensagens ainda no debounce (usuário manda de novo) |
| `chat_history:{phone}` | 5 min | Histórico de conversa recente do CONVERSAR |
| `processing:{phone}` | 120s | Lock de processamento (expira sozinho de qualquer forma) |
| `debounce_version:{phone}` | 120s | Contador de versão (reinicia zerado, sem problema) |

Se o Redis reiniciar, o bot **volta a funcionar imediatamente** com estado zerado. O único impacto é que:
- Uma mensagem que estava no debounce no exato momento do restart pode ser perdida (o usuário precisaria mandar de novo)
- O bot perde o contexto de conversa do CONVERSAR (não afeta a verificação de notícias)

**Não configure persistência em disco** — ela adiciona latência e é desnecessária para esse caso de uso.

---

## 🐛 Troubleshooting

### `Connection refused` ou `ECONNREFUSED redis:6379`

- Confirme que o serviço Redis está **rodando** (status verde no EasyPanel)
- Confirme que os dois serviços estão no **mesmo projeto**
- Confirme que o nome do serviço Redis é exatamente o que está na URL (`redis` por padrão)

### `WRONGPASS` ou erro de autenticação

- Verifique a senha na variável `REDIS_URL` — deve ser idêntica à senha configurada no serviço Redis
- A URL deve ter o formato `redis://:<senha>@redis:6379/0` (com os dois-pontos antes da senha)

### Bot inicia mas cai logo depois

- Veja os logs do bot — se Redis não conectar, o bot pode entrar em modo de fallback ou crashar
- Teste subindo o Redis antes do bot (aguarde o status ficar verde)

### Como ver os dados no Redis

Se precisar inspecionar o Redis, você pode usar o **terminal** do EasyPanel:

1. Vá no serviço Redis → aba **"Console"** ou **"Terminal"**
2. Execute:
   ```bash
   redis-cli -a <sua-senha> ping
   # Deve retornar: PONG

   redis-cli -a <sua-senha> keys "*"
   # Lista todas as chaves
   ```

---

## ✨ Resumo

| O que fazer | Onde |
|---|---|
| Criar serviço Redis | EasyPanel → Projeto → Add Service → Redis |
| Definir senha | Na criação do serviço Redis |
| Conectar o bot | Variável `REDIS_URL=redis://:<senha>@<nome-do-serviço>:6379/0` no serviço do bot |
| Redeploy do bot | Após salvar a variável |
| Persistência em disco | Não precisa — todos os dados são volatãis por design |

**É só isso.** O EasyPanel cuida da rede interna automaticamente — não precisa expor porta nenhuma na internet.
