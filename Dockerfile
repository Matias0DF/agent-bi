FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /bin/uv

RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FASTEMBED_CACHE_PATH=/home/user/app/models \
    PATH="/home/user/app/.venv/bin:$PATH"

COPY --chown=user pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=user src ./src
RUN uv sync --frozen --no-dev
RUN python -c "from agent_bi.rag import embed; embed(['precarga del modelo'])"

EXPOSE 7860
CMD ["uvicorn", "agent_bi.api:app", "--host", "0.0.0.0", "--port", "7860", "--proxy-headers", "--forwarded-allow-ips", "*"]
