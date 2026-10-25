FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv

WORKDIR /app
# Explicit inputs, in addition to .dockerignore, keep local state out.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY sources/ ./sources/
COPY schemas/ ./schemas/
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 datumoj

ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["datumoj"]
CMD ["cloud", "run"]
