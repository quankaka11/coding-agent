# e2ea — agent nhận ticket Backlog, sửa code, mở MR GitLab.
#
#   docker build -t e2ea .
#   docker build -t e2ea --build-arg EXTRA_APT="golang-go openjdk-17-jdk-headless maven" .
#
# Ảnh gốc có Python + Node (đủ cho repo Python/JS/TS). Repo ngôn ngữ khác cần toolchain
# của nó trong ảnh — thêm qua EXTRA_APT, hoặc viết Dockerfile riêng `FROM e2ea`.
# Claude Code CLI cần Node ≥ 22; Node của Debian bookworm là 18 → lấy từ ảnh chính thức.
FROM node:22-bookworm-slim AS node

FROM python:3.12-slim-bookworm

ARG EXTRA_APT=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl bubblewrap socat ${EXTRA_APT} \
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
 && node --version && npm --version

# Claude Code CLI — agent chạy `claude -p` headless.
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
# uv: tải đúng bản Python mà repo đích khai (xem autoprofile.find_python).
RUN pip install --no-cache-dir uv

WORKDIR /opt/e2ea
COPY pyproject.toml README.md ./
COPY e2e_agent ./e2e_agent
RUN pip install --no-cache-dir ".[web]"

RUN useradd -m -u 1000 agent && mkdir -p /work && chown agent:agent /work
USER agent
# Agent commit trong worktree; volume có thể thuộc uid khác nên tin mọi thư mục.
RUN git config --global user.name "e2e-agent" \
 && git config --global user.email "e2e-agent@localhost" \
 && git config --global --add safe.directory '*'

WORKDIR /work
VOLUME ["/work"]
EXPOSE 8080
ENTRYPOINT ["e2ea"]
# Một tiến trình giữ cả giao diện lẫn vòng quét. Chạy tay thì đổi CMD, vd `up --dir /work`.
CMD ["serve", "--dir", "/work", "--host", "0.0.0.0", "--port", "8080", "--watch"]
