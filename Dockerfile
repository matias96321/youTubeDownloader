FROM python:3.11-slim

# Instala dependências do sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Diretório de trabalho
WORKDIR /app

# Copia arquivos do projeto
COPY . .

# Instala dependências Python
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

RUN pip install yt-dlp --upgrade

# Expõe a porta usada pelo uvicorn
EXPOSE 10000

# Comando para iniciar a aplicação
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]