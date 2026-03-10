# Usando Python 3.11 slim para menor tamanho de imagem
FROM python:3.11-slim

# Definir diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema necessárias para pydub, ffmpeg e yt-dlp EJS
# tini = init process correto para containers (repassa SIGTERM para Python)
# curl + unzip = necessários para instalar Deno (JS runtime para yt-dlp YouTube)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tini \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# ── Instalar Deno (JavaScript runtime requerido pelo yt-dlp para YouTube) ──
# Desde yt-dlp 2025.11.12, um JS runtime externo é OBRIGATÓRIO para YouTube.
# Deno é o runtime recomendado pelo yt-dlp (habilitado por padrão).
# Ref: https://github.com/yt-dlp/yt-dlp/issues/15012
# Instalação via GitHub releases para evitar problemas de DNS com deno.land
ENV DENO_DIR=/deno
RUN DENO_VERSION="v2.3.5" \
    && ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "amd64" ]; then DENO_ARCH="x86_64"; else DENO_ARCH="aarch64"; fi \
    && curl -fsSL "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin/ \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && deno --version \
    && mkdir -p /deno && chmod 777 /deno

# Copiar requirements primeiro (melhor uso de cache do Docker)
COPY requirements.txt .

# Instalar dependências Python
# Nota: yt-dlp[default] inclui yt-dlp-ejs (scripts EJS para resolver challenges do YouTube)
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo o código da aplicação
COPY . .

# Garantir que o firebase-credentials.json existe e é legível
# (será montado como volume no docker-compose, mas copiado como fallback)
RUN test -f firebase-credentials.json && chmod 644 firebase-credentials.json || true

# Expor a porta 5000 (padrão do webhook)
EXPOSE 5000

# Criar usuário não-root para segurança
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /deno
USER appuser

# tini como entrypoint garante que SIGTERM é repassado corretamente
# Sem isso, Docker envia SIGTERM, Python ignora, e após 10s faz SIGKILL
# matando todas as tasks em processamento instantaneamente
ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py"]
