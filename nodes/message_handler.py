"""Gerenciador de mensagens com debounce, classificação Gemini e histórico de chat.

Responsabilidades:
1. Debounce de 1 segundo — acumula mensagens antes de classificar
2. Classificação via Gemini — decide se a mensagem é para verificar ou responder
3. Se for responder — envia segunda requisição ao Gemini com histórico de chat
4. Se for verificar — retorna controle ao pipeline existente (LangGraph)
5. Interrupção — se o usuário enviar nova mensagem durante processamento,
   cancela o fluxo atual e recomeça com todas as mensagens acumuladas

Redis keys:
- pending_msgs:{phone}   — Lista JSON de mensagens aguardando classificação (TTL 60s)
- chat_history:{phone}   — Lista JSON de mensagens (user+bot) dos últimos 5 min (TTL 300s)
- processing:{phone}     — Flag indicando processamento em andamento (TTL 120s)
- debounce_version:{phone} — Contador de versão do debounce para detectar interrupção (TTL 120s)
"""

import asyncio
import json
import logging
import time

import config

logger = logging.getLogger(__name__)

# ── Constantes ──
_DEBOUNCE_SECONDS = 1.0
_PENDING_TTL = 60          # 60s TTL para lista de mensagens pendentes
_CHAT_HISTORY_TTL = 300    # 5 min TTL para histórico de chat
_PROCESSING_TTL = 120      # 2 min TTL para flag de processamento
_GEMINI_CALL_TIMEOUT = 60  # 60s timeout para chamadas ao Gemini classifier/chat

# ── Redis client (lazy singleton) ──
_redis_client = None


async def _get_redis():
    """Retorna client Redis async (singleton lazy)."""
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis_client


async def close_redis():
    """Fecha a conexão Redis (chamado no shutdown)."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ═══════════════════════════════════════════════════════════════════
#  Redis helpers — Pending Messages (Item 1)
# ═══════════════════════════════════════════════════════════════════

def _pending_key(phone: str) -> str:
    return f"pending_msgs:{phone}"


def _debounce_version_key(phone: str) -> str:
    return f"debounce_version:{phone}"


def _processing_key(phone: str) -> str:
    return f"processing:{phone}"


async def _add_pending_message(phone: str, msg_data: dict) -> None:
    """Adiciona mensagem à lista de pendentes no Redis."""
    r = await _get_redis()
    key = _pending_key(phone)
    await r.rpush(key, json.dumps(msg_data, ensure_ascii=False))
    await r.expire(key, _PENDING_TTL)


async def _get_pending_messages(phone: str) -> list[dict]:
    """Recupera todas as mensagens pendentes do Redis."""
    r = await _get_redis()
    key = _pending_key(phone)
    items = await r.lrange(key, 0, -1)
    return [json.loads(item) for item in items]


async def _clear_pending_messages(phone: str) -> None:
    """Limpa as mensagens pendentes do Redis."""
    r = await _get_redis()
    await r.delete(_pending_key(phone))


async def _increment_debounce_version(phone: str) -> int:
    """Incrementa e retorna a versão do debounce (para detectar interrupção)."""
    r = await _get_redis()
    key = _debounce_version_key(phone)
    version = await r.incr(key)
    await r.expire(key, _PROCESSING_TTL)  # TTL alinhado com processing para evitar expiração prematura
    return version


async def _get_debounce_version(phone: str) -> int:
    """Retorna a versão atual do debounce."""
    r = await _get_redis()
    val = await r.get(_debounce_version_key(phone))
    return int(val) if val else 0


async def _set_processing(phone: str, value: str = "1") -> bool:
    """Marca que o processamento está em andamento para este telefone.

    Usa SET NX (set if not exists) para evitar race conditions.
    Retorna True se conseguiu marcar (ninguém estava processando),
    False se já havia processamento em andamento.
    """
    r = await _get_redis()
    result = await r.set(_processing_key(phone), value, ex=_PROCESSING_TTL, nx=True)
    return result is not None


async def _clear_processing(phone: str) -> None:
    """Limpa a flag de processamento."""
    r = await _get_redis()
    await r.delete(_processing_key(phone))


async def _is_processing(phone: str) -> bool:
    """Verifica se há processamento em andamento para este telefone."""
    r = await _get_redis()
    return await r.exists(_processing_key(phone)) > 0


# ═══════════════════════════════════════════════════════════════════
#  Redis helpers — Chat History (Item 2)
# ═══════════════════════════════════════════════════════════════════

def _chat_history_key(phone: str) -> str:
    return f"chat_history:{phone}"


async def _add_to_chat_history(phone: str, role: str, content: str) -> None:
    """Adiciona mensagem ao histórico de chat (user ou bot)."""
    r = await _get_redis()
    key = _chat_history_key(phone)
    entry = json.dumps({
        "role": role,
        "content": content,
        "timestamp": time.time(),
    }, ensure_ascii=False)
    await r.rpush(key, entry)
    await r.expire(key, _CHAT_HISTORY_TTL)


async def _get_chat_history(phone: str) -> list[dict]:
    """Recupera o histórico de chat dos últimos 5 minutos."""
    r = await _get_redis()
    key = _chat_history_key(phone)
    items = await r.lrange(key, 0, -1)
    now = time.time()
    history = []
    for item in items:
        entry = json.loads(item)
        # Filtrar mensagens mais antigas que 5 minutos
        if now - entry.get("timestamp", 0) <= _CHAT_HISTORY_TTL:
            history.append(entry)
    return history


# ═══════════════════════════════════════════════════════════════════
#  Gemini — Classificação de mensagem
# ═══════════════════════════════════════════════════════════════════

_CLASSIFIER_PROMPT = """Você é um assistente de um bot de verificação de fake news chamado "Tá Certo Isso? AI".

Sua tarefa é analisar a(s) mensagem(ns) do usuário e decidir se ele quer VERIFICAR uma informação/notícia ou se está apenas CONVERSANDO.

VERIFICAR — O usuário enviou:
- Uma notícia, afirmação, rumor ou informação que pode ser verdadeira ou falsa
- Um link para uma matéria/notícia
- Um texto encaminhado de outra pessoa
- Uma pergunta do tipo "isso é verdade?" sobre algum fato
- Qualquer conteúdo que contenha uma alegação verificável

CONVERSAR — O usuário enviou:
- Uma saudação (oi, olá, bom dia, etc.)
- Uma pergunta sobre o bot (como funciona, o que faz, etc.)
- Um agradecimento ou feedback
- Uma conversa casual que não contém informação verificável
- Uma pergunta genérica que não envolve checagem de fatos
- Uma reclamação ou sugestão

Responda APENAS com uma única palavra: VERIFICAR ou CONVERSAR

Mensagem(ns) do usuário:
{messages}"""


_CHAT_PROMPT = """Você é o "Tá Certo Isso? AI", um bot de verificação de fake news no WhatsApp.
Você é simpático, breve e direto nas respostas.

Sobre você:
O "Tá Certo Isso? AI" é uma iniciativa que nasceu em outubro de 2024 por alunos da Universidade Federal de Itajubá (UNIFEI) que utiliza inteligência artificial para combater a desinformação. Através da nossa plataforma online e chatbot no WhatsApp, qualquer pessoa pode verificar a veracidade de informações, combatendo fake news de forma rápida e acessível. A plataforma está disponível em tacertoissoai.com.br. O Instagram do projeto é @tacertoisso.ai.

Seu objetivo principal é verificar informações, notícias e conteúdos enviados pelos usuários para combater desinformação.

Regras:
1. Responda de forma breve e amigável.
2. Se o usuário pedir para verificar algo que ele MENCIONOU mas não ENVIOU como conteúdo verificável, peça para ele enviar novamente a informação completa para que você possa verificar. Exemplo: se o usuário disser "verifica aquela notícia que te mandei", diga que ele precisa enviar novamente o conteúdo para verificação.
3. Explique brevemente o que você faz quando perguntado: verificar notícias, imagens, áudios e vídeos contra fake news.
4. Não invente informações. Se não souber, diga que não sabe.
5. Mantenha as respostas curtas (máximo 2-3 frases).
6. Use emojis com moderação para ser amigável.

Histórico recente da conversa:
{history}

Mensagem(ns) atual(is) do usuário:
{messages}"""


async def _call_gemini_classifier(messages_text: str) -> str:
    """Chama o Gemini para classificar a mensagem como VERIFICAR ou CONVERSAR."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_GEMINI_API_KEY)
    prompt = _CLASSIFIER_PROMPT.format(messages=messages_text)

    def _call():
        return client.models.generate_content(
            model=config.GEMINI_CLASSIFIER_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=10,
            ),
        )

    response = await asyncio.wait_for(
        asyncio.to_thread(_call), timeout=_GEMINI_CALL_TIMEOUT
    )
    result = (response.text or "").strip().upper()
    logger.info("[classifier] Gemini respondeu: '%s'", result)
    return result


async def _call_gemini_chat(messages_text: str, history_text: str) -> str:
    """Chama o Gemini para gerar uma resposta de conversa."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GOOGLE_GEMINI_API_KEY)
    prompt = _CHAT_PROMPT.format(messages=messages_text, history=history_text)

    def _call():
        return client.models.generate_content(
            model=config.GEMINI_CHAT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=256,
            ),
        )

    response = await asyncio.wait_for(
        asyncio.to_thread(_call), timeout=_GEMINI_CALL_TIMEOUT
    )
    return (response.text or "").strip()


# ═══════════════════════════════════════════════════════════════════
#  Formatação de mensagens para prompts
# ═══════════════════════════════════════════════════════════════════

def _format_pending_for_prompt(pending: list[dict]) -> str:
    """Formata mensagens pendentes para o prompt do Gemini."""
    parts = []
    for msg in pending:
        msg_type = msg.get("type", "text")
        # Tipos de texto: text, interactive, button — todos têm campo "text"
        text = msg.get("text", "")
        if text:
            parts.append(text)
        elif msg_type not in ("text", "interactive", "button"):
            # Mídia — apenas indicar o tipo
            parts.append(f"[{msg_type} enviado pelo usuário]")
    return "\n".join(parts) if parts else "(mensagem vazia)"


def _format_history_for_prompt(history: list[dict]) -> str:
    """Formata o histórico de chat para o prompt do Gemini."""
    if not history:
        return "(sem histórico recente)"
    parts = []
    for entry in history:
        role = "Usuário" if entry["role"] == "user" else "Bot"
        content = entry.get("content", "")
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
#  Handler principal — chamado pelo main.py para cada mensagem
# ═══════════════════════════════════════════════════════════════════

async def handle_incoming_message(
    phone: str,
    msg_id: str,
    msg_type: str,
    text: str,
    media_id: str,
    caption: str,
    name: str,
    isolated_body: dict,
    process_message_callback,
) -> None:
    """Ponto de entrada para processar uma mensagem recebida.

    Para mensagens de mídia (imagem, vídeo, áudio, sticker) → vai direto
    para o pipeline de verificação, sem classificação do Gemini.

    Para mensagens de texto → acumula com debounce de 1s, classifica com
    Gemini, e decide se verifica ou responde.

    Args:
        phone: Número do remetente
        msg_id: ID da mensagem (wamid)
        msg_type: Tipo da mensagem (text, image, audio, video, sticker, etc.)
        text: Texto da mensagem (para text, interactive, button)
        media_id: ID da mídia (para audio, image, video, sticker)
        caption: Legenda (para image, video)
        name: Nome do remetente
        isolated_body: Body isolado do webhook para passar ao LangGraph
        process_message_callback: Função async para processar via LangGraph
    """
    # Tipos de mídia que vão direto para verificação (sem classificação)
    # Inclui document que será tratado como "não suportado" pelo pipeline
    media_types = {"image", "video", "audio", "sticker", "document"}

    if msg_type in media_types:
        # Mídia → direto para verificação
        logger.info("[handler] Mídia (%s) de %s → verificação direta", msg_type, phone[-4:])
        # Salvar no histórico de chat (best-effort)
        try:
            media_desc = f"[{msg_type} enviado]"
            if caption:
                media_desc += f" Legenda: {caption}"
            await _add_to_chat_history(phone, "user", media_desc)
        except Exception:
            logger.warning("[handler] Falha ao salvar mídia no histórico de chat")
        # Processar via LangGraph (pipeline existente)
        await process_message_callback(isolated_body, msg_id, phone)
        return

    # Texto → acumular com debounce e classificar
    msg_data = {
        "type": msg_type,
        "text": text,
        "msg_id": msg_id,
        "media_id": media_id,
        "caption": caption,
        "name": name,
        "timestamp": time.time(),
        "isolated_body": isolated_body,
    }

    try:
        # Adicionar à lista de pendentes
        await _add_pending_message(phone, msg_data)
        # Salvar no histórico de chat
        if text:
            await _add_to_chat_history(phone, "user", text)

        # Incrementar versão do debounce (invalida qualquer debounce anterior)
        version = await _increment_debounce_version(phone)
        logger.info("[handler] Texto de %s adicionado (debounce v%d)", phone[-4:], version)

        # Se já há um processamento em andamento (classificação ou resposta Gemini),
        # a nova mensagem já foi adicionada ao Redis. O loop de classificação
        # vai detectar a interrupção na próxima verificação de versão.
        if await _is_processing(phone):
            logger.info("[handler] Processamento já em andamento para %s, mensagem acumulada", phone[-4:])
            return

        # Iniciar o ciclo de debounce → classificação
        await _debounce_and_classify(phone, version, process_message_callback)
    except Exception:
        logger.exception("[handler] Erro no handler de mensagem, processando via pipeline direto")
        # Fallback: se Redis falhar, processar diretamente pelo pipeline
        await process_message_callback(isolated_body, msg_id, phone)


async def _debounce_and_classify(
    phone: str,
    version: int,
    process_message_callback,
) -> None:
    """Aguarda o debounce de 1 segundo, depois classifica as mensagens acumuladas.

    Se durante o debounce o usuário enviar mais mensagens, a versão muda
    e este debounce é invalidado (o novo handle_incoming_message inicia outro).
    """
    # Aguardar 1 segundo de silêncio
    await asyncio.sleep(_DEBOUNCE_SECONDS)

    # Verificar se a versão mudou (usuário enviou outra mensagem durante o debounce)
    current_version = await _get_debounce_version(phone)
    if current_version != version:
        logger.info(
            "[debounce] Versão mudou (%d→%d) para %s, abortando debounce antigo",
            version, current_version, phone[-4:],
        )
        return

    # Tentar marcar processamento em andamento (atômico com SETNX)
    acquired = await _set_processing(phone)
    if not acquired:
        # Outra task já está processando — as mensagens já foram adicionadas ao Redis
        logger.info("[debounce] Outra task já processando para %s, abortando", phone[-4:])
        return

    try:
        await _classify_and_act(phone, process_message_callback)
    finally:
        await _clear_processing(phone)


async def _classify_and_act(
    phone: str,
    process_message_callback,
) -> None:
    """Classifica as mensagens pendentes e executa a ação (verificar ou responder).

    Contém o loop de interrupção: se durante a chamada ao Gemini o usuário
    enviar nova mensagem, recomeça a classificação com todas as mensagens.
    """
    from nodes import whatsapp_api

    max_retries = 10  # Limite de re-classificações para evitar loop infinito

    for attempt in range(max_retries):
        # Capturar versão ANTES da chamada ao Gemini
        version_before = await _get_debounce_version(phone)

        # Recuperar mensagens pendentes
        pending = await _get_pending_messages(phone)
        if not pending:
            logger.info("[classify] Sem mensagens pendentes para %s", phone[-4:])
            return

        messages_text = _format_pending_for_prompt(pending)
        logger.info(
            "[classify] Classificando %d mensagem(ns) de %s (tentativa %d)",
            len(pending), phone[-4:], attempt + 1,
        )

        # Verificar se há alguma mensagem com mídia marcada como pendente
        # (não deveria acontecer pois mídias vão direto, mas por segurança)
        has_media = any(
            m.get("type") in ("image", "video", "audio", "sticker")
            for m in pending
        )
        if has_media:
            # Se houver mídia misturada, tratar como verificação
            logger.info("[classify] Mídia detectada nas pendentes → VERIFICAR")
            classification = "VERIFICAR"
        else:
            # Chamar Gemini para classificar
            try:
                classification = await _call_gemini_classifier(messages_text)
            except Exception:
                logger.exception("[classify] Erro ao chamar Gemini classifier")
                # Em caso de erro, assumir verificação (comportamento padrão)
                classification = "VERIFICAR"

        # Verificar se o usuário enviou nova mensagem durante a classificação
        version_after = await _get_debounce_version(phone)
        if version_after != version_before:
            logger.info(
                "[classify] Interrupção detectada (v%d→v%d) para %s, aguardando debounce e reclassificando...",
                version_before, version_after, phone[-4:],
            )
            # Esperar o debounce da nova mensagem
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            # Verificar se mais mensagens chegaram durante o sleep
            version_now = await _get_debounce_version(phone)
            if version_now != version_after:
                # Mais mensagens chegaram, repetir o sleep
                continue
            # Reclassificar com todas as mensagens acumuladas
            continue

        # Classificação concluída sem interrupção
        if "VERIFICAR" in classification:
            # Verificar — usar o pipeline existente
            logger.info("[classify] Decisão: VERIFICAR para %s", phone[-4:])
            await _handle_verify(phone, pending, process_message_callback)
            return
        else:
            # Conversar — gerar resposta com Gemini
            logger.info("[classify] Decisão: CONVERSAR para %s", phone[-4:])

            # Capturar versão antes da chamada de resposta
            version_before_chat = await _get_debounce_version(phone)

            # Ativar typing indicator enquanto o Gemini gera a resposta
            last_msg_id = pending[-1].get("msg_id", "") if pending else ""
            if last_msg_id:
                try:
                    await whatsapp_api.send_typing_indicator(last_msg_id)
                except Exception:
                    pass

            try:
                history = await _get_chat_history(phone)
                history_text = _format_history_for_prompt(history)
                response_text = await _call_gemini_chat(messages_text, history_text)
            except Exception:
                logger.exception("[classify] Erro ao gerar resposta de chat")
                response_text = (
                    "Desculpe, não consegui processar sua mensagem. "
                    "Se você quer verificar uma informação, envie o conteúdo que eu analiso para você! 📰"
                )

            # Verificar interrupção após a resposta do chat
            version_after_chat = await _get_debounce_version(phone)
            if version_after_chat != version_before_chat:
                logger.info(
                    "[classify] Interrupção durante chat (v%d→v%d) para %s, reclassificando...",
                    version_before_chat, version_after_chat, phone[-4:],
                )
                # Esperar o debounce da nova mensagem
                await asyncio.sleep(_DEBOUNCE_SECONDS)
                continue

            # Enviar resposta ao usuário
            last_msg_id = pending[-1].get("msg_id", "") if pending else ""
            try:
                await whatsapp_api.send_text(
                    phone, response_text, quoted_message_id=last_msg_id or None,
                )
                logger.info("[classify] Resposta de chat enviada para %s", phone[-4:])
            except Exception:
                logger.exception("[classify] Erro ao enviar resposta de chat para %s", phone[-4:])

            # Salvar resposta do bot no histórico
            await _add_to_chat_history(phone, "bot", response_text)

            # Limpar mensagens pendentes
            await _clear_pending_messages(phone)
            return

    logger.warning("[classify] Máximo de tentativas (%d) atingido para %s", max_retries, phone[-4:])
    # Limpar pendentes para não ficar em estado inconsistente
    await _clear_pending_messages(phone)


async def _handle_verify(
    phone: str,
    pending: list[dict],
    process_message_callback,
) -> None:
    """Processa as mensagens para verificação via pipeline LangGraph existente.

    Usa a última mensagem de texto como mensagem principal para verificação.
    Se houver múltiplas mensagens, concatena-as para verificação.
    """
    # Limpar mensagens pendentes
    await _clear_pending_messages(phone)

    if not pending:
        return

    # Se há apenas 1 mensagem, usar o isolated_body dela diretamente
    if len(pending) == 1:
        msg = pending[0]
        isolated_body = msg.get("isolated_body", {})
        msg_id = msg.get("msg_id", "")
        await process_message_callback(isolated_body, msg_id, phone)
        return

    # Múltiplas mensagens — concatenar textos e usar o body da última mensagem
    # modificando o texto para incluir todas as mensagens
    last_msg = pending[-1]
    isolated_body = last_msg.get("isolated_body", {})
    msg_id = last_msg.get("msg_id", "")

    # Concatenar todos os textos
    all_texts = []
    for msg in pending:
        text = msg.get("text", "")
        if text:
            all_texts.append(text)

    if all_texts and len(all_texts) > 1:
        combined_text = "\n".join(all_texts)
        # Modificar o body para incluir o texto combinado
        try:
            entry = isolated_body["entry"][0]
            change = entry["changes"][0]
            value = change["value"]
            messages = value["messages"]
            if messages:
                msg_obj = messages[0]
                # Se for text, atualizar o body
                if msg_obj.get("type") == "text":
                    msg_obj["text"]["body"] = combined_text
                # Se for interactive ou button, converter para text
                elif msg_obj.get("type") in ("interactive", "button"):
                    msg_obj["type"] = "text"
                    msg_obj["text"] = {"body": combined_text}
                    # Remover campos interactive/button
                    msg_obj.pop("interactive", None)
                    msg_obj.pop("button", None)
        except (KeyError, IndexError):
            logger.warning("[verify] Falha ao combinar textos no body, usando body original")

    await process_message_callback(isolated_body, msg_id, phone)


async def save_bot_response_to_history(phone: str, response: str) -> None:
    """Salva uma resposta do bot no histórico de chat.

    Chamado pelos nós de resposta (send_rationale_text, etc.) para manter
    o histórico atualizado com as respostas do pipeline de verificação.
    """
    if phone and response:
        try:
            await _add_to_chat_history(phone, "bot", response)
        except Exception:
            logger.warning("[history] Falha ao salvar resposta do bot no histórico")
