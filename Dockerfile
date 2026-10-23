FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

ARG CHAIN_REACTION_PY_REPO=https://github.com/AccXLR8/Python-Engine.git
ARG CHAIN_REACTION_PY_REF=main

RUN pip install --no-cache-dir --upgrade pip \
    && git clone --depth 1 --branch "${CHAIN_REACTION_PY_REF}" "${CHAIN_REACTION_PY_REPO}" /tmp/chainreaction-py \
    && pip install --no-cache-dir /tmp/chainreaction-py \
    && rm -rf /tmp/chainreaction-py

COPY . .

RUN pip install --no-cache-dir .


FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=builder /usr/local /usr/local
COPY --from=builder /build /app

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
