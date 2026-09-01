# syntax=docker/dockerfile:1.7
# Echo OS appliance · one verified Echo wheel/resources/Codex + NAS desktop.
#
# Required preflight:
#   ./deploy/appliance/prepare-agent-bundle.sh
#   docker build -t echo-os .
# Safe local publish (loopback only):
#   docker run --rm -p 127.0.0.1:8000:8000 echo-os

FROM node:20-alpine@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293 AS webui-builder

WORKDIR /webui
RUN corepack enable && corepack prepare pnpm@10.26.2 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build


# Fail before dependency installation if the three Agent surfaces are missing,
# mixed between commits, or changed after the manifest was assembled.
FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS agent-bundle-verifier

WORKDIR /build
COPY deploy/appliance/agent_bundle.py ./agent_bundle.py
COPY deploy/appliance/agent-bundle.json ./agent-bundle.json
COPY deploy/appliance/agent-dist/ ./agent-dist/
COPY deploy/appliance/agent-resources/ ./agent-resources/
COPY deploy/appliance/agent-codex/ ./agent-codex/
RUN python agent_bundle.py verify \
      --bundle-root /build \
      --manifest /build/agent-bundle.json


FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY appliance/ ./appliance/
COPY --from=agent-bundle-verifier /build/agent_bundle.py ./agent_bundle.py
COPY --from=agent-bundle-verifier /build/agent-bundle.json ./agent-bundle.json
COPY --from=agent-bundle-verifier /build/agent-dist/ ./agent-dist/

# Install the source-bound, hash-locked dependency closure and the one unified
# Echo wheel. Build backends live only in /build-tools and are never copied
# into the runtime image.
RUN pip install --prefix=/build-tools --no-warn-script-location \
      --require-hashes --only-binary=:all: \
      -r agent-dist/build-requirements.lock \
 && pip install --prefix=/install --no-warn-script-location \
      --require-hashes --only-binary=:all: \
      -r agent-dist/runtime-requirements.lock \
 && pip install --prefix=/install --no-warn-script-location --no-deps \
      -r agent-dist/requirements.txt \
 && PYTHONPATH=/install/lib/python3.12/site-packages \
      python agent_bundle.py verify-installed --manifest agent-bundle.json


FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/install/lib/python3.12/site-packages:${PYTHONPATH}" \
    ECHO_DATA_DIR=/data \
    ECHO_CONFIG=/etc/echo/config.yaml \
    ECHO_WEBUI_DIST=/app/webui \
    ECHO_RESOURCES_DIR=/app/resources \
    ECHO_AGENT_BUNDLE_MANIFEST=/app/agent-bundle.json \
    ECHO_CODEX_BUNDLE_DIR=/app/codex \
    ECHO_CODEX_EXECUTABLE=/app/codex/bin/codex

RUN groupadd -r echo && \
    useradd -r -g echo -d /home/echo -s /bin/false echo && \
    mkdir -p /home/echo /data /etc/echo /app/webui /app/resources /app/codex && \
    chown -R echo:echo /home/echo /data /etc/echo /app

COPY --from=py-builder /install /install
COPY --from=webui-builder /webui/dist/ /app/webui/
COPY --from=agent-bundle-verifier /build/agent-resources/ /app/resources/
COPY --from=agent-bundle-verifier /build/agent-codex/ /app/codex/
COPY --from=agent-bundle-verifier /build/agent-bundle.json /app/agent-bundle.json
COPY config.example.yaml /etc/echo/config.example.yaml
RUN chown -R echo:echo /app /etc/echo

# The entrypoint starts with only uid/gid/chown capabilities, repairs the
# bind-mounted state directory for ECHO_PUID/ECHO_PGID, then permanently drops
# to that unprivileged identity before importing or executing Agent runtime.
USER root
WORKDIR /data
EXPOSE 8000

# The unified distribution exposes both `echo` and `echo-agent` entry points.
ENTRYPOINT ["python", "-m", "appliance.entrypoint"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
