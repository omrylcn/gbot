FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim

WORKDIR /app

# Healthcheck için curl
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Bağımlılıkları önce kopyala (Docker cache)
COPY pyproject.toml README.md ./
COPY graphbot/__init__.py graphbot/__version__.py graphbot/
RUN uv pip install --system --no-cache ".[channels]"

# Kaynak kodu kopyala ve kur
COPY graphbot/ graphbot/
RUN uv pip install --system --no-cache .

# Runtime dizinleri
RUN mkdir -p /app/data /app/workspace

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["graphbot"]
CMD ["run", "--host", "0.0.0.0", "--port", "8000"]
