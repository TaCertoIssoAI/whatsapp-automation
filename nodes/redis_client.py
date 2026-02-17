"""Cliente Redis assíncrono para persistência de dados do usuário.

Chaves Redis:
- user:{phone}:registered        → "1" (permanente)
- user:{phone}:terms_accepted    → "yes" / "no" (permanente)
- user:{phone}:pending_messages  → lista JSON (TTL 5 min)
- user:{phone}:chat_history      → sorted set (TTL 5 min)
- user:{phone}:debounce_lock     → UUID (TTL 5s)
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

import config

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_redis_lock = asyncio.Lock()


async def get_redis() -> aioredis.Redis:
    """Retorna a conexão Redis (singleton thread-safe via asyncio.Lock)."""
    global _redis
    if _redis is None:
        async with _redis_lock:
            if _redis is None:
                _redis = aioredis.from_url(
                    config.REDIS_URL,
                    decode_responses=True,
                    max_connections=20,
                )
    return _redis


# ──────────────────────── Registro de usuário ────────────────────────


async def is_user_registered(phone: str) -> bool:
    r = await get_redis()
    return await r.exists(f"user:{phone}:registered") == 1


async def register_user_with_terms(phone: str) -> None:
    """Registra o usuário e define termos como 'no' atomicamente via pipeline."""
    r = await get_redis()
    async with r.pipeline(transaction=True) as pipe:
        pipe.set(f"user:{phone}:registered", "1")
        pipe.set(f"user:{phone}:terms_accepted", "no")
        await pipe.execute()


async def register_user(phone: str) -> None:
    r = await get_redis()
    await r.set(f"user:{phone}:registered", "1")


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


# ──────────────────────── Termos e condições ────────────────────────


async def get_terms_status(phone: str) -> str | None:
    r = await get_redis()
    return await r.get(f"user:{phone}:terms_accepted")


async def set_terms_status(phone: str, accepted: bool) -> None:
    r = await get_redis()
    await r.set(f"user:{phone}:terms_accepted", "yes" if accepted else "no")


# ──────────────────────── Mensagens pendentes ────────────────────────


async def add_pending_message(phone: str, message_data: dict) -> None:
    r = await get_redis()
    key = f"user:{phone}:pending_messages"
    async with r.pipeline(transaction=False) as pipe:
        pipe.rpush(key, json.dumps(message_data))
        pipe.expire(key, 300)
        await pipe.execute()


async def get_and_clear_pending_messages(phone: str) -> list[dict]:
    """Retorna e limpa atomicamente todas as mensagens pendentes."""
    r = await get_redis()
    key = f"user:{phone}:pending_messages"
    async with r.pipeline(transaction=True) as pipe:
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        results = await pipe.execute()
    return [json.loads(msg) for msg in results[0]]


async def get_pending_message_count(phone: str) -> int:
    r = await get_redis()
    return await r.llen(f"user:{phone}:pending_messages")


# ──────────────────────── Debounce ────────────────────────


async def set_debounce_lock(phone: str, lock_id: str) -> None:
    r = await get_redis()
    await r.set(f"user:{phone}:debounce_lock", lock_id, ex=5)


async def get_debounce_lock(phone: str) -> str | None:
    r = await get_redis()
    return await r.get(f"user:{phone}:debounce_lock")


async def clear_debounce_lock(phone: str) -> None:
    r = await get_redis()
    await r.delete(f"user:{phone}:debounce_lock")


# ──────────────────────── Histórico de chat ────────────────────────


async def add_chat_message(phone: str, role: str, content: str) -> None:
    """Adiciona mensagem ao histórico com cleanup de entradas expiradas."""
    r = await get_redis()
    now = time.time()
    entry = json.dumps({"role": role, "content": content, "timestamp": now})
    key = f"user:{phone}:chat_history"
    cutoff = now - config.CHAT_HISTORY_TTL_SECONDS

    async with r.pipeline(transaction=False) as pipe:
        pipe.zadd(key, {entry: now})
        pipe.zremrangebyscore(key, "-inf", cutoff)
        pipe.expire(key, config.CHAT_HISTORY_TTL_SECONDS + 60)
        await pipe.execute()


async def get_chat_history(phone: str) -> list[dict]:
    """Retorna o histórico de chat dos últimos 5 minutos."""
    r = await get_redis()
    now = time.time()
    cutoff = now - config.CHAT_HISTORY_TTL_SECONDS
    key = f"user:{phone}:chat_history"

    await r.zremrangebyscore(key, "-inf", cutoff)
    entries = await r.zrangebyscore(key, cutoff, "+inf")
    return [json.loads(entry) for entry in entries]
