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
    && rm -rf /var/lib/apt/lists/*

# Create Python venv and upgrade pip safely
RUN python3 -m venv /opt/claude-venv \
    && /opt/claude-venv/bin/python -m pip install --upgrade pip

# Install Claude Code (native installer)
#RUN curl -fsSL https://install.anthropic.com/claude-code/linux | bash
RUN curl -fsSL https://claude.ai/install.sh | bash

# Add venv + Claude to PATH
ENV PATH="/opt/claude-venv/bin:/root/.local/bin:${PATH}"

WORKDIR /workspace

CMD ["bash"]
