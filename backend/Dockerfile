FROM python:3.12-slim

WORKDIR /app

# Instalar deps de sistema (sqlite3 para backups)
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Deps Python primero para mejor caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY app ./app
COPY seed_data.json .

# Crear dir de datos
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
