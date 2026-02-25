# Stage 1: uv로 패키지 설치
FROM python:3.14-slim-bookworm AS builder
ARG PAGEMAP_VERSION=0.7.2
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN uv venv /opt/pagemap && \
    uv pip install --python /opt/pagemap/bin/python \
        "retio-pagemap==${PAGEMAP_VERSION}"

# Stage 2: Chromium 사전 설치 + 비root
FROM python:3.14-slim-bookworm
COPY --from=builder /opt/pagemap /opt/pagemap
ENV PATH="/opt/pagemap/bin:$PATH"
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers

RUN playwright install --with-deps chromium

RUN groupadd -r pagemap && useradd -r -g pagemap -m pagemap && \
    chown -R pagemap:pagemap /opt/pw-browsers
USER pagemap
WORKDIR /home/pagemap

# MCP Registry 소유권 검증용 OCI 라벨
LABEL io.modelcontextprotocol.server.name="io.github.Retio-ai/pagemap"

EXPOSE 8000
STOPSIGNAL SIGTERM

ENTRYPOINT ["retio-pagemap"]
