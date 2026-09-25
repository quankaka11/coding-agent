#!/usr/bin/env bash
# Một lệnh: giao diện web + vòng quét Backlog trong cùng một tiến trình.
#
#   ./scripts/run.sh                 # http://127.0.0.1:8080, quét mỗi 60s
#   PORT=9090 INTERVAL=30 ./scripts/run.sh
#
# Thư mục làm việc là gốc repo này (.env, repos/, profiles/, runs/, work/ nằm ở đây).
# Ctrl-C một lần: đợi run đang chạy xong rồi mới thoát — đừng bấm lần hai.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
E2EA="$ROOT/.venv/bin/e2ea"
PORT="${PORT:-8080}"
INTERVAL="${INTERVAL:-60}"

die() { echo "❌ $*" >&2; exit 1; }

[[ -x "$E2EA" ]] || die "chưa có .venv — chạy: uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -e '.[web,dev]'"
[[ -f "$ROOT/.env" ]] || die "thiếu $ROOT/.env — chép từ .env.example rồi điền token"
command -v claude >/dev/null || die "không thấy lệnh 'claude' trong PATH"
claude auth status 2>/dev/null | grep -q '"loggedIn": true' || die "Claude chưa đăng nhập — chạy: claude auth login"
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
  die "cổng $PORT đang bận (có thể e2ea đang chạy ở terminal khác) — dừng nó hoặc đặt PORT=..."
fi
command -v socat >/dev/null || echo "⚠ thiếu socat: sandbox cho lệnh Bash của agent chưa bật (sudo apt install -y socat)"

cd "$ROOT"
exec "$E2EA" serve --dir "$ROOT" --port "$PORT" --watch --interval "$INTERVAL"
