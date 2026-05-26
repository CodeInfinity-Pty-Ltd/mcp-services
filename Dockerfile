# Build context is the repo root. Single image, single pod.

FROM python:3.13-slim

WORKDIR /app

# uv for fast, frozen deps
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install deps first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# App code — templates + public assets too, since the landing route renders
# a Frond template and references CSS from src/public/css/.
COPY app.py ./
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1 \
    TINA4_OVERRIDE_CLIENT=true \
    PORT=7145
EXPOSE 7145

CMD ["uv", "run", "python", "app.py", "0.0.0.0:7145"]
