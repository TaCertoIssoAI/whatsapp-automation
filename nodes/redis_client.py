"""Cliente Redis para persistência de dados do usuário.

Chaves Redis:
- user:{phone}:registered        → "1" (permanente) — usuário já visto
- user:{phone}:terms_accepted    → "yes" / "no" (permanente) — aceitou termos
- user:{phone}:pending_messages  → lista JSON de mensagens pendentes (temporário)
- user:{phone}:chat_history      → sorted set (score=timestamp) — histórico 5 min
- user:{phone}:debounce_lock     → "1" com TTL curto — lock de debounce
"""

import json
import logging
import time

import redis.asyncio as aioredis

import config

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Retorna a conexão Redis (singleton assíncrono)."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            config.REDIS_URL,
            decode_responses=True,
        )
    return _redis


# ──────────────────────── Registro de usuário ────────────────────────


async def is_user_registered(phone: str) -> bool:
    """Verifica se o número de telefone já está registrado no Redis."""
    r = await get_redis()
    return await r.exists(f"user:{phone}:registered") == 1


async def register_user(phone: str) -> None:
    """Registra o número de telefone no Redis (permanente)."""
    r = await get_redis()
    await r.set(f"user:{phone}:registered", "1")
    logger.info("Usuário %s registrado no Redis", phone)


async def unregister_user(phone: str) -> None:
    """Remove todos os dados do usuário do Redis."""
    r = await get_redis()
    keys = [
        f"user:{phone}:registered",
        f"user:{phone}:terms_accepted",
        f"user:{phone}:pending_messages",
        f"user:{phone}:chat_history",
        f"user:{phone}:debounce_lock",
    ]
    await r.delete(*keys)
    logger.info("Dados do usuário %s removidos do Redis", phone)


# ──────────────────────── Termos e condições ────────────────────────


async def get_terms_status(phone: str) -> str | None:
    """Retorna o status de aceitação dos termos ('yes', 'no', ou None)."""
    r = await get_redis()
    return await r.get(f"user:{phone}:terms_accepted")


async def set_terms_status(phone: str, accepted: bool) -> None:
    """Salva o status de aceitação dos termos (permanente)."""
    r = await get_redis()
    value = "yes" if accepted else "no"
    await r.set(f"user:{phone}:terms_accepted", value)
    logger.info("Termos do usuário %s: %s", phone, value)


# ──────────────────────── Mensagens pendentes (debounce/classificação) ────────────────────────


async def add_pending_message(phone: str, message_data: dict) -> None:
    """Adiciona uma mensagem à lista de mensagens pendentes do usuário."""
    r = await get_redis()
    await r.rpush(f"user:{phone}:pending_messages", json.dumps(message_data))
    # TTL de segurança para não acumular lixo (5 minutos)
    await r.expire(f"user:{phone}:pending_messages", 300)


async def get_pending_messages(phone: str) -> list[dict]:
    """Retorna todas as mensagens pendentes do usuário."""
    r = await get_redis()
    raw_messages = await r.lrange(f"user:{phone}:pending_messages", 0, -1)
    return [json.loads(msg) for msg in raw_messages]


async def get_and_clear_pending_messages(phone: str) -> list[dict]:
    """Retorna e limpa atomicamente todas as mensagens pendentes.

    Usa uma pipeline Redis para garantir que nenhuma mensagem seja perdida
    entre o get e o clear (evita race conditions).
    """
    r = await get_redis()
    key = f"user:{phone}:pending_messages"
    async with r.pipeline(transaction=True) as pipe:
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()
    raw_messages = results[0]
    return [json.loads(msg) for msg in raw_messages]


async def clear_pending_messages(phone: str) -> None:
    """Limpa todas as mensagens pendentes do usuário."""
    r = await get_redis()
    await r.delete(f"user:{phone}:pending_messages")


async def get_pending_message_count(phone: str) -> int:
    """Retorna o número de mensagens pendentes."""
    r = await get_redis()
    return await r.llen(f"user:{phone}:pending_messages")


# ──────────────────────── Debounce ────────────────────────


async def set_debounce_lock(phone: str, lock_id: str) -> None:
    """Define o lock de debounce com um ID único. O valor é o ID do lock."""
    r = await get_redis()
    # TTL de 5 segundos de segurança (muito maior que o debounce de 1s)
    await r.set(f"user:{phone}:debounce_lock", lock_id, ex=5)


async def get_debounce_lock(phone: str) -> str | None:
    """Retorna o ID do lock de debounce atual."""
    r = await get_redis()
    return await r.get(f"user:{phone}:debounce_lock")


async def clear_debounce_lock(phone: str) -> None:
    """Remove o lock de debounce."""
    r = await get_redis()
    await r.delete(f"user:{phone}:debounce_lock")


# ──────────────────────── Histórico de chat (5 minutos) ────────────────────────


async def add_chat_message(phone: str, role: str, content: str) -> None:
    """Adiciona uma mensagem ao histórico de chat com timestamp.

    Args:
        phone: Número do telefone.
        role: 'user' ou 'bot'.
        content: Conteúdo da mensagem.
    """
    r = await get_redis()
    now = time.time()
    entry = json.dumps({"role": role, "content": content, "timestamp": now})
    key = f"user:{phone}:chat_history"

    # Adicionar ao sorted set com score = timestamp
    await r.zadd(key, {entry: now})

    # Remover entradas mais antigas que 5 minutos
    cutoff = now - config.CHAT_HISTORY_TTL_SECONDS
    await r.zremrangebyscore(key, "-inf", cutoff)

    # TTL de segurança na chave (6 minutos)
    await r.expire(key, config.CHAT_HISTORY_TTL_SECONDS + 60)


async def get_chat_history(phone: str) -> list[dict]:
    """Retorna o histórico de chat dos últimos 5 minutos, ordenado cronologicamente."""
    r = await get_redis()
    now = time.time()
    cutoff = now - config.CHAT_HISTORY_TTL_SECONDS
    key = f"user:{phone}:chat_history"

    # Remover entradas expiradas
    await r.zremrangebyscore(key, "-inf", cutoff)

    # Buscar todas as entradas restantes
    entries = await r.zrangebyscore(key, cutoff, "+inf")
    return [json.loads(entry) for entry in entries]


async def clear_chat_history(phone: str) -> None:
    """Limpa o histórico de chat do usuário."""
    r = await get_redis()
    await r.delete(f"user:{phone}:chat_history")
