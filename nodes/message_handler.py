"""Gerenciador de mensagens com debounce, classificação Gemini e histórico de chat.

Responsabilidades:
1. Debounce de 1 segundo — acumula TODAS as mensagens (texto + mídia) antes de agir
2. Após debounce, analisa o batch:
   a. Se tem mídia → identifica o tipo da ÚLTIMA mídia, coleta até 3 mídias
      desse mesmo tipo, processa as extras (download + análise/transcrição),
      concatena resultados + textos/legendas → envia a última mídia via LangGraph
   b. Se só tem texto → classificação via Gemini (VERIFICAR ou CONVERSAR)
3. Se for CONVERSAR → envia resposta com Gemini + histórico de chat
4. Se for VERIFICAR → retorna controle ao pipeline existente (LangGraph)
5. Interrupção — se o usuário enviar nova mensagem durante processamento,
   cancela o fluxo atual e recomeça com todas as mensagens acumuladas
6. Mídias de tipos diferentes do último são ignoradas; se >3 do mesmo tipo
   ou tipos mistos, a mensagem de status informa o usuário

Redis keys:
- pending_msgs:{phone}   — Lista JSON de mensagens aguardando processamento (TTL 60s)
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
    # Pipeline atômico: push + trim + renova TTL
    pipe = r.pipeline()
    pipe.rpush(key, json.dumps(msg_data, ensure_ascii=False))
    pipe.ltrim(key, -_MAX_PENDING_ENTRIES, -1)  # mantém só as últimas 50
    pipe.expire(key, _PENDING_TTL)
    await pipe.execute()


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


# Limite máximo de entradas no histórico (proteção contra listas gigantes)
_MAX_CHAT_HISTORY_ENTRIES = 100
# Limite máximo de mensagens pendentes na fila
_MAX_PENDING_ENTRIES = 50


async def _add_to_chat_history(phone: str, role: str, content: str) -> None:
    """Adiciona mensagem ao histórico de chat (user ou bot)."""
    r = await _get_redis()
    key = _chat_history_key(phone)
    entry = json.dumps({
        "role": role,
        "content": content,
        "timestamp": time.time(),
    }, ensure_ascii=False)
    # Pipeline atômico: push + trim + renova TTL
    pipe = r.pipeline()
    pipe.rpush(key, entry)
    pipe.ltrim(key, -_MAX_CHAT_HISTORY_ENTRIES, -1)  # mantém só as últimas 100
    pipe.expire(key, _CHAT_HISTORY_TTL)
    await pipe.execute()


async def _get_chat_history(phone: str) -> list[dict]:
    """Recupera o histórico de chat dos últimos 5 minutos.

    Filtra entradas mais antigas que _CHAT_HISTORY_TTL em Python
    (o TTL do Redis renova a cada mensagem nova, mas entradas velhas
    dentro da lista podem persistir — filtramos aqui).
    """
    r = await _get_redis()
    key = _chat_history_key(phone)
    items = await r.lrange(key, 0, -1)
    now = time.time()
    history = []
    for item in items:
        try:
            entry = json.loads(item)
        except (json.JSONDecodeError, ValueError):
            continue
        # Manter apenas mensagens dos últimos 5 minutos
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
            # Mídia — indicar o tipo
            caption = msg.get("caption", "")
            if caption:
                parts.append(f"[{msg_type} enviado pelo usuário] Legenda: {caption}")
            else:
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

    TODAS as mensagens (texto e mídia) são acumuladas no Redis com debounce
    de 1 segundo. Após o debounce:

    - Se o batch contém mídia → processa a ÚLTIMA mídia + concatena todos
      os textos/legendas em ordem cronológica → verificação via LangGraph
    - Se o batch é só texto → classifica com Gemini (VERIFICAR/CONVERSAR)

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
    # Montar dados da mensagem para a fila (todos os tipos, incluindo mídia)
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
        # Adicionar à lista de pendentes (texto E mídia)
        await _add_pending_message(phone, msg_data)

        # Salvar no histórico de chat
        media_types = {"image", "video", "audio", "sticker", "document"}
        if msg_type in media_types:
            media_desc = f"[{msg_type} enviado]"
            if caption:
                media_desc += f" Legenda: {caption}"
            await _add_to_chat_history(phone, "user", media_desc)
        elif text:
            await _add_to_chat_history(phone, "user", text)

        # Incrementar versão do debounce (invalida qualquer debounce anterior)
        version = await _increment_debounce_version(phone)
        logger.info("[handler] Msg (%s) de %s adicionada (debounce v%d)", msg_type, phone[-4:], version)

        # Se já há um processamento em andamento, a nova mensagem já foi
        # adicionada ao Redis. O loop de classificação vai detectar a
        # interrupção na próxima verificação de versão.
        if await _is_processing(phone):
            logger.info("[handler] Processamento já em andamento para %s, mensagem acumulada", phone[-4:])
            return

        # Iniciar o ciclo de debounce → processamento
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
    """Analisa as mensagens pendentes e executa a ação apropriada.

    Se o batch contém mídia → processa a última mídia + textos concatenados.
    Se só texto → classifica com Gemini (VERIFICAR/CONVERSAR).

    Contém o loop de interrupção: se durante o processamento o usuário
    enviar nova mensagem, recomeça com todas as mensagens.
    """
    from nodes import whatsapp_api

    media_types = {"image", "video", "audio", "sticker"}
    max_retries = 10  # Limite de re-tentativas para evitar loop infinito

    for attempt in range(max_retries):
        # Capturar versão ANTES do processamento
        version_before = await _get_debounce_version(phone)

        # Recuperar mensagens pendentes
        pending = await _get_pending_messages(phone)
        if not pending:
            logger.info("[classify] Sem mensagens pendentes para %s", phone[-4:])
            return

        logger.info(
            "[classify] Processando batch de %d mensagem(ns) de %s (tentativa %d)",
            len(pending), phone[-4:], attempt + 1,
        )

        # ── Verificar se há mídia no batch ──
        has_media = any(m.get("type") in media_types for m in pending)
        # Document é tratado como "não suportado" — se é a ÚNICA coisa, passa pelo pipeline
        has_only_document = (
            not has_media
            and any(m.get("type") == "document" for m in pending)
            and all(m.get("type") in ("document", "text", "interactive", "button") for m in pending)
        )

        if has_media:
            # ══════════════════════════════════════════
            #  BATCH COM MÍDIA → verificação direta
            # ══════════════════════════════════════════
            logger.info("[classify] Mídia detectada no batch → verificação direta para %s", phone[-4:])
            await _handle_batch_with_media(phone, pending, process_message_callback)
            return

        if has_only_document:
            # ══════════════════════════════════════════
            #  BATCH COM DOCUMENTO (SEM MÍDIA PROCESSÁVEL)
            #  → Passa pelo pipeline para enviar msg de "não suportado"
            # ══════════════════════════════════════════
            logger.info("[classify] Apenas documento(s) no batch → pipeline 'document' para %s", phone[-4:])
            # Usar a última mensagem de documento
            doc_msg = None
            for m in reversed(pending):
                if m.get("type") == "document":
                    doc_msg = m
                    break
            if doc_msg:
                await _clear_pending_messages(phone)
                isolated_body = doc_msg.get("isolated_body", {})
                msg_id = doc_msg.get("msg_id", "")
                await process_message_callback(isolated_body, msg_id, phone)
            return

        # ══════════════════════════════════════════
        #  BATCH SÓ TEXTO → classificar com Gemini
        # ══════════════════════════════════════════
        messages_text = _format_pending_for_prompt(pending)

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

            # ── Rate-limit check para CONVERSAR ──
            # O caminho CONVERSAR não passa pelo LangGraph, então precisamos
            # verificar e incrementar o contador de rate-limit aqui.
            from nodes.rate_limiter import (
                save_message_count as _rl_save,
                _read_count_without_increment as _rl_read,
                _LIMIT_REACHED_MESSAGE,
                _WELCOME_MESSAGE,
            )

            # Decidir se incrementa ou apenas lê (mesma lógica do batch)
            last_ts = pending[-1].get("timestamp", 0) if pending else 0
            first_ts = pending[0].get("timestamp", 0) if pending else 0
            skip_inc = (last_ts - first_ts < 1.0 and len(pending) > 1)

            if skip_inc:
                rl_result = await _rl_read(phone)
            else:
                # Incrementar contador via Firestore (reusa a lógica existente)
                from nodes.rate_limiter import _hash_phone, _today, _get_firestore_db, _firestore_db, _firestore_initialized, _memory_increment
                phone_hash = _hash_phone(phone)
                today = _today()
                limit = config.DAILY_MESSAGE_LIMIT
                db = None
                try:
                    if not _firestore_initialized:
                        db = await asyncio.to_thread(_get_firestore_db)
                    else:
                        db = _firestore_db
                except Exception:
                    pass
                if db is not None:
                    from nodes.rate_limiter import _save_to_firestore
                    rl_result = await _save_to_firestore(db, phone_hash, phone_hash[:12], today, limit)
                else:
                    count = _memory_increment(phone_hash)
                    rl_result = {"daily_count": count, "is_new_user": False, "is_reset_command": False}

            daily_count = rl_result.get("daily_count", 0)
            is_new_user = rl_result.get("is_new_user", False)
            limit = config.DAILY_MESSAGE_LIMIT

            # Se for usuário novo, enviar mensagem de boas-vindas
            if is_new_user:
                try:
                    await whatsapp_api.send_text(phone, _WELCOME_MESSAGE)
                    await _add_to_chat_history(phone, "bot", _WELCOME_MESSAGE)
                    logger.info("[classify] Welcome enviado para novo usuário (CONVERSAR) %s", phone[-4:])
                except Exception:
                    logger.warning("[classify] Falha ao enviar welcome para %s", phone[-4:])

            # Verificar se atingiu o limite
            if daily_count > limit:
                logger.warning(
                    "[classify] 🚫 CONVERSAR bloqueado por rate-limit: %d/%d para %s",
                    daily_count, limit, phone[-4:],
                )
                try:
                    last_msg_id = pending[-1].get("msg_id", "") if pending else ""
                    await whatsapp_api.send_text(
                        phone, _LIMIT_REACHED_MESSAGE,
                        quoted_message_id=last_msg_id or None,
                    )
                except Exception:
                    logger.warning("[classify] Falha ao enviar aviso de limite para %s", phone[-4:])
                await _clear_pending_messages(phone)
                return

            # ── Gerar resposta de chat ──
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


# Máximo de mídias do mesmo tipo a processar num batch
_MAX_SAME_TYPE_MEDIA = 3


async def _handle_batch_with_media(
    phone: str,
    pending: list[dict],
    process_message_callback,
) -> None:
    """Processa um batch que contém mídia.

    Regras:
    1. Encontra a ÚLTIMA mídia processável (image/video/audio/sticker)
    2. Coleta até 3 mídias DO MESMO TIPO da última mídia (as últimas 3)
    3. Para as mídias extras (além da principal), faz download + análise/
       transcrição AQUI e concatena os resultados no batch_extra_text
    4. Coleta TODOS os textos + legendas em ordem cronológica
    5. Se há mídias de tipos diferentes ou >3 do mesmo tipo, inclui
       aviso na mensagem de status
    6. Usa o isolated_body da última mídia para passar pelo LangGraph
    7. Injeta o texto concatenado como _batch_extra_text no body

    Exemplo: text1, image1, image2, text2, image3, text3
    → Última mídia = image3 (type=image)
    → Mídias do mesmo tipo = [image1, image2, image3] (últimas 3 images)
    → image3 vai pelo LangGraph (pipeline normal)
    → image1 e image2 são analisadas aqui, descrições vão no batch_extra_text
    → batch_extra_text = descrição_image1 + descrição_image2 + text1 + text2 + text3
    """
    media_types = {"image", "video", "audio", "sticker"}

    if not pending:
        return

    # ── Encontrar a ÚLTIMA mídia no batch ──
    last_media_idx = -1
    for i in range(len(pending) - 1, -1, -1):
        if pending[i].get("type") in media_types:
            last_media_idx = i
            break

    if last_media_idx == -1:
        logger.warning("[batch-media] Nenhuma mídia encontrada no batch, usando _handle_verify")
        await _handle_verify(phone, pending, process_message_callback)
        return

    last_media = pending[last_media_idx]
    target_type = last_media.get("type", "")
    # sticker é tratado como image no pipeline
    target_type_normalized = "image" if target_type == "sticker" else target_type
    isolated_body = last_media.get("isolated_body", {})
    msg_id = last_media.get("msg_id", "")

    # ── Coletar todas as mídias do MESMO tipo (normalizado) ──
    same_type_indices = []
    other_media_types = set()
    for i, msg in enumerate(pending):
        mt = msg.get("type", "")
        if mt not in media_types:
            continue
        mt_norm = "image" if mt == "sticker" else mt
        if mt_norm == target_type_normalized:
            same_type_indices.append(i)
        else:
            other_media_types.add(mt)

    # Pegar as últimas _MAX_SAME_TYPE_MEDIA do mesmo tipo
    selected_indices = same_type_indices[-_MAX_SAME_TYPE_MEDIA:]
    skipped_same_type = len(same_type_indices) - len(selected_indices)

    # A última mídia selecionada vai pelo LangGraph; as anteriores são processadas aqui
    # A última mídia selecionada DEVE ser last_media_idx
    extra_media_indices = [i for i in selected_indices if i != last_media_idx]

    logger.info(
        "[batch-media] Tipo alvo: %s, total mesmo tipo: %d, selecionadas: %d, "
        "extras para processar aqui: %d, outros tipos ignorados: %s, para %s",
        target_type_normalized, len(same_type_indices), len(selected_indices),
        len(extra_media_indices), other_media_types or "nenhum", phone[-4:],
    )

    # ── Gerar mensagem de status customizada ──
    status_notes = _build_batch_status_notes(
        target_type_normalized, len(selected_indices),
        skipped_same_type, other_media_types,
    )
    if status_notes:
        isolated_body["_batch_status_notes"] = status_notes

    # ── Processar mídias extras (download + análise/transcrição) ──
    extra_descriptions = await _process_extra_media(
        phone, pending, extra_media_indices, target_type_normalized,
    )

    # ── Coletar textos em ordem cronológica ──
    all_texts = []
    for i, msg in enumerate(pending):
        # Texto direto (text, interactive, button)
        text = msg.get("text", "")
        if text:
            all_texts.append(text)
        # Legenda de mídia (mas NÃO a legenda da última mídia — essa vai no caption do body)
        if i != last_media_idx:
            caption = msg.get("caption", "")
            if caption:
                all_texts.append(caption)

    # Combinar: descrições das mídias extras + textos do usuário
    batch_parts = extra_descriptions + all_texts
    batch_extra_text = "\n".join(batch_parts) if batch_parts else ""

    logger.info(
        "[batch-media] Última mídia: %s (idx %d/%d), extras processadas: %d, "
        "textos: %d, batch_extra_text: %d chars, para %s",
        target_type_normalized, last_media_idx, len(pending) - 1,
        len(extra_descriptions), len(all_texts),
        len(batch_extra_text), phone[-4:],
    )

    # Injetar _batch_extra_text no isolated_body para o data_extractor extrair
    if batch_extra_text:
        isolated_body["_batch_extra_text"] = batch_extra_text

    # Injetar flag de debounce para controle de contagem no Firebase
    # Quando veio de um batch com debounce, NÃO incrementar contador
    # se já passaram <1s desde a última msg do batch
    last_msg_ts = pending[-1].get("timestamp", 0)
    first_msg_ts = pending[0].get("timestamp", 0)
    if last_msg_ts - first_msg_ts < 1.0 and len(pending) > 1:
        isolated_body["_skip_counter_increment"] = True

    # Processar via LangGraph (pipeline existente cuida de rate limit, etc.)
    await process_message_callback(isolated_body, msg_id, phone)

    # Limpar mensagens pendentes APÓS o processamento
    await _clear_pending_messages(phone)


def _build_batch_status_notes(
    target_type: str,
    selected_count: int,
    skipped_same_type: int,
    other_media_types: set[str],
) -> str:
    """Constrói notas para a mensagem de status quando há limitações no batch.

    Retorna string vazia se não há nada especial para informar.
    """
    notes = []
    type_names = {
        "image": "imagens", "video": "vídeos",
        "audio": "áudios", "sticker": "figurinhas",
    }
    target_name = type_names.get(target_type, target_type)

    if skipped_same_type > 0:
        notes.append(
            f"Recebi {selected_count + skipped_same_type} {target_name}, "
            f"mas consigo analisar no máximo {_MAX_SAME_TYPE_MEDIA} por vez. "
            f"Estou analisando as {selected_count} mais recentes."
        )

    if other_media_types:
        ignored_names = [type_names.get(t, t) for t in other_media_types]
        joined = ", ".join(ignored_names)
        notes.append(
            f"Também recebi {joined}, mas vou focar na análise "
            f"das {target_name} que você enviou por último."
        )

    return " ".join(notes)


async def _process_extra_media(
    phone: str,
    pending: list[dict],
    extra_indices: list[int],
    target_type: str,
) -> list[str]:
    """Processa mídias extras (não a principal) fazendo download + análise/transcrição.

    Retorna lista de descrições/transcrições em ordem cronológica.
    """
    if not extra_indices:
        return []

    from nodes import whatsapp_api, ai_services

    descriptions = []
    for media_num, idx in enumerate(extra_indices, start=1):
        msg = pending[idx]
        media_id = msg.get("media_id", "")
        if not media_id:
            continue

        try:
            media_b64 = await whatsapp_api.download_media_as_base64(media_id)
        except Exception:
            logger.warning("[batch-media] Falha ao baixar mídia extra idx=%d para %s", idx, phone[-4:])
            continue

        try:
            if target_type == "audio":
                text = await ai_services.transcribe_audio(media_b64)
                descriptions.append(f"[Transcrição de áudio {media_num}]: {text}")
            elif target_type == "image":
                analysis = await ai_services.analyze_image_content(media_b64)
                descriptions.append(f"[Análise de imagem {media_num}]: {analysis}")
            elif target_type == "video":
                from nodes.media_processor import get_video_duration_from_base64
                try:
                    duration = get_video_duration_from_base64(media_b64)
                except Exception:
                    duration = 0
                if duration >= 120:
                    descriptions.append(f"[Vídeo {media_num}]: Vídeo ignorado (duração > 2 min)")
                    continue
                analysis = await ai_services.analyze_video(media_b64)
                descriptions.append(f"[Análise de vídeo {media_num}]: {analysis}")
        except Exception:
            logger.warning("[batch-media] Falha ao analisar mídia extra idx=%d para %s", idx, phone[-4:])
            continue

    return descriptions


async def _handle_verify(
    phone: str,
    pending: list[dict],
    process_message_callback,
) -> None:
    """Processa mensagens de texto para verificação via pipeline LangGraph.

    Usa a última mensagem de texto como mensagem principal para verificação.
    Se houver múltiplas mensagens, concatena-as para verificação.
    """
    if not pending:
        return

    # Se há apenas 1 mensagem, usar o isolated_body dela diretamente
    if len(pending) == 1:
        msg = pending[0]
        isolated_body = msg.get("isolated_body", {})
        msg_id = msg.get("msg_id", "")
        await process_message_callback(isolated_body, msg_id, phone)
        # Limpar mensagens pendentes APÓS o processamento
        await _clear_pending_messages(phone)
        return

    # Múltiplas mensagens — concatenar textos e usar o body da última mensagem
    # modificando o texto para incluir todas as mensagens
    last_msg = pending[-1]
    isolated_body = last_msg.get("isolated_body", {})
    msg_id = last_msg.get("msg_id", "")

    # Injetar flag de debounce para controle de contagem no Firebase
    last_msg_ts = pending[-1].get("timestamp", 0)
    first_msg_ts = pending[0].get("timestamp", 0)
    if last_msg_ts - first_msg_ts < 1.0:
        isolated_body["_skip_counter_increment"] = True

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

    # Limpar mensagens pendentes APÓS o processamento
    await _clear_pending_messages(phone)


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
