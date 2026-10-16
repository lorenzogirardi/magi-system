# AgentCore Runtime image for magi-system (microVM compute type: linux/arm64).
# Build from the repository root:
#   podman build --platform linux/arm64 -f deploy/agentcore/Dockerfile \
#     --build-arg MAGI_PLATFORM_REF=$(git rev-parse HEAD) -t magi-agentcore .
# Contains no secrets: the OpenRouter key is read at start from Secrets Manager
# (MAGI_OPENROUTER_SECRET_ID). See docs/agentcore/.
FROM docker.io/library/python:3.13-slim

ARG MAGI_PLATFORM_REF=unknown
ENV MAGI_PLATFORM_REF=${MAGI_PLATFORM_REF} \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/magi/.venv \
    PATH=/opt/magi/.venv/bin:$PATH

# git: workspace clones and commits. ca-certificates: HTTPS to OpenRouter and Git hosts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.25 /uv /usr/local/bin/uv

WORKDIR /opt/magi
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra agentcore \
 && rm /usr/local/bin/uv

# Non-root; must match the POSIX uid/gid of the EFS access points (MAGI_EFS_POSIX_UID/GID).
RUN useradd --uid 1000 --create-home magi
USER 1000:1000

ENV MAGI_STORE=dynamodb \
    MAGI_RUNS_ROOT=/mnt/runs \
    MAGI_ALLOWED_PROFILES=agentcore \
    MAGI_ALLOWED_PROVIDERS=openrouter \
    MAGI_EVENTS_STDOUT=1

EXPOSE 8080
ENTRYPOINT ["magi-agentcore"]
