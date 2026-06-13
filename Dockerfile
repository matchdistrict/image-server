FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies:
#   build-essential  — compiles C extensions (asyncpg, cryptography)
#   libpq-dev        — PostgreSQL client headers for asyncpg
#   libssl-dev       — OpenSSL headers required by Telethon / cryptography
#   libffi-dev       — FFI headers required by the cryptography package
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (telethon>=1.36.0 is now included)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Expose app port (Railway injects $PORT at runtime)
EXPOSE 8000

# Start uvicorn — binds to $PORT if set by Railway, otherwise falls back to 8000
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
