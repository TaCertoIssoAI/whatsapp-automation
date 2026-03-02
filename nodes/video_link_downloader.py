"""Deteção de links de vídeo e download via yt-dlp.

Deteta URLs de plataformas de vídeo (YouTube, Instagram, TikTok, Facebook,
X/Twitter, etc.) em mensagens de texto, faz download do vídeo usando yt-dlp
e retorna o vídeo como base64 para injeção no pipeline existente.
"""

import asyncio
import base64
import logging
import os
import re
import shutil
import tempfile
import threading
import time as _time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Deteção de URLs ──

_VIDEO_PLATFORM_DOMAINS = (
    r"(?:youtube\.com|youtu\.be|"
    r"instagram\.com|"
    r"tiktok\.com|"
    r"facebook\.com|fb\.watch|"
    r"twitter\.com|x\.com|"
    r"vimeo\.com|"
    r"dailymotion\.com|"
    r"reddit\.com|v\.redd\.it|"
    r"streamable\.com|"
    r"twitch\.tv|clips\.twitch\.tv|"
    r"kwai\.com|"
    r"rumble\.com)"
)

_VIDEO_URL_REGEX = re.compile(
    r"https?://(?:www\.|m\.|vm\.)?" + _VIDEO_PLATFORM_DOMAINS + r"[^\s\)\]\}]*",
    re.IGNORECASE,
)

_MAX_DURATION = 120       # Duração máxima em segundos
_DOWNLOAD_TIMEOUT = 90    # Timeout para o download (segundos)
_MAX_FILESIZE = 50 * 1024 * 1024  # 50MB
_MAX_DESCRIPTION_LEN = 500


# ── Resultado do download ──

@dataclass
class DownloadResult:
    """Resultado de uma tentativa de download de vídeo."""
    status: str  # "success", "duration_exceeded", "not_video", "error"
    video_b64: str = ""
    description: str = ""
    url: str = ""
    duration: int = 0


# ── Cache em memória para vídeos baixados ──

_video_cache: dict[str, tuple[str, float]] = {}  # media_id -> (base64, timestamp)
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutos


def cache_video(media_id: str, video_b64: str) -> None:
    """Armazena um vídeo baixado no cache em memória."""
    with _cache_lock:
        _video_cache[media_id] = (video_b64, _time.monotonic())
        # Limpeza lazy de entradas expiradas
        if len(_video_cache) > 20:
            now = _time.monotonic()
            expired = [k for k, (_, ts) in _video_cache.items() if now - ts > _CACHE_TTL]
            for k in expired:
                del _video_cache[k]


def get_cached_video(media_id: str) -> str | None:
    """Recupera e remove um vídeo do cache. Retorna base64 ou None."""
    with _cache_lock:
        entry = _video_cache.pop(media_id, None)
    if entry is None:
        return None
    b64, ts = entry
    if _time.monotonic() - ts > _CACHE_TTL:
        return None
    return b64


def is_ytdlp_media_id(media_id: str) -> bool:
    """Verifica se um media_id é um ID sintético do yt-dlp."""
    return media_id.startswith("ytdlp_local_")


# ── Deteção de URLs ──

def extract_video_url(text: str) -> str | None:
    """Extrai a primeira URL de plataforma de vídeo do texto."""
    if not text:
        return None
    match = _VIDEO_URL_REGEX.search(text)
    return match.group(0) if match else None


# ── Limpeza segura de diretório temporário ──

def _cleanup_tmp_dir(tmp_dir: str) -> None:
    """Remove o diretório temporário e todo o seu conteúdo de forma segura."""
    try:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


# ── Download síncrono (executa em thread pool) ──

def _download_video_sync(url: str) -> DownloadResult:
    """Download de vídeo via yt-dlp (SÍNCRONO — executar em thread pool).

    Retorna DownloadResult com o status da operação.
    Nunca levanta exceções — todos os erros são capturados e retornados
    como DownloadResult com status apropriado.
    """
    tmp_dir = None
    try:
        import yt_dlp
    except ImportError:
        logger.error("[ytdlp] yt-dlp não instalado")
        return DownloadResult(status="error", url=url)

    try:
        tmp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    except Exception:
        logger.error("[ytdlp] Falha ao criar diretório temporário")
        return DownloadResult(status="error", url=url)

    output_template = os.path.join(tmp_dir, f"video_{uuid.uuid4().hex[:8]}.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "format": (
            "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=480]+bestaudio/"
            "best[height<=480]/"
            "best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 2,
        "max_filesize": _MAX_FILESIZE,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Fase 1: Extrair info sem download — verifica duração ANTES
            try:
                info = ydl.extract_info(url, download=False)
            except Exception:
                logger.warning("[ytdlp] Falha ao extrair info de %s", url, exc_info=True)
                return DownloadResult(status="not_video", url=url)

            if info is None:
                logger.info("[ytdlp] Nenhuma info extraída para: %s", url)
                return DownloadResult(status="not_video", url=url)

            # Verificar se é realmente um vídeo (algumas páginas retornam
            # playlists ou resultados sem duração)
            duration = 0
            raw_duration = info.get("duration")
            if raw_duration is not None:
                try:
                    duration = int(raw_duration)
                except (ValueError, TypeError):
                    duration = 0

            # Se não tem duração e não é live, pode ser página sem vídeo
            is_live = info.get("is_live", False)
            if is_live:
                logger.info("[ytdlp] Conteúdo é live/stream, ignorando: %s", url)
                return DownloadResult(status="not_video", url=url)

            # Verificar duração ANTES do download
            if duration > _MAX_DURATION:
                logger.info(
                    "[ytdlp] Vídeo muito longo: %ds > %ds para %s",
                    duration, _MAX_DURATION, url,
                )
                return DownloadResult(
                    status="duration_exceeded", url=url, duration=duration,
                )

            description = (
                info.get("description")
                or info.get("title")
                or info.get("fulltitle")
                or ""
            )
            if len(description) > _MAX_DESCRIPTION_LEN:
                description = description[:_MAX_DESCRIPTION_LEN - 3] + "..."

            # Fase 2: Download
            try:
                ydl.download([url])
            except Exception:
                logger.warning("[ytdlp] Falha no download de %s", url, exc_info=True)
                return DownloadResult(status="error", url=url)

        # Encontrar o ficheiro baixado
        downloaded_file = None
        try:
            for fname in os.listdir(tmp_dir):
                fpath = os.path.join(tmp_dir, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    downloaded_file = fpath
                    break
        except OSError:
            logger.warning("[ytdlp] Falha ao listar ficheiros em %s", tmp_dir)
            return DownloadResult(status="error", url=url)

        if not downloaded_file:
            logger.warning("[ytdlp] Nenhum ficheiro encontrado após download de %s", url)
            return DownloadResult(status="error", url=url)

        # Verificar tamanho do ficheiro
        try:
            file_size = os.path.getsize(downloaded_file)
        except OSError:
            return DownloadResult(status="error", url=url)

        if file_size > _MAX_FILESIZE:
            logger.warning(
                "[ytdlp] Ficheiro muito grande: %.1f MB para %s",
                file_size / (1024 * 1024), url,
            )
            return DownloadResult(status="error", url=url)

        if file_size == 0:
            logger.warning("[ytdlp] Ficheiro vazio para %s", url)
            return DownloadResult(status="error", url=url)

        try:
            with open(downloaded_file, "rb") as f:
                video_bytes = f.read()
        except (OSError, MemoryError):
            logger.warning("[ytdlp] Falha ao ler ficheiro de %s", url, exc_info=True)
            return DownloadResult(status="error", url=url)

        video_b64 = base64.b64encode(video_bytes).decode("utf-8")
        logger.info(
            "[ytdlp] Download concluído %s: %.1f MB, duração=%ds",
            url, len(video_bytes) / (1024 * 1024), duration,
        )
        return DownloadResult(
            status="success",
            video_b64=video_b64,
            description=description,
            url=url,
            duration=duration,
        )

    except MemoryError:
        logger.error("[ytdlp] Memória insuficiente ao processar %s", url)
        return DownloadResult(status="error", url=url)
    except Exception:
        logger.warning("[ytdlp] Erro inesperado ao processar %s", url, exc_info=True)
        return DownloadResult(status="error", url=url)
    finally:
        if tmp_dir:
            _cleanup_tmp_dir(tmp_dir)


# ── Wrapper assíncrono ──

async def try_download_video_from_url(text: str) -> DownloadResult | None:
    """Tenta detetar uma URL de vídeo no texto e fazer download.

    Ponto de entrada assíncrono principal. Executa yt-dlp em thread pool.

    Returns:
        DownloadResult com o status da operação, ou None se não encontrou URL.
    """
    url = extract_video_url(text)
    if not url:
        return None

    logger.info("[ytdlp] URL de vídeo detetada: %s", url)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_download_video_sync, url),
            timeout=_DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning("[ytdlp] Timeout no download de %s", url)
        return DownloadResult(status="error", url=url)
    except Exception:
        logger.warning("[ytdlp] Erro inesperado para %s", url, exc_info=True)
        return DownloadResult(status="error", url=url)

    return result
