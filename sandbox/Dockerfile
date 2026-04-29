FROM debian:bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    findutils \
    grep \
    sed \
    gawk \
    less \
    nano \
    vim \
    tree \
    file \
    which \
    procps \
    psmisc \
    sudo \
    curl \
    wget \
    ca-certificates \
    iputils-ping \
    dnsutils \
    netcat-traditional \
    iproute2 \
    git \
    unzip \
    zip \
    tar \
    gzip \
    bzip2 \
    xz-utils \
    build-essential \
    make \
    python3 \
    python3-venv \
    python3-pip \
    jq \
    openssh-client \
    ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && JLESS_URL=$(curl -fsSL https://api.github.com/repos/PaulJuliusMartinez/jless/releases/latest \
       | grep -o '"browser_download_url": *"[^"]*x86_64-unknown-linux-gnu.zip"' \
       | cut -d'"' -f4) \
    && if [ -z "$JLESS_URL" ]; then echo "ERROR: failed to resolve jless download URL from GitHub API" >&2; exit 1; fi \
    && curl -fsSL "$JLESS_URL" -o /tmp/jless.zip \
    && unzip -o /tmp/jless.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/jless \
    && rm /tmp/jless.zip

RUN groupadd sandbox && useradd -m -g sandbox sandbox \
    && mkdir -p /workspace && chown sandbox:sandbox /workspace

RUN python3 -m venv /opt/claude-venv \
    && /opt/claude-venv/bin/python -m pip install --upgrade pip \
    && chown -R sandbox:sandbox /opt/claude-venv


# Claude Code target
FROM base AS claude

USER sandbox

RUN curl -fsSL -o /tmp/install.sh https://claude.ai/install.sh \
    && bash /tmp/install.sh \
    && rm /tmp/install.sh

ENV HOME="/home/sandbox"
ENV PATH="/opt/claude-venv/bin:/home/sandbox/.local/bin:${PATH}"

RUN cat <<'EOF' > /home/sandbox/.bash_history
claude --plugin-dir /plugins/evolve-lite --dangerously-skip-permissions
claude --dangerously-skip-permissions
EOF

WORKDIR /workspace

CMD ["bash"]


# Codex target
FROM base AS codex

COPY --from=node:20-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:20-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN npm install --global @openai/codex@0.122.0 \
    && npm cache clean --force

RUN mkdir -p /codex-home \
    && chown sandbox:sandbox /codex-home \
    && chmod 0700 /codex-home

COPY codex/bootstrap_codex_config.py /usr/local/bin/bootstrap_codex_config.py
COPY codex/entrypoint.sh /usr/local/bin/codex-container-entrypoint

RUN chmod 0755 /usr/local/bin/bootstrap_codex_config.py /usr/local/bin/codex-container-entrypoint

WORKDIR /workspace

USER sandbox

ENV HOME="/home/sandbox"
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["codex-container-entrypoint"]
CMD ["bash"]
