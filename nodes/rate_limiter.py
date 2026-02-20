"""Rate limiter com Firestore + fallback in-memory.

Limite diário de mensagens por usuário. Os telefones são armazenados
como hash SHA-256 para privacidade.

Coleção Firestore: users-whatsapp (database: tacertoissoai)
Campos: lastInteractionDate, dailyMessageCount, totalMessageCount

Se o Firestore não estiver disponível, usa um dicionário in-memory
como fallback para garantir que o rate limit SEMPRE funcione.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timezone

import config
from state import WorkflowState

logger = logging.getLogger(__name__)

# ── Firestore client (lazy singleton) ──
_firestore_db = None
_firestore_initialized = False

_COLLECTION = "users-whatsapp"

_LIMIT_REACHED_MESSAGE = (
    "⚠️ Você atingiu o limite diário de mensagens. "
    "O serviço estará disponível novamente amanhã. Obrigado pela compreensão! 🙏"
)

# ── Fallback in-memory (quando Firestore não está disponível) ──
# { phone_hash: {"date": "YYYY-MM-DD", "count": int} }
_memory_counts: dict[str, dict] = {}


# ═══════════════════════════════════════════════════════════════════
#  Inicialização do Firestore
# ═══════════════════════════════════════════════════════════════════

def _get_firestore_db():
    """Inicializa e retorna o client Firestore (singleton lazy)."""
    global _firestore_db, _firestore_initialized

    if _firestore_initialized:
        return _firestore_db

    _firestore_initialized = True

    cred_path = config.FIREBASE_CREDENTIALS_PATH
    logger.info("[firebase-init] FIREBASE_CREDENTIALS_PATH = '%s'", cred_path)

    if not cred_path:
        logger.warning(
            "[firebase-init] FIREBASE_CREDENTIALS_PATH vazio — "
            "usando fallback in-memory para rate limiting"
        )
        return None

    # Resolver caminho relativo a partir do diretório do projeto
    if not os.path.isabs(cred_path):
        project_dir = os.path.dirname(os.path.dirname(__file__))
        cred_path = os.path.join(project_dir, cred_path)
        logger.info("[firebase-init] Caminho resolvido: %s", cred_path)

    if not os.path.exists(cred_path):
        logger.error(
            "[firebase-init] Arquivo NÃO encontrado: %s — "
            "usando fallback in-memory", cred_path,
        )
        return None

    logger.info("[firebase-init] Arquivo encontrado: %s (size=%d bytes)",
                cred_path, os.path.getsize(cred_path))

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore

        # Evitar inicializar o app mais de uma vez
        try:
            app = firebase_admin.get_app()
            logger.info("[firebase-init] App já inicializado: %s", app.project_id)
        except ValueError:
            cred = credentials.Certificate(cred_path)
            app = firebase_admin.initialize_app(cred)
            logger.info("[firebase-init] App inicializado: project=%s", app.project_id)

        # Usar o banco "tacertoissoai" (não o default)
        _firestore_db = firestore.client(database_id="tacertoissoai")
        logger.info("[firebase-init] ✅ Firestore conectado (database=tacertoissoai)")

        # Teste de conectividade: tentar ler a coleção
        try:
            test_docs = _firestore_db.collection(_COLLECTION).limit(1).get()
            logger.info("[firebase-init] ✅ Teste de leitura OK (docs encontrados: %d)",
                        len(test_docs))
        except Exception as e:
            logger.warning("[firebase-init] ⚠️ Teste de leitura falhou: %s", e)

        return _firestore_db

    except Exception as e:
        logger.exception(
            "[firebase-init] ❌ FALHA ao inicializar Firestore: %s — "
            "usando fallback in-memory", e,
        )
        return None


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _hash_phone(phone: str) -> str:
    """SHA-256 do telefone + salt."""
    salt = config.HASH_SALT
    return hashlib.sha256(f"{phone}{salt}".encode()).hexdigest()


def _today() -> str:
    """Data UTC como YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ═══════════════════════════════════════════════════════════════════
#  Fallback in-memory (quando Firestore não funciona)
# ═══════════════════════════════════════════════════════════════════

def _memory_increment(phone_hash: str) -> int:
    """Incrementa contagem in-memory. Retorna o novo count."""
    today = _today()
    entry = _memory_counts.get(phone_hash)

    if entry is None or entry["date"] != today:
        _memory_counts[phone_hash] = {"date": today, "count": 1}
        return 1

    entry["count"] += 1
    return entry["count"]


# ═══════════════════════════════════════════════════════════════════
#  NÓ 1: save_message_count — roda para TODA mensagem
# ═══════════════════════════════════════════════════════════════════

async def save_message_count(state: WorkflowState) -> WorkflowState:
    """Contabiliza a mensagem. Roda para TODA mensagem recebida.

    Retorna daily_count no state para check_rate_limit usar.
    Se Firestore indisponível, usa fallback in-memory.
    NUNCA retorna daily_count=0 (que causaria bypass do rate limit).
    """
    phone = state.get("numero_quem_enviou", "")
    limit = config.DAILY_MESSAGE_LIMIT

    if not phone:
        logger.warning("[save-count] Sem número de telefone — liberando")
        return {"daily_count": 0}

    phone_hash = _hash_phone(phone)
    doc_id = phone_hash[:12]  # para logs
    today = _today()

    logger.info("[save-count] ══════ INÍCIO ══════")
    logger.info("[save-count] phone_hash=%s…, today=%s, limit=%d", doc_id, today, limit)

    # Tentar Firestore primeiro
    db = None
    try:
        if not _firestore_initialized:
            logger.info("[save-count] Firestore não inicializado ainda, inicializando...")
            db = await asyncio.to_thread(_get_firestore_db)
        else:
            db = _firestore_db
            logger.info("[save-count] Firestore já inicializado, db=%s",
                        "OK" if db else "None")
    except Exception as e:
        logger.error("[save-count] Erro ao obter Firestore: %s", e)

    if db is not None:
        return await _save_to_firestore(db, phone_hash, doc_id, today, limit)

    # Fallback in-memory
    logger.warning("[save-count] ⚠️ Firestore INDISPONÍVEL — usando fallback in-memory")
    count = _memory_increment(phone_hash)
    logger.info("[save-count] IN-MEMORY: %s… → %d/%d", doc_id, count, limit)
    return {"daily_count": count}


async def _save_to_firestore(db, phone_hash: str, doc_id: str,
                              today: str, limit: int) -> WorkflowState:
    """Salva contagem no Firestore."""
    try:
        from google.cloud.firestore_v1 import Increment

        doc_ref = db.collection(_COLLECTION).document(phone_hash)

        logger.info("[save-count] Lendo doc %s… do Firestore...", doc_id)
        doc = await asyncio.to_thread(doc_ref.get)
        logger.info("[save-count] Doc existe: %s", doc.exists)

        if not doc.exists:
            # Caso A: Primeiro acesso — criar documento
            data = {
                "lastInteractionDate": today,
                "dailyMessageCount": 1,
                "totalMessageCount": 1,
            }
            logger.info("[save-count] Criando doc novo: %s", data)
            await asyncio.to_thread(doc_ref.set, data)
            logger.info("[save-count] ✅ NOVO usuário %s… → 1/%d (criado no Firestore)",
                        doc_id, limit)
            return {"daily_count": 1}

        data = doc.to_dict()
        last_date = data.get("lastInteractionDate", "")
        daily_count = data.get("dailyMessageCount", 0)
        total_count = data.get("totalMessageCount", 0)

        logger.info("[save-count] Doc %s… dados atuais: lastDate=%s, daily=%d, total=%d",
                    doc_id, last_date, daily_count, total_count)

        if today != last_date:
            # Caso B: Novo dia — resetar daily
            update_data = {
                "lastInteractionDate": today,
                "dailyMessageCount": 1,
                "totalMessageCount": Increment(1),
            }
            logger.info("[save-count] Novo dia detectado (era %s, agora %s), resetando...",
                        last_date, today)
            await asyncio.to_thread(doc_ref.update, update_data)
            logger.info("[save-count] ✅ NOVO DIA %s… → 1/%d", doc_id, limit)
            return {"daily_count": 1}

        # Mesmo dia
        if daily_count >= limit:
            # Caso D: Limite já atingido — NÃO atualizar banco
            logger.warning("[save-count] 🚫 LIMITE JÁ ATINGIDO %s… → %d/%d (NÃO incrementou)",
                           doc_id, daily_count, limit)
            return {"daily_count": daily_count}

        # Caso C: Dentro do limite — incrementar
        update_data = {
            "dailyMessageCount": Increment(1),
            "totalMessageCount": Increment(1),
        }
        logger.info("[save-count] Incrementando %s… (atual=%d)...", doc_id, daily_count)
        await asyncio.to_thread(doc_ref.update, update_data)
        new_count = daily_count + 1
        logger.info("[save-count] ✅ INCREMENTOU %s… → %d/%d", doc_id, new_count, limit)
        return {"daily_count": new_count}

    except Exception as e:
        logger.exception("[save-count] ❌ ERRO Firestore: %s — usando fallback in-memory", e)
        # Fallback: usar in-memory para não perder o rate limit
        count = _memory_increment(phone_hash)
        logger.info("[save-count] FALLBACK in-memory: %s… → %d/%d", doc_id, count, limit)
        return {"daily_count": count}


# ═══════════════════════════════════════════════════════════════════
#  NÓ 2: check_rate_limit — bloqueia se passou do limite
# ═══════════════════════════════════════════════════════════════════

async def check_rate_limit(state: WorkflowState) -> WorkflowState:
    """Verifica se o usuário excedeu o limite diário.

    daily_count >= DAILY_MESSAGE_LIMIT → BLOQUEIA
    """
    phone = state.get("numero_quem_enviou", "")
    daily_count = state.get("daily_count", 0)
    limit = config.DAILY_MESSAGE_LIMIT

    logger.info("[rate-limit] ══════ CHECK ══════")
    logger.info("[rate-limit] daily_count=%d, limit=%d, phone=…%s",
                daily_count, limit, phone[-4:] if phone else "???")

    if not phone:
        logger.info("[rate-limit] Sem telefone — liberando")
        return {"rate_limited": False}

    if daily_count >= limit:
        logger.warning("[rate-limit] 🚫 BLOQUEADO: %d >= %d — enviando aviso ao usuário",
                       daily_count, limit)
        try:
            from nodes import whatsapp_api
            await whatsapp_api.send_text(
                phone,
                _LIMIT_REACHED_MESSAGE,
                quoted_message_id=state.get("id_mensagem"),
            )
            logger.info("[rate-limit] ✅ Aviso de bloqueio enviado")
        except Exception as e:
            logger.exception("[rate-limit] Falha ao enviar aviso: %s", e)

        return {"rate_limited": True}

    logger.info("[rate-limit] ✅ LIBERADO: %d/%d", daily_count, limit)
    return {"rate_limited": False}


def route_rate_limit(state: WorkflowState) -> str:
    """Rota: bloqueado → END, ok → check_is_on_group."""
    blocked = state.get("rate_limited", False)
    logger.info("[rate-limit-route] rate_limited=%s → %s",
                blocked, "__end__" if blocked else "check_is_on_group")
    if blocked:
        return "__end__"
    return "check_is_on_group"
