FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG CHAIN_REACTION_PY_REPO=https://github.com/AccXLR8/Backend-Python.git
ARG CHAIN_REACTION_PY_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

RUN git clone --depth 1 --branch ${CHAIN_REACTION_PY_REF} ${CHAIN_REACTION_PY_REPO} /tmp/chainreaction-py \
    && pip install --no-cache-dir /tmp/chainreaction-py/python-engine \
    && rm -rf /tmp/chainreaction-py

COPY . .

RUN pip install --no-cache-dir .[dev]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
