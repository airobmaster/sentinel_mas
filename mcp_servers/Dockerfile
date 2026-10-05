# Image for the Sentinel MCP tool servers. One image, one container per server:
#   docker compose up  (see docker-compose.yml)  or  docker run ... python -m mcp_servers txn_history
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app

# Only what the tool servers need (the full requirements.txt is for the agent service and dev).
RUN pip install --no-cache-dir \
    "fastmcp==2.14.7" "mcp==1.30.0" "langchain-core>=1.0,<2.0" "psycopg[binary,pool]>=3.2" \
    "rapidfuzz>=3.9" "pydantic-settings>=2.4" "pyjwt>=2.9"

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY mcp_servers ./mcp_servers

RUN useradd --create-home --uid 10001 mcp
USER mcp
EXPOSE 8000
ENTRYPOINT ["python", "-m", "mcp_servers"]
