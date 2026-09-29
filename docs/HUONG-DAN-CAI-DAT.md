# Cài e2ea trên một máy khác

Hướng dẫn đưa e2ea sang máy mới — thường là **một máy chủ chạy Docker**, hoặc một máy dev
cài trực tiếp. Làm xong, máy đó tự quét Backlog, lập plan, viết code và mở MR trên GitLab.

| Bạn muốn | Làm theo |
|---|---|
| Chạy lâu dài trên máy chủ | [A. Docker trên máy chủ](#a-docker-trên-máy-chủ-khuyên-dùng) |
| Chạy trên máy dev, hoặc máy chủ không có Docker | [B. Cài trực tiếp](#b-cài-trực-tiếp) |
| Đăng nhập Claude trên máy không có trình duyệt | [Đăng nhập Claude trên máy chủ](#đăng-nhập-claude-trên-máy-chủ) |

---

## Trước khi bắt đầu

### 1. Dừng e2ea trên máy cũ

Hai máy cùng quét **một project Backlog** sẽ nhận trùng ticket và mở MR trùng — khoá
`watch.lock` chỉ có tác dụng trong một máy. Dừng máy cũ trước:

- đang chạy `e2ea up` / `e2ea watch` trong terminal: **Ctrl-C một lần** rồi đợi — nó dừng
  sau khi run đang chạy xong;
- đang chạy `e2ea serve`: tắt vòng quét bằng nút ở thanh bên, hoặc Ctrl-C;
- đang chạy Docker: `docker compose down`.

Ticket nào còn ở `agent_working` sau khi dừng thì máy mới sẽ tự giao cho người
(`human_needed`) — chuyển lại `agent_assign` là agent làm tiếp từ bước cuối cùng đã xong
(checkpoint nằm trong `runs/` và ref `refs/e2ea/*` của repo — chuyển máy thì mang theo cả hai,
không thì agent chạy lại phần viết code với plan đã duyệt). (Hồ sơ dùng
`tracker.state_field: category` thì tên tương ứng là `agent:running`, `agent:needs-human`,
`agent:try`.)

### 2. Chuẩn bị khoá

| Thứ cần | Lấy ở đâu | Ghi chú |
|---|---|---|
| Token GitLab | GitLab → Preferences → Access tokens (hoặc Project access token) | Vai trò **Developer** trở lên trên repo đích; scope `api` và `write_repository` |
| API key Backlog | Backlog → Cài đặt cá nhân → API | Tài khoản phải là thành viên project |
| Project Backlog | Mã project (vd `PROJKEY`) hoặc id số | Nên để *Text formatting rule* = **Markdown**, không thì comment plan hiện thô |
| Webhook Google Chat | Space → Apps & integrations → Webhooks | Tuỳ chọn |
| Đăng nhập Claude | Xem [mục riêng](#đăng-nhập-claude-trên-máy-chủ) | Làm **trước** bước sinh hồ sơ |

### 3. Mang gì từ máy cũ sang

| Thứ | Mang sang? | Vì sao |
|---|---|---|
| `.env` (và `.e2ea/repos/*.env` nếu dùng nhiều repo) | **Có**, qua kênh an toàn (scp), `chmod 600` | Chứa token |
| `profiles/<tên>.yaml` đã sửa tay | Chỉ để **đối chiếu** | Hồ sơ Python có đường dẫn tuyệt đối tới venv của máy cũ — sinh lại trên máy mới rồi chép các chỗ bạn đã sửa sang |
| `runs/` | Tuỳ chọn | Chỉ để giữ lịch sử và số đo (`e2ea metrics`) |
| `repos/`, `venvs/`, `work/` | **Không** | Máy mới tự clone, tự tạo venv; worktree cũ gắn với đường dẫn máy cũ |
| `~/.claude/` (đăng nhập Claude) | **Không** | Xem lý do ở mục đăng nhập |

---

## A. Docker trên máy chủ (khuyên dùng)

Ảnh Docker có sẵn Python 3.12, Node 22, git, Claude Code CLI, bubblewrap và socat (để
sandbox lệnh của agent). Một container giữ cả giao diện web lẫn vòng quét.

### A1. Yêu cầu máy chủ

- Docker Engine và Docker Compose **v2.24 trở lên** (`docker compose version`).
- Đi ra được mạng tới: GitLab, `*.backlog.com`, `api.anthropic.com`, `claude.ai`, và
  registry gói của repo đích (pypi.org, registry.npmjs.org…).
- Đủ đĩa cho repo đích + dependency của nó + vài worktree (thường vài GB).

### A2. Lấy code và build ảnh

```bash
git clone https://git.hblab.vn/<nhóm>/coding-agent.git
cd coding-agent
docker compose build
```

Repo đích không phải Python/JS thì ảnh cần thêm toolchain của nó, vd Go hoặc Java — sửa
`EXTRA_APT` trong `docker-compose.yml` rồi build lại:

```yaml
      args:
        EXTRA_APT: "golang-go"                               # repo Go
        # EXTRA_APT: "openjdk-17-jdk-headless maven"         # repo Java/Maven
```

<details>
<summary>Máy chủ không có quyền truy cập GitLab để <code>git clone</code>?</summary>

Build ở máy của bạn rồi chuyển ảnh sang:

```bash
docker compose build
docker save e2ea:latest | gzip | ssh user@may-chu 'gunzip | docker load'
```

Trên máy chủ vẫn cần file `docker-compose.yml` (chép riêng file đó sang), rồi chạy các
bước dưới như thường, bỏ `build`.
</details>

### A3. Thư mục làm việc và `.env`

```bash
mkdir workspace
cp .env.example workspace/.env
chmod 600 workspace/.env
nano workspace/.env              # điền repo_url, gitlab_token, backlog_*, …
```

Container chạy dưới uid **1000**. Nếu user của bạn trên máy chủ có uid khác (`id -u`):

```bash
sudo chown -R 1000:1000 workspace
```

> Token GitLab/Backlog để trong `workspace/.env`, **không** đặt vào `environment:` của
> compose. Sandbox chặn agent đọc file `.env`, nhưng trong container không gỡ được biến môi
> trường khỏi các lệnh Bash của agent.

### A4. Đăng nhập Claude

Cách khuyên dùng (chi tiết và các cách khác ở [mục riêng](#đăng-nhập-claude-trên-máy-chủ)):

```bash
docker compose run --rm --entrypoint claude e2ea auth login
```

Lệnh in ra một đường link. Mở link đó **trên máy của bạn** (máy có trình duyệt), đăng
nhập, rồi dán mã nhận được vào terminal của máy chủ. Thông tin đăng nhập nằm trong volume
`agent-home`, dùng lại được cho mọi lần chạy sau. Kiểm tra:

```bash
docker compose run --rm --entrypoint claude e2ea auth status
```

### A5. Sinh hồ sơ repo

```bash
docker compose run --rm e2ea init --dir /work
```

Lệnh này clone repo đích vào `workspace/repos/<tên>`, soi repo và ghi
`workspace/profiles/<tên>.yaml`:

- repo **Python**: tạo venv, cài dependency, suy lệnh test/lint/coverage;
- repo **ngôn ngữ khác**: agent Claude soi repo và đề xuất lệnh setup/build/test/lint, rồi
  hệ thống tự chạy lại từng lệnh để xác nhận (vài phút, tốn một ít chi phí Claude).

Đọc các dòng `# ĐOÁN:` và `# TODO:` ở đầu file hồ sơ. Còn `TODO` thì sửa file rồi chạy
lại lệnh trên. Mang hồ sơ cũ sang thì chép các chỗ bạn từng sửa tay (`allowed_paths`,
`limits`, `conventions`…) vào file mới — **đừng** chép cả file cũ đè lên.

> Làm được toàn bộ bước này bằng giao diện: tab **Cấu hình** → *Repo và hồ sơ* →
> **Clone và sinh hồ sơ**.

### A6. Tạo status trạng thái agent trên Backlog

Ticket phải chuyển được sang status `agent_assign` thì vòng quét mới thấy. Tạo đủ 3 status
(`agent_assign`, `agent_working`, `human_needed`) và 4 category kết cục một lần cho mỗi project —
cần quyền admin project và gói Backlog **Starter trở lên**. Ý nghĩa từng status: [README →
Trạng thái trên Backlog](../README.md#trạng-thái-trên-backlog). Project đã tạo 7 status của bản
cũ thì cứ để: status cũ vẫn được đọc, hết ticket nằm ở đó thì xoá.

```bash
docker compose run --rm e2ea labels-init --profile /work/profiles/<tên>.yaml --repo /work/repos/<tên>
```

(hoặc nút **Tạo nhãn** ở tab Cấu hình). Chạy lại không tạo trùng. Space không tạo được status
tự đặt (gói Free, không có quyền admin) thì thêm `state_field: category` vào khối `tracker:` của
hồ sơ rồi chạy lại lệnh trên — hệ thống dùng category `agent:*` thay cho status.

### A7. Chạy

```bash
docker compose up -d
docker compose logs -f           # Ctrl-C chỉ thoát xem log, container vẫn chạy
```

Có hồ sơ rồi thì container tự bật vòng quét khi khởi động. Nếu bạn sinh hồ sơ bằng giao
diện **sau** khi container đã chạy, bật vòng quét bằng nút ở thanh bên.

### A8. Mở giao diện từ máy của bạn

Giao diện chỉ nghe `127.0.0.1` của máy chủ, vì nó đọc và ghi được token. Mở bằng SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 user@may-chu
```

rồi vào <http://127.0.0.1:8080> trên máy của bạn. **Đừng** đổi cổng sang `0.0.0.0` hay mở
qua reverse proxy công khai: giao diện không có đăng nhập.

### A9. Cập nhật phiên bản

```bash
git pull
docker compose build
docker compose up -d             # dừng container cũ: đợi run đang chạy xong (tối đa 30 phút)
```

---

## B. Cài trực tiếp

Dùng cho máy dev, hoặc máy chủ không cài Docker. Ví dụ dưới cho Ubuntu/Debian.

### B1. Gói hệ thống

```bash
sudo apt install -y git python3 python3-venv bubblewrap socat
```

`bubblewrap` và `socat` là thứ bật sandbox cho lệnh của agent. Thiếu chúng e2ea vẫn chạy,
nhưng Bash của agent đọc được file `.env` — `e2ea doctor` sẽ báo ⚠.

Claude Code CLI cần **Node 22 trở lên** (Node của Ubuntu/Debian thường cũ hơn). Cài Node 22
cho **toàn máy** (vd NodeSource) nếu định chạy e2ea dưới một user riêng như ở B5 — Node cài
bằng nvm chỉ user cài nó mới thấy. Rồi:

```bash
node --version                               # phải ≥ v22
sudo npm install -g @anthropic-ai/claude-code
claude --version
```

Tuỳ chọn: `pip install uv` — để e2ea tự tải đúng bản Python mà repo đích yêu cầu nếu máy
chưa có.

### B2. Cài e2ea

```bash
git clone https://git.hblab.vn/<nhóm>/coding-agent.git
cd coding-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[web,dev]"
e2ea --help
```

### B3. Đăng nhập Claude

```bash
claude auth login        # in link → mở trên máy có trình duyệt → dán mã lại
claude auth status
```

Làm dưới **đúng user sẽ chạy e2ea** (xem [mục riêng](#đăng-nhập-claude-trên-máy-chủ)).

### B4. Thư mục làm việc, hồ sơ, chạy

Nên để thư mục làm việc **tách khỏi** thư mục mã nguồn:

```bash
mkdir -p ~/e2ea-work && cd ~/e2ea-work
cp ~/coding-agent/.env.example .env && chmod 600 .env
nano .env

e2ea init                  # clone + sinh profiles/<tên>.yaml — đọc ĐOÁN/TODO rồi sửa
e2ea up                    # doctor → tạo status trên Backlog → vòng quét trong terminal
```

Chạy `e2ea up` ít nhất một lần để tạo status trên Backlog. Sau đó, muốn có giao diện thì thay bằng:

```bash
e2ea serve --watch         # giao diện http://127.0.0.1:8080 + vòng quét trong một tiến trình
```

### B5. Chạy như service (systemd)

Chạy dưới một user **riêng**, không phải tài khoản cá nhân — agent có Bash và đọc được mọi
thứ user đó đọc được.

```bash
sudo useradd -m -s /bin/bash e2ea
sudo mkdir -p /srv/e2ea && sudo chown e2ea: /srv/e2ea
sudo -iu e2ea                                 # các lệnh sau chạy dưới user e2ea
  git clone https://git.hblab.vn/<nhóm>/coding-agent.git ~/coding-agent
  python3 -m venv ~/venv && ~/venv/bin/pip install -e "$HOME/coding-agent[web]"
  claude auth login                           # claude đã cài toàn máy ở B1
  cp ~/coding-agent/.env.example /srv/e2ea/.env && chmod 600 /srv/e2ea/.env && nano /srv/e2ea/.env
  cd /srv/e2ea && ~/venv/bin/e2ea init        # đọc ĐOÁN/TODO trong profiles/<tên>.yaml
  ~/venv/bin/e2ea up --once                   # doctor + tạo status, quét một vòng rồi thoát
  exit
sudo cp /home/e2ea/coding-agent/deploy/e2ea.service /etc/systemd/system/
sudo systemctl edit --full e2ea               # ExecStart=/home/e2ea/venv/bin/e2ea serve --dir /srv/e2ea --watch
sudo systemctl enable --now e2ea
journalctl -u e2ea -f
```

---

## Đăng nhập Claude trên máy chủ

Máy của bạn đã đăng nhập sẵn vì bạn từng bấm đăng nhập trên trình duyệt. Máy chủ không có
trình duyệt, nên chọn một trong các cách dưới.

| Cách | Hạn | Agent có đọc được không | Khi nào dùng |
|---|---|---|---|
| **1. `claude auth login`** (dán mã) | **30 ngày** rồi phải đăng nhập lại | Không — nằm trong file, sandbox chặn đọc | Mặc định, an toàn nhất |
| **2. `claude setup-token`** → `CLAUDE_CODE_OAUTH_TOKEN` | **1 năm** | Có, bằng `env` (token chỉ gọi được model, không vào được tài khoản) | Không muốn đăng nhập lại mỗi tháng |
| **3. `ANTHROPIC_API_KEY`** | Tới khi thu hồi | Có, bằng `env` | Muốn tính tiền theo token qua Console, không dùng gói cá nhân |
| 4. Chép `~/.claude/.credentials.json` từ máy của bạn | — | — | **Không nên**: hai máy dùng chung một phiên, máy này làm mới phiên có thể làm máy kia bị đăng xuất |

Tài liệu của Claude Code khuyên dùng **một tài khoản riêng cho máy tự động**, không dùng tài
khoản cá nhân. Nếu định dùng gói Pro/Max cá nhân cho một máy chủ cả nhóm dùng chung, kiểm
lại điều khoản gói với người quản lý; không thì dùng cách 3 hoặc gói Team/Enterprise.

### Cách 1 — `claude auth login` (dán mã)

| | Lệnh |
|---|---|
| Docker | `docker compose run --rm --entrypoint claude e2ea auth login` |
| Cài trực tiếp | `claude auth login` (dưới user chạy e2ea) |

Lệnh in một link → mở trên máy bất kỳ có trình duyệt → đăng nhập → dán mã lại vào terminal.
**Đặt lịch nhắc đăng nhập lại mỗi 30 ngày.** Hết hạn thì mọi run dừng ở `ERROR` với lý do
`agent … lỗi 2 lần: …` kèm thông báo về đăng nhập — chạy lại lệnh trên là xong, không cần
khởi động lại e2ea.

### Cách 2 — token một năm từ máy của bạn

Trên **máy của bạn** (đã đăng nhập, cần gói Claude có hỗ trợ):

```bash
claude setup-token        # in ra một token
```

Đưa token lên máy chủ:

- **Docker**: tạo file `claude.env` cạnh `docker-compose.yml` (đã có trong `.gitignore`):

  ```bash
  echo 'CLAUDE_CODE_OAUTH_TOKEN=<token>' > claude.env && chmod 600 claude.env
  docker compose up -d
  ```

- **Cài trực tiếp / systemd**: thêm `Environment=CLAUDE_CODE_OAUTH_TOKEN=<token>` (hoặc
  `EnvironmentFile=`) vào unit, hoặc `export` trong shell chạy e2ea.

**Không** để token này trong `workspace/.env`: e2ea không đọc khoá Claude từ đó.

### Cách 3 — khoá API

Tạo khoá ở Anthropic Console, rồi làm y như cách 2 với dòng `ANTHROPIC_API_KEY=<khoá>`.

### Kiểm tra đăng nhập

```bash
# Docker
docker compose run --rm --entrypoint claude e2ea auth status
docker compose run --rm --entrypoint claude e2ea -p "trả lời đúng một chữ: OK" --model haiku
# Cài trực tiếp
claude auth status && claude -p "trả lời đúng một chữ: OK" --model haiku
```

---

## Kiểm tra sau khi cài

```bash
# Docker
docker compose exec e2ea e2ea doctor --profile /work/profiles/<tên>.yaml --repo /work/repos/<tên>
# Cài trực tiếp (trong thư mục làm việc)
e2ea doctor --profile profiles/<tên>.yaml --repo repos/<tên>
```

Những dòng cần thấy:

| Dòng | Mong đợi |
|---|---|
| `hook chặn file cấm import được` | ✅ |
| `check test` / `check lint` | ✅ và đúng lệnh bạn muốn |
| `sandbox agent` | ✅ `bật (bubblewrap…)` — trong Docker là `chế độ nested (container)` |
| `backlog: định dạng Markdown` | ✅ không có ⚠ |
| `backlog: đủ status trạng thái agent` | ✅ `đủ` |

Rồi chạy thử một ticket thật: tạo ticket nhỏ trên Backlog (vd sửa một hàm có test), chuyển
status sang **`agent_assign`**. Trong 1–2 phút ticket sang `agent_working`, vài phút sau là
`human_needed` kèm comment plan, và được gán lại cho bạn. Duyệt (chọn lại `agent_assign` ở
khung comment, hoặc bấm Duyệt trên giao diện; muốn sửa plan thì viết góp ý cùng lần Submit)
→ agent viết code → `human_needed` kèm link MR.

Muốn chắc bản cài không hỏng mà không tốn tiền Claude (chỉ cho cài trực tiếp):

```bash
cd coding-agent && python -m pytest tests -q        # 56 test, không gọi Claude, không cần mạng
```

---

## Sự cố thường gặp

| Triệu chứng | Nguyên nhân | Cách sửa |
|---|---|---|
| `thiếu repo_url` | Chạy lệnh không đứng ở thư mục có `.env` | `cd` vào thư mục làm việc, hoặc thêm `--dir …` (Docker: `--dir /work`) |
| `clone thất bại … 401/403` | Token GitLab sai, hết hạn, thiếu scope, hoặc vai trò thấp hơn Developer | Tạo lại token với scope `api` + `write_repository` |
| `… → 401` từ Backlog | API key sai hoặc tài khoản không thuộc project | Kiểm tra key và quyền trên project |
| `doctor` báo `sandbox agent ⚠ chưa bật được (thiếu socat)` | Máy thiếu gói | `sudo apt install bubblewrap socat` |
| Docker: `sandbox agent ⚠ … No permissions to create new namespace` | Thiếu `security_opt` | Giữ nguyên hai dòng `security_opt` trong `docker-compose.yml` |
| Run `ERROR`: `agent … lỗi 2 lần` kèm lỗi đăng nhập | Claude chưa đăng nhập hoặc hết hạn (cách 1: 30 ngày) | Xem [đăng nhập Claude](#đăng-nhập-claude-trên-máy-chủ) |
| Run `ERROR`: `không thấy lệnh 'claude'` | Claude CLI không có trong PATH của user chạy e2ea | Cài lại cho đúng user; systemd: thêm thư mục chứa `claude` vào `Environment=PATH=…` |
| Agent báo `Sandbox is required but failed to initialize` | Có ai đặt tay biến `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` trên máy thiếu socat | Bỏ biến đó; e2ea tự bật khi máy chạy được |
| `đã có tiến trình watch khác đang chạy (pid …)` | Còn một vòng quét khác trên cùng thư mục làm việc | Dừng nó; tiến trình đó đã chết thì khoá tự được dọn ở lần chạy sau |
| Ticket bị xử lý hai lần, hai MR | Máy cũ chưa dừng | Xem [dừng e2ea trên máy cũ](#1-dừng-e2ea-trên-máy-cũ) |
| Mọi ticket `NO_MR/baseline_red` hoặc `ERROR … lệnh test không chạy được` | Môi trường test của repo đích chưa đúng (thiếu dependency, sai bản Python, thiếu toolchain trong ảnh Docker) | Chạy tay lệnh `commands.test` trong hồ sơ ngay trong `repos/<tên>`; Docker: thêm toolchain qua `EXTRA_APT` |
| Hồ sơ mang từ máy cũ: lệnh test trỏ tới `/home/<ai-đó>/…/venvs/…` | Hồ sơ Python chứa đường dẫn tuyệt đối | Cất bản cũ, chạy `e2ea init --force`, chép lại các chỗ đã sửa tay |
| Không mở được `http://…:8080` từ máy khác | Giao diện chỉ nghe localhost (cố ý) | Dùng SSH tunnel như [A8](#a8-mở-giao-diện-từ-máy-của-bạn) |
| Không có status `agent_assign` trong dropdown Status | Chưa tạo status | Chạy `labels-init` như [A6](#a6-tạo-status-trạng-thái-agent-trên-backlog), hoặc `e2ea up` |
| `labels-init` báo `không tạo được status …` | Gói Backlog không có status tự đặt, thiếu quyền admin, hoặc project đã đủ 8 status tự đặt | Nâng quyền/gói, xoá bớt status thừa, hoặc đặt `tracker.state_field: category` |
| Vòng quét báo `project chưa có status …` mỗi chu kỳ | Hồ sơ ở chế độ status nhưng chưa tạo status | Chạy `labels-init` |
