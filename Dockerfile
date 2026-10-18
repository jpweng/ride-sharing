FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first so this layer is cached across code-only changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/ backend/
COPY frontend/ frontend/

# uid/gid 1000 matches the EFS access point's POSIX user (infra/efs.tf).
RUN useradd --uid 1000 --user-group --no-create-home app \
    && mkdir /data && chown app:app /data

ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/rideshare.db \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8000

CMD ["uvicorn", "main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
