FROM debian:bookworm-slim

# Install Linux utilities, Python, and jq
RUN apt-get update && apt-get install -y \
    # Basics & editors
    bash coreutils findutils grep sed gawk less nano vim tree file which procps psmisc sudo \
    # Networking
    curl wget ca-certificates iputils-ping dnsutils netcat-traditional iproute2 \
    # Git + archives
    git unzip zip tar gzip bzip2 xz-utils \
    # Build tools
    build-essential make \
    # Python
    python3 python3-venv python3-pip \
    # JSON parsing
    jq \
    && rm -rf /var/lib/apt/lists/* \
    # GitHub CLI
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd sandbox && useradd -m -g sandbox sandbox \
    && mkdir -p /workspace && chown sandbox:sandbox /workspace

# Create Python venv and make it accessible to sandbox user
RUN python3 -m venv /opt/claude-venv \
    && /opt/claude-venv/bin/python -m pip install --upgrade pip \
    && chown -R sandbox:sandbox /opt/claude-venv

# Install Claude Code as sandbox user
USER sandbox
RUN curl -fsSL -o /tmp/install.sh https://claude.ai/install.sh \
    && bash /tmp/install.sh \
    && rm /tmp/install.sh

# Add venv + Claude to PATH
ENV HOME="/home/sandbox"
ENV PATH="/opt/claude-venv/bin:/home/sandbox/.local/bin:${PATH}"

# Pre-populate bash history with common sandbox commands (most recent last)
RUN cat <<'EOF' > /home/sandbox/.bash_history
claude --plugin-dir /plugins/evolve-lite --dangerously-skip-permissions
claude --dangerously-skip-permissions
EOF

WORKDIR /workspace

CMD ["bash"]
