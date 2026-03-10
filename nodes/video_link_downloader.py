"""Deteção de links de vídeo e download via yt-dlp.

Deteta URLs de plataformas de vídeo (YouTube, YouTube Shorts, Instagram
Reels/vídeos, TikTok, Facebook, X/Twitter, etc.) em mensagens de texto,
verifica a duração ANTES de baixar (máximo 2 minutos), faz download do
vídeo usando yt-dlp em resolução máxima de 480p e retorna o vídeo como
base64 para injeção no pipeline existente — tratado exatamente como um
vídeo enviado nativamente no WhatsApp.
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


# ── Diagnóstico do yt-dlp (executado uma única vez) ──

_ytdlp_diag_done = False


def _log_ytdlp_diagnostics() -> None:
    """Loga informações de diagnóstico do yt-dlp (versão, plugins, JS runtime)."""
    global _ytdlp_diag_done
    if _ytdlp_diag_done:
        return
    _ytdlp_diag_done = True

    try:
        import yt_dlp
        logger.info("[ytdlp-diag] yt-dlp versão: %s", yt_dlp.version.__version__)
    except Exception as e:
        logger.error("[ytdlp-diag] Falha ao importar yt-dlp: %s", e)
        return

    # Verificar se yt-dlp-ejs está instalado
    try:
        import importlib
        ejs_spec = importlib.util.find_spec("yt_dlp_ejs")
        if ejs_spec:
            logger.info("[ytdlp-diag] yt-dlp-ejs: INSTALADO (%s)", ejs_spec.origin)
        else:
            logger.warning("[ytdlp-diag] yt-dlp-ejs: NÃO ENCONTRADO — YouTube pode falhar!")
    except Exception:
        logger.warning("[ytdlp-diag] yt-dlp-ejs: não foi possível verificar")

    # Verificar se bgutil plugin está instalado
    try:
        bgutil_spec = importlib.util.find_spec("yt_dlp_plugins")
        logger.info("[ytdlp-diag] yt_dlp_plugins dir: %s", bgutil_spec.submodule_search_locations if bgutil_spec else "N/A")
    except Exception:
        pass

    # Verificar se Deno está disponível
    import shutil as _shutil
    deno_path = _shutil.which("deno")
    node_path = _shutil.which("node")
    if deno_path:
        logger.info("[ytdlp-diag] JS runtime Deno: %s", deno_path)
    elif node_path:
        logger.info("[ytdlp-diag] JS runtime Node: %s", node_path)
    else:
        logger.warning(
            "[ytdlp-diag] NENHUM JS runtime (deno/node) encontrado! "
            "YouTube requer JS runtime desde yt-dlp 2025.11.12"
        )

    # Verificar POT_PROVIDER_URL
    pot_url = os.environ.get("POT_PROVIDER_URL", "")
    if pot_url:
        logger.info("[ytdlp-diag] POT_PROVIDER_URL: %s", pot_url)
    else:
        logger.info("[ytdlp-diag] POT_PROVIDER_URL: não configurado (usando padrão 127.0.0.1:4416)")


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


# ── Resolução de info para playlists/carrosséis ──

def _resolve_single_video_info(info: dict) -> dict | None:
    """Resolve a info para um único vídeo.

    Quando extract_info retorna uma playlist (ex: carrossel do Instagram),
    seleciona a primeira entrada que seja um vídeo.  Também filtra
    resultados que claramente não são vídeos (fotos do Instagram, etc.).
    """
    # Se é uma playlist (carrossel Instagram, etc.), pegar a primeira entry
    if info.get("_type") == "playlist" or "entries" in info:
        entries = info.get("entries")
        if entries is None:
            return None
        # entries pode ser um gerador/LazyList — iterar com cuidado
        for entry in entries:
            if entry is None:
                continue
            # Verificar se é vídeo (tem duração ou formato de vídeo)
            if entry.get("duration") is not None or entry.get("vcodec", "none") != "none":
                return entry
            # Verificar se tem formatos de vídeo disponíveis
            fmts = entry.get("formats") or []
            if any(f.get("vcodec", "none") != "none" for f in fmts):
                return entry
            # Entrada do tipo 'url' (lazy) — aceitar se não foi possível verificar
            if entry.get("_type") in ("url", "url_transparent"):
                return entry
        return None

    return info


def _is_video_content(info: dict) -> bool:
    """Verifica se a info extraída corresponde a conteúdo de vídeo.

    Filtra fotos do Instagram, posts de texto, etc. que não contêm vídeo.
    """
    # Se tem duração, provavelmente é vídeo
    if info.get("duration") is not None:
        return True

    # Se tem formatos com codec de vídeo, é vídeo
    formats = info.get("formats", [])
    if any(f.get("vcodec", "none") != "none" for f in formats):
        return True

    # Se o extractor indicou que é vídeo
    if info.get("vcodec", "none") != "none":
        return True

    return False


# ── Download síncrono (executa em thread pool) ──

def _download_video_sync(url: str) -> DownloadResult:
    """Download de vídeo via yt-dlp (SÍNCRONO — executar em thread pool).

    Fluxo:
    1. extract_info(download=False) — extrai metadados sem baixar
    2. Verifica se é vídeo (não foto/post de texto)
    3. Verifica duração (máximo 2 minutos = 120 segundos)
    4. process_ie_result(info, download=True) — baixa reutilizando info já extraída
    5. Lê o arquivo, converte para base64

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

    # Diagnóstico na primeira execução
    _log_ytdlp_diagnostics()

    try:
        tmp_dir = tempfile.mkdtemp(prefix="ytdlp_")
    except Exception:
        logger.error("[ytdlp] Falha ao criar diretório temporário")
        return DownloadResult(status="error", url=url)

    output_template = os.path.join(tmp_dir, f"video_{uuid.uuid4().hex[:8]}.%(ext)s")

    # Logger customizado para capturar output do yt-dlp
    # Redireciona para o nosso logger — mensagens de debug do yt-dlp contêm
    # informações vitais sobre PO Token, EJS, plugins e JS runtime
    class _YDLLogger:
        def debug(self, msg: str) -> None:
            # Mensagens com [pot], [ejs], [debug] são importantes para diagnóstico
            if any(kw in msg.lower() for kw in ("[pot", "[ejs", "po token", "js runtime", "javascript")):
                logger.info("[ytdlp-lib] %s", msg)
            else:
                logger.debug("[ytdlp-lib] %s", msg)
        def warning(self, msg: str) -> None:
            logger.warning("[ytdlp-lib] %s", msg)
        def error(self, msg: str) -> None:
            logger.warning("[ytdlp-lib] %s", msg)

    # ── Extractor args para YouTube ──
    # Usar 'mweb' como player client — é mais leve e funciona bem com PO Token.
    # Se o plugin bgutil-ytdlp-pot-provider estiver instalado, ele gera
    # PO Tokens automaticamente via HTTP server (porta 4416 por padrão).
    # NOTA: usar "player-client" (com hífen) — é o formato correto para yt-dlp recente.
    _yt_extractor_args = {
        "youtube": [
            "player-client=mweb,default",
        ],
    }

    # Se a env var POT_PROVIDER_URL estiver definida, configurar o plugin
    pot_url = os.environ.get("POT_PROVIDER_URL", "")
    if pot_url:
        _yt_extractor_args["youtubepot-bgutilhttp"] = [
            f"base_url={pot_url}",
        ]
        logger.debug("[ytdlp] PO Token provider configurado: %s", pot_url)

    ydl_opts = {
        "outtmpl": output_template,
        "logger": _YDLLogger(),
        # Verbose = True para que yt-dlp logue informações de PO Token,
        # EJS, plugins e JS runtime (capturado pelo nosso logger)
        "verbose": True,
        # Seleção de formato: máximo 480p, preferindo mp4
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
        # Não baixar playlists — apenas o vídeo individual
        "noplaylist": True,
        # Extractor args: player client mweb + PO Token provider
        "extractor_args": _yt_extractor_args,
        # Forçar saída em mp4 para compatibilidade com WhatsApp
        "postprocessors": [{
            "key": "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # ════════════════════════════════════════
            # Fase 1: Extrair info SEM download
            # ════════════════════════════════════════
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                exc_str = str(exc)
                logger.info("[ytdlp] Falha ao extrair info de %s: %s", url, exc)
                # Distinguir erro de bot detection / autenticação do YouTube
                # de outros erros (URL inválida, etc.)
                _bot_keywords = (
                    "Sign in to confirm",
                    "not a bot",
                    "cookies",
                    "authentication",
                    "HTTP Error 403",
                    "403",
                )
                if any(kw.lower() in exc_str.lower() for kw in _bot_keywords):
                    logger.warning(
                        "[ytdlp] YouTube bloqueou o download (bot detection) para %s. "
                        "Verifique se o PO Token provider está ativo.",
                        url,
                    )
                    return DownloadResult(status="error", url=url)
                return DownloadResult(status="not_video", url=url)

            if info is None:
                logger.info("[ytdlp] Nenhuma info extraída para: %s", url)
                return DownloadResult(status="not_video", url=url)

            # Resolver playlist/carrossel para um único vídeo
            info = _resolve_single_video_info(info)
            if info is None:
                logger.info("[ytdlp] Nenhum vídeo encontrado (playlist/carrossel sem vídeo): %s", url)
                return DownloadResult(status="not_video", url=url)

            # Verificar se é realmente conteúdo de vídeo
            if not _is_video_content(info):
                logger.info("[ytdlp] Conteúdo não é vídeo (foto ou post de texto): %s", url)
                return DownloadResult(status="not_video", url=url)

            # Verificar se é live/stream
            is_live = info.get("is_live", False)
            if is_live:
                logger.info("[ytdlp] Conteúdo é live/stream, ignorando: %s", url)
                return DownloadResult(status="not_video", url=url)

            # Extrair duração
            duration = 0
            raw_duration = info.get("duration")
            if raw_duration is not None:
                try:
                    duration = int(raw_duration)
                except (ValueError, TypeError):
                    duration = 0

            # ════════════════════════════════════════
            # Verificar duração ANTES do download
            # Máximo: 2 minutos (120 segundos)
            # ════════════════════════════════════════
            if duration > _MAX_DURATION:
                logger.info(
                    "[ytdlp] Vídeo muito longo: %ds > %ds para %s",
                    duration, _MAX_DURATION, url,
                )
                return DownloadResult(
                    status="duration_exceeded", url=url, duration=duration,
                )

            # Extrair descrição/legenda do vídeo
            description = (
                info.get("description")
                or info.get("title")
                or info.get("fulltitle")
                or ""
            )
            if len(description) > _MAX_DESCRIPTION_LEN:
                description = description[:_MAX_DESCRIPTION_LEN - 3] + "..."

            # ════════════════════════════════════════
            # Fase 2: Download reutilizando info já extraída
            # ════════════════════════════════════════
            try:
                ydl.process_ie_result(info, download=True)
            except Exception:
                logger.warning("[ytdlp] Falha no download de %s", url, exc_info=True)
                return DownloadResult(status="error", url=url)

        # Encontrar o ficheiro baixado
        downloaded_file = None
        try:
            for fname in os.listdir(tmp_dir):
                fpath = os.path.join(tmp_dir, fname)
                if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                    # Preferir .mp4
                    if fpath.endswith(".mp4"):
                        downloaded_file = fpath
                        break
                    if downloaded_file is None:
                        downloaded_file = fpath
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
