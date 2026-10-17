# AgentCore Runtime BASE image for magi-system (microVM compute type: linux/arm64).
# It holds Python, git, uv and the locked dependencies, but NOT the magi-system code: that is a zip in S3
# loaded at session start by bootstrap.py (see deploy/agentcore/push_code.py). Rebuild this image only when
# pyproject.toml / uv.lock (the dependencies) or this file change; a code change needs `push_code.py` only.
# Build from the repository root:
#   docker build --platform linux/arm64 -f deploy/agentcore/Dockerfile -t magi-agentcore .
# Contains no secrets: the OpenRouter key is read at start from Secrets Manager
# (MAGI_OPENROUTER_SECRET_ID). See docs/agentcore/.
FROM docker.io/library/python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
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
COPY pyproject.toml uv.lock ./
COPY deploy/agentcore/deps_hash.py deploy/agentcore/bootstrap.py /opt/magi/
# Dependencies only (--no-install-project). The hash of the pins is what a code bundle must match.
RUN uv sync --frozen --no-dev --extra agentcore --no-install-project \
 && uv export --frozen --no-dev --extra agentcore --no-hashes --no-emit-project --no-header -q \
    | python /opt/magi/deps_hash.py > /opt/magi/deps.sha256
# uv stays in the image: the runtime installs the target's wheels for the sandbox (sandbox.requirements).

# Non-root; must match the POSIX uid/gid of the EFS access points (MAGI_EFS_POSIX_UID/GID).
RUN useradd --uid 1000 --create-home magi
USER 1000:1000

ENV MAGI_STORE=dynamodb \
    MAGI_RUNS_ROOT=/mnt/runs \
    MAGI_ALLOWED_PROFILES=agentcore \
    MAGI_ALLOWED_PROVIDERS=openrouter \
    MAGI_EVENTS_STDOUT=1

EXPOSE 8080
ENTRYPOINT ["python", "/opt/magi/bootstrap.py"]
