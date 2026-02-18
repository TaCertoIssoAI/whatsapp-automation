# Usando Python 3.11 slim para menor tamanho de imagem
FROM python:3.11-slim

# Definir diretório de trabalho
WORKDIR /app

# Instalar dependências do sistema necessárias para pydub e ffmpeg
# tini = init process correto para containers (repassa SIGTERM para Python)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements primeiro (melhor uso de cache do Docker)
COPY requirements.txt .

# Instalar dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo o código da aplicação
COPY . .

# Expor a porta 5000 (padrão do webhook)
EXPOSE 5000

# Criar usuário não-root para segurança
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# tini como entrypoint garante que SIGTERM é repassado corretamente
# Sem isso, Docker envia SIGTERM, Python ignora, e após 10s faz SIGKILL
# matando todas as tasks em processamento instantaneamente
ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py"]
