# E2E Coding Agent — lớp cưỡng chế

Phần "phán quyết" của hệ thống: baseline, gate, anti-gaming. Đây là code có exit
code, không phải prompt — agent không đọc, không sửa, không tự chấm được
(nguyên tắc R-6: luật chấm không bao giờ nằm trong prompt).

Lớp "phán đoán" (Intake / Discovery / Planning / Test-gen / Implement) là skill của
Claude Code headless, nằm ở `skills/`.

**Cài trên máy khác / máy chủ (Docker, đăng nhập Claude không cần trình duyệt): [docs/HUONG-DAN-CAI-DAT.md](docs/HUONG-DAN-CAI-DAT.md). Bắt đầu nhanh: mục [Đưa sang repo mới](#đưa-sang-repo-mới-một-lệnh) hoặc [Chạy như service](#chạy-như-service). Cách test ba mức (offline → Claude thật → Backlog/GitLab thật): [TESTING.md](TESTING.md).**

```
e2e_agent/
  core/      log, vòng chạy, mã kết cục, git, parser
  config/    hồ sơ repo
  enforce/   baseline, gate, anti-gaming, mutation   ← có exit code
  tracker/   nơi chứa TICKET: backlog (Nulab) · gitlab issues · file
  forge/     nơi chứa CODE: gitlab (push nhánh + MR) · file
  agent/     gọi Claude Code headless, schema spec
  pipeline/  sandbox, Phase A, Phase B, orchestrator, watcher
  notify/    thông báo ra ngoài: Google Chat / Slack / webhook
  skills/    7 skill cho agent (đi kèm trong gói): intake, discovery, planning, test_gen, implement, lint_fix, profile
  hooks/     PreToolUse hook chặn forbidden_paths
```

## Cài

```bash
pip install -e ".[dev]"     # hoặc: PYTHONPATH=. python3 -m e2e_agent.cli
```

## Đưa sang repo mới: một lệnh

Tạo một thư mục làm việc, trong đó có `.env`:

```
repo_url=https://git.hblab.vn/nhom/ten-repo.git
gitlab_token=...                 # vai trò Developer trở lên
backlog_space=xxx.backlog.com
backlog_project=PROJKEY
backlog_api_key=...
google_chat_webhook=...          # tuỳ chọn
agent_model=opus                 # tuỳ chọn: model cho claude headless (alias hoặc tên đầy đủ)
agent_effort=high                # tuỳ chọn: low|medium|high|xhigh|max
```

```bash
cd thu-muc-lam-viec && e2ea up
```

`up` clone repo vào `repos/`, sinh `profiles/<tên>.yaml`, chạy `doctor`, tạo status trạng thái
agent trên Backlog rồi vào vòng `watch`. Cách sinh hồ sơ tuỳ ngôn ngữ:

- **Python** (có `pyproject.toml`/`setup.py`/`requirements.txt`, hoặc chỉ có file `.py`):
  tất định — tạo venv riêng `venvs/<tên>`, cài dependency của repo cùng pytest, pytest-cov,
  ruff, thử `pytest --collect-only`, suy thư mục nguồn/test, lint, coverage. Bố cục
  `src/<gói>` được đặt `PYTHONPATH=src` để test chạy đúng code trong worktree của agent.
- **Ngôn ngữ khác** (Node/TS, Go, Java, PHP, Ruby, Rust…): **agent soi repo** (skill
  `profile`: đọc manifest, cấu hình CI, chạy thử) và đề xuất `setup`/`build`/`test`/`lint`,
  `allowed_paths`, `test_globs`. Hệ thống **tự chạy lại** từng lệnh để xác nhận (lệnh test
  phải ghi JUnit XML vào `{junit}` — file hoặc thư mục) trước khi ghi hồ sơ. `commands.setup`
  chạy trong mỗi worktree mới (worktree không có sẵn `node_modules`…). G-6 và mutation chỉ
  soi được Python, ở repo khác ghi `out_of_scope`.

Từ đó chỉ cần chuyển ticket sang status `agent_assign` trên Backlog; plan xong thì duyệt bằng
cách chuyển lại `agent_assign` — agent viết code và mở MR **ngay trên ticket đó** (xem
[Trạng thái trên Backlog](#trạng-thái-trên-backlog)).
Hồ sơ sinh ra ghi rõ từng điều đã đoán ở đầu file; sửa tay xong thì `up` lần sau giữ
nguyên. Thiếu hai dòng `backlog_*` hoặc `repo_url` là đường dẫn trên đĩa thì chạy offline:
ticket và MR ghi vào `profiles/backlog/`. Mẫu `.env`: [.env.example](.env.example).

`e2ea init` chỉ làm bước clone, venv và sinh hồ sơ, để xem trước khi chạy. `--no-venv`
bỏ qua bước cài và dùng `python3` của máy.

Hai việc còn phải làm tay: đăng nhập `claude` trên máy chạy `up`, và duyệt plan trên Backlog
(bỏ được bằng `conventions.auto_approve`).

## Trạng thái trên Backlog

Mặc định (`tracker.state_field: status`) trạng thái agent là **status** của ticket. Người đổi
status ngay ở khung comment cuối ticket: viết câu trả lời/lý do, chọn status, bấm **Submit**
một lần. Board của Backlog chia cột theo status, nên nhìn là biết ticket đang ở lượt ai.

Status chỉ nói **tới lượt ai**. Ba status tự đặt:

| Status | Ai đặt | Nghĩa |
|---|---|---|
| `agent_assign` | người | **Tới lượt agent.** Người xong phần mình thì chuyển về đây, kèm comment nếu muốn — agent tự biết làm bước nào (bảng dưới) |
| `agent_working` | agent | Agent đang chạy — đừng đặt tay |
| `human_needed` | agent | **Tới lượt người**: plan chờ duyệt, agent hỏi lại, NO_MR, lỗi, hay MR chờ review. Lý do ở comment cuối (kèm dòng **Tiếp theo:** nói chuyển `agent_assign` thì agent làm gì); category `agent:plan-ready` / `agent:no-mr` / `agent:needs-human` / `agent:mr-created` để lọc |

Xong việc thì chuyển Resolved/Closed như thường. Người chuyển `agent_assign` thì agent đọc lịch
sử ticket để biết cú chuyển đó nghĩa là gì — không LLM, chỉ đọc dấu máy trong comment
(`orchestrator.route()`):

| Agent vừa để lại | Người làm | Agent làm |
|---|---|---|
| (chưa có gì) / câu hỏi | tạo ticket / trả lời trong comment rồi chuyển | lập plan (đọc cả câu trả lời) |
| Plan chờ duyệt | chuyển, không comment | **duyệt** → viết code → MR |
| Plan chờ duyệt | góp ý trong comment rồi chuyển | sửa plan theo góp ý. Không mở rộng phạm vi (không thêm file ngoài `scope.modules` cũ, không đổi loại task) thì tự duyệt và làm tiếp; có mở rộng thì đưa plan mới lại cho người duyệt |
| Lỗi / NO_MR khi viết code | sửa nguyên nhân (token, hồ sơ…), comment nếu cần, rồi chuyển | **làm tiếp từ bước dừng** trên code đã có — không lập lại plan, không viết lại code (xem [Làm tiếp sau khi dừng](#làm-tiếp-sau-khi-dừng)) |
| MR đã mở | góp ý trong comment rồi chuyển | sửa trên nhánh MR, đẩy thêm commit, cập nhật mô tả MR |
| MR đã mở | chuyển, không comment | không có gì để làm, trả lại người |

Muốn **từ chối hẳn** plan (lập plan khác, luôn đưa lại duyệt) thì dùng nút Từ chối trên giao
diện hoặc `e2ea reject`. Plan lạc hậu (code trong phạm vi đổi trên nhánh gốc) thì agent tự lập
plan mới.

- Bốn status mặc định (Open, In Progress, Resolved, Closed) để nguyên cho đội; agent chỉ đụng
  ticket ở các status trên. Đang `agent_working` mà người kéo ticket sang status khác thì agent
  chạy nốt nhưng **không ghi đè** lựa chọn đó.
- Tới lượt người, agent gán ticket cho **người tạo ticket**; tới lượt agent thì gán cho tài khoản
  agent — Backlog tự gửi thông báo. Tắt bằng `tracker.assign: none`. Nên cho agent một tài khoản
  Backlog riêng (API key riêng): dùng chung tài khoản với người thì gán qua gán lại không báo ai.
- Status tự đặt cần gói Backlog **Starter trở lên** và quyền admin project (tối đa 8 status tự
  đặt; agent dùng 3). `e2ea labels-init` (hoặc nút **Tạo nhãn** ở tab Cấu hình) tạo sẵn;
  `e2ea doctor` báo nếu còn thiếu. Space không tạo được status thì đặt
  `tracker.state_field: category` để dùng category `agent:*`, đổi trong form sửa ticket
  (`agent:try` là lượt agent, như `agent_assign`).
- **Nâng từ bản 7 status:** các status cũ `human_review_plan`, `human_approve`, `human_reject`,
  `human_review_mr` vẫn được đọc để ticket đang nằm đó không kẹt (`human_approve` vẫn là duyệt),
  nhưng hệ thống không đặt chúng nữa. Hết ticket ở đó thì xoá trên Backlog.

## Luồng, chế độ, chạy song song

**Một ticket từ đầu tới MR.** `agent:try` → Phase A (đọc ticket, soi repo, lập plan) →
`agent:plan-ready` → người chuyển lại `agent:try` (= duyệt) → Phase B chạy ngay trên ticket đó
→ `agent:mr-created`, kèm comment link MR trên ticket. Không còn ticket `[impl]` con: plan và
spec người đã duyệt nằm sẵn trong comment bàn giao của chính ticket. Chuyển `agent:try` trên
plan đã ra MR mà không góp ý thì không mở MR thứ hai. (Ticket `[impl]` và trạng thái
`agent:plan-approved` của phiên bản cũ vẫn chạy được.)

### Làm tiếp sau khi dừng

Phase B ghi **checkpoint** sau mỗi bước (`runs/<ticket>/checkpoint.json`, commit giữ bằng ref
`refs/e2ea/<ticket>` nên `e2ea clean` không làm mất). Run dừng giữa chừng — token hết hạn lúc
mở MR, gate fail, hết ngân sách, tiến trình bị kill — thì người sửa nguyên nhân rồi chuyển
`agent:try`: agent dựng lại worktree tại đúng commit cũ, cùng nhánh, và:

- bỏ qua bước đã xong (baseline, sửa lint, viết test — mốc đóng băng G-9 giữ nguyên);
- bước dùng LLM (implement) làm tiếp **trên code đã có**; comment mới của người sau lần dừng
  → thêm một vòng implement theo comment đó;
- gate và anti-gaming luôn chạy lại, trừ khi code, hồ sơ repo và comment đều y nguyên so với
  lần đã chấm — vd token hết hạn lúc mở MR: sửa token, chuyển `agent:try` là mở MR luôn,
  không gọi agent lần nào.

Không làm tiếp được thì chạy lại Phase B từ đầu **với plan đã duyệt** (không lập lại plan):
plan/spec đã đổi, commit cũ không còn, hoặc lần trước dừng vì anti-gaming chặn cứng (bằng
chứng không còn tin được). Trước Phase B có **preflight** kiểm token GitLab (còn hạn, đủ quyền
Developer) — hỏng thì dừng trước khi tốn tiền cho agent (`NEEDS_HUMAN/forge_auth`). Token và
hồ sơ repo được đọc lại mỗi vòng quét: sửa trên giao diện hay `.env` là có hiệu lực, không phải
khởi động lại (đổi URL/project của tracker, forge hay model của agent thì vẫn cần).

**Chế độ** — `conventions.mode` trong hồ sơ:

| | `relaxed` (mặc định) | `strict` |
|---|---|---|
| Mục tiêu | ra MR; thiếu bằng chứng test thì MR là **Draft** kèm lý do | chỉ ra MR khi có bằng chứng test fail-trước/pass-sau |
| Ticket T4 (UI, cấu hình, prompt… không test tất định được) | vẫn làm, gate phải xanh → Draft MR | `NO_MR/t4` |
| Readiness chưa đạt | chỉ `clear` và `scoped` chặn; mục khác thành cảnh báo trên plan | mọi mục đều chặn |
| Test viết trước không chạy được / pass sẵn sau mọi lượt | bỏ test hỏng (hoặc giữ test pass sẵn làm test hồi quy) → implement → Draft MR | `NO_MR` |
| G-4 / G-7 / G-10 vẫn fail sau số vòng tự sửa | hạ xuống `needs_review` → Draft MR | `NO_MR/antigaming` |
| Sửa file dependency (`dependency_files`: package.json, pyproject.toml, go.mod…) | được, G-11 gắn cờ cần review; gate chạy lại `commands.setup` | G-11 fail → agent hoàn nguyên |
| G-1/G-2/G-3/G-5/G-9 (xoá test, tắt test, ra ngoài vùng, lệch commit, sửa test đã đóng băng) | **không bao giờ hạ** | như relaxed |

Gate (test + lint + build của repo) luôn phải xanh ở cả hai chế độ. Agent không đổi dòng
code nguồn nào mà gate vẫn xanh → `NO_MR/no_change`, không mở MR rỗng.

**Tự duyệt** — `conventions.auto_approve: [T1]`: plan loại T1 được tự duyệt, người chỉ còn
review MR. Plan vẫn được ghi lên ticket.

**Song song** — `limits.max_parallel: N`: tối đa N ticket chạy cùng lúc, mỗi ticket một
worktree; fetch và thêm/xoá worktree trên repo gốc đi qua một khoá. Vòng `watch` không đợi
run xong mới quét tiếp; dừng (Ctrl-C/SIGTERM) là đợi mọi run đang chạy xong rồi mới thoát.

**Dọn đĩa** — run ra MR tự xoá worktree của nó. Run NO_MR/cần người giữ worktree để xem
hiện trường; `e2ea clean --days 7` dọn cái cũ, `--branches` xoá cả nhánh local `agent/*`
(chỉ khi forge là GitLab — forge offline dùng nhánh local làm "MR").

## Chạy như service

**Docker** — ảnh gồm Python, Node 22, git, `claude` CLI, bubblewrap + socat; một tiến trình
giữ cả giao diện lẫn vòng quét (`serve --watch`):

```bash
mkdir workspace && cp .env.example workspace/.env        # điền token vào workspace/.env
docker compose run --rm --entrypoint claude e2ea auth login   # đăng nhập Claude, dán mã từ trình duyệt; lưu ở volume home
docker compose up -d --build                             # http://127.0.0.1:8080
docker compose run --rm e2ea init --dir /work            # tuỳ chọn: sinh hồ sơ từ CLI thay vì giao diện
```

Token để trong `workspace/.env`, **không** để trong `environment:` của container: sandbox
chặn agent đọc file đó, nhưng trong container không gỡ được biến môi trường khỏi Bash của
agent (xem [Cô lập agent](#cô-lập-agent)). Repo ngôn ngữ khác cần toolchain trong ảnh:
`--build-arg EXTRA_APT="golang-go"` (hoặc `FROM e2ea` rồi cài thêm).

**systemd** — [deploy/e2ea.service](deploy/e2ea.service): `e2ea serve --dir /srv/e2ea --watch`
dưới một user riêng.

## Dùng

```bash
e2ea doctor       --profile p.yaml --repo R      # soát hồ sơ với repo thật
e2ea labels-init  --profile p.yaml --repo R      # tạo label trên tracker

e2ea new-ticket --profile p.yaml --repo R --title "..." --file t.md   # tạo ticket để thử
e2ea phase-a  --profile p.yaml --repo R --ticket 42     # Intake → Discovery → Planning
e2ea approve  --profile p.yaml --repo R --ticket 42     # duyệt plan → vòng quét sau viết code, mở MR
e2ea reject   --profile p.yaml --repo R --ticket 42 --why "..."
e2ea phase-b  --profile p.yaml --repo R --ticket 42     # Baseline → … → MR, trên chính ticket đó
e2ea watch    --profile p.yaml --repo R --interval 60   # VÒNG TỰ ĐỘNG: quét → xử lý → lặp
e2ea serve    --dir .                                  # giao diện web trên localhost:8080
e2ea serve    --dir . --watch                          # giao diện + vòng quét trong một tiến trình
e2ea clean    --dir . --days 7                          # dọn worktree của run cũ
e2ea scan     --profile p.yaml --repo R                 # một vòng rồi thoát (cho cron)

e2ea check    --profile p.yaml --repo R --base-sha <sha> \
              --modules src/cart.py                       # gate + anti-gaming (CI dùng)
e2ea report   --run-dir runs/<repo>/43/<run_id>
e2ea tickets  --profile p.yaml --repo R
e2ea label    --task-id 43 --outcome merged-as-is --test-value real --note "..."
e2ea metrics
e2ea reasons
```

**Exit code là hợp đồng với CI:** `0` đủ điều kiện mở MR · `1` NO_MR · `2` cần người.

## Giao diện web

Thay cho vòng "gõ `.env` bằng tay → `e2ea up` chạy vài phút → mới biết token sai".
Giao diện đọc đúng những file mà CLI đọc (`.env`, `profiles/`, `runs/`) và đổi trạng
thái ticket qua đúng `tracker` mà orchestrator dùng — nó **không phải nguồn sự thật
thứ hai và không tự chạy pipeline**. Việc đó vẫn là của vòng quét.

### Chạy

```bash
pip install -e ".[web]"                  # fastapi + uvicorn
cd thu-muc-lam-viec && e2ea serve        # http://127.0.0.1:8080
e2ea serve --dir /duong/dan --port 9000  # thư mục làm việc khác, cổng khác
```

Bản build của giao diện nằm sẵn trong gói (`e2e_agent/web/static/`). Chưa build thì
`/` hiện hướng dẫn build, còn `/api/*` vẫn chạy.

### Lần đầu trên một thư mục trống

1. **Cấu hình → Kết nối**: điền ba nhóm Code / Ticket / Thông báo, bấm **Thử kết nối**
   từng nhóm, rồi **Lưu thay đổi**.
2. **Cấu hình → Repo và hồ sơ**: **Clone và sinh hồ sơ** (clone + venv + sinh
   `profiles/<tên>.yaml`, log chạy từng dòng) → đọc các dòng `TODO` trong hồ sơ, sửa ngay
   trong ô soạn → **Lưu và soát lại** → **Tạo 9 nhãn**.
3. Bật **vòng quét** bằng nút ở góc dưới bên trái. Từ đó tạo ticket `agent:try` trên
   tracker như bình thường; plan chờ duyệt sẽ hiện ở tab Trực.

Hai việc vẫn phải làm ngoài giao diện: đăng nhập `claude` trên máy chạy `serve`, và
đặt `agent_model`/`agent_effort` trong `.env` (cố ý không sửa được qua web).

### Bốn tab

**Trực** — màn mở cả ngày.
- Năm ô số: chờ bạn quyết · đang chạy (kèm số run mất tín hiệu) · chờ agent · MR đã mở ·
  chi phí đến nay.
- Danh sách việc của người: `agent:plan-ready` và `agent:needs-human`. Danh sách ticket
  đọc từ tracker, đệm 10 giây (kèm giờ đồng bộ); duyệt/từ chối xong thì làm mới ngay.
- **Duyệt plan ngay tại đây.** Trang plan đưa lên trước những thứ gật nhầm là tốn cả
  run: giả định agent tự chốt, phần cố ý không làm, phạm vi file được sửa (G-10), điều
  kiện nghiệm thu — rồi mới tới `plan.md`.
  - *Duyệt* ghi dấu duyệt lên ticket rồi chuyển `agent:try`; vòng quét sau chạy Phase B
    ngay trên ticket đó.
  - *Từ chối* bắt buộc có lý do; plan mới luôn đưa lại duyệt (khác góp ý qua comment, vốn
    được tự duyệt nếu không mở rộng phạm vi). Màn hình đếm số lần đã từ chối so với trần
    `MAX_REJECTS`.
- Run nổi bật (đang chạy → mất tín hiệu → gần nhất), tiến độ đẩy qua SSE.
- Các dòng `TODO` còn lại trong hồ sơ, và tình trạng vòng quét.

**Các lần chạy** — 25 run gần nhất, lọc theo Đang chạy / Cần người / Đã mở MR / Không ra
MR, tìm theo mã ticket. Mở một run là biên bản:
- kết luận, MR, thời gian, chi phí, kết quả gate;
- "Agent đã làm gì": mỗi bước một dòng kèm **số lượt tự sửa**;
- bằng chứng: test mới đỏ trên code cũ / xanh sau khi sửa, sha đóng băng test;
- **đủ mười ô G-1…G-10**, kể cả luật không chạy (hiện `skip`, không biến mất);
- run đã ra MR có ô **gán nhãn reviewer** — nguồn duy nhất của chỉ số false-green
  (tương đương `e2ea label`);
- "Chi tiết kỹ thuật" gấp lại: từng bước, bảng gate, mọi file của run (plan, spec,
  prompt, nhật ký agent…).

URL dạng hash (`#/run/<ticket>/<run_id>`, `#/truc/<ticket>`) nên F5 không mất chỗ và
gửi link được cho người khác.

**Cấu hình**
- *Kết nối* — sửa 6 khoá `.env`: `repo_url`, `gitlab_token`, `backlog_space`,
  `backlog_project`, `backlog_api_key`, `google_chat_webhook`. Mỗi nhóm có **Thử kết nối**
  dùng giá trị đang gõ, chưa cần lưu:

  | Nhóm | Thử bằng cách | Ghi chú |
  |---|---|---|
  | Code | `git ls-remote --heads` (repo trên đĩa thì chỉ kiểm có `.git`) | không clone |
  | Ticket | đọc project Backlog và danh sách category | báo thiếu mấy category; không tạo |
  | Thông báo | **gửi một tin thật** vào kênh | không có cách thử webhook mà không gửi |

  Ô bí mật để trống nghĩa là giữ giá trị đang lưu; ô nào đã gõ rồi xoá trắng rồi lưu thì
  **dòng đó bị xoá khỏi `.env`**. Lưu có thay đổi thì vòng quét của `serve` **dừng** (sau
  vòng hiện tại) để không chạy tiếp bằng cấu hình cũ — xem xong thì bật lại. Khoá nào
  đang bị biến môi trường của tiến trình che thì màn hình nói ra, vì `read_secret` đọc
  `os.environ` trước `.env`.
- *Repo và hồ sơ* — ô soạn hồ sơ yaml (kiểm cú pháp rồi chạy `doctor` ngay khi lưu),
  **Clone và sinh hồ sơ** (không đụng hồ sơ đã có), **Sinh lại hồ sơ** (ghi đè, bản cũ cất
  thành `<tên>.yaml.<giờ>.bak`), **Chạy doctor**, **Tạo 9 nhãn**; model/effort chỉ hiện để
  xem.

**Số đo** — chính `e2ea metrics` của repo đang bật: kết cục, lý do không ra MR, luật
anti-gaming bị chạm, kết quả review.

### Đang chạy hay mất tín hiệu

Chỉ gọi là *đang chạy* khi có nguồn hiện hành. Run chưa có `run.end` mà `events.jsonl`
không đổi quá **10 phút** thì hiện **mất tín hiệu** (màu cần người, có đếm ở thanh bên),
không phải "đang chạy" mãi mãi. Vòng quét quá ba chu kỳ không có nhịp thì hiện "có thể
đã treo". Có run đang chạy thì giao diện hỏi lại mỗi 5 giây, ngồi không thì 20 giây.

### Vòng quét trong `serve`

Nút bật/tắt chạy watcher bằng một thread nền **trong chính tiến trình `serve`**. Dừng là
dừng **sau** vòng hiện tại — giết ngang để lại ticket kẹt `agent:running` và sandbox bẩn,
nên không có nút giết ngang. Watcher do `e2ea watch`/`up` chạy ở terminal thì giao diện
chỉ hiện trạng thái, không bật/tắt được.

`watch.lock` nằm ở `runs/<tên repo>/watch/`. `e2ea up` và vòng quét của `serve` dùng
cùng đường đó nên không chạy chồng được; gõ tay `e2ea watch --profile …` thì phải kèm
`--runs-root runs/<tên repo>` thì khoá mới chặn được nhau.

### Nhiều repo trong một thư mục làm việc

Bộ chuyển repo ở thanh bên. `.env` luôn là repo **đang bật** (CLI chạy y như trước); các
repo đã cấu hình được cất ở `.e2ea/repos/<tên>.env`. Đổi repo = dừng vòng quét của
`serve` (đang dở một run thì từ chối, đợi xong), cất `.env` hiện tại, chép file của repo
kia đè lên. Vòng quét không tự bật lại.

`repos/`, `venvs/`, `work/`, `profiles/` vốn đã tách theo tên repo; `runs/` nay cũng
tách thành `runs/<tên repo>/` để số đo hai repo không trộn vào nhau. Hệ quả cho CLI:
đứng trong thư mục làm việc thì `e2ea metrics` và `e2ea label --task-id` tự đọc
`runs/<repo trong .env>/` (thư mục đó chưa có thì vẫn `runs/`); muốn repo khác thì
`--runs-root runs/<tên>`. `e2ea report --run-dir runs/<tên repo>/<task>/<run_id>`.

Sửa `.env` trên giao diện cũng cất lại cấu hình vào `.e2ea/repos/` (cả trước lẫn sau
khi ghi), nên đổi `repo_url` sang repo mới thì repo cũ vẫn nằm trong bộ chuyển, với
token mới nhất của nó.

Lần `serve` đầu tiên tự dồn `runs/<task>/` kiểu cũ vào `runs/<tên repo đang bật>/` — nhưng
**không dồn khi còn vòng quét đang giữ `runs/watch/watch.lock`**; dừng nó rồi chạy lại
`serve`. Run cũ của repo khác (nếu có) cũng bị dồn vào repo đang bật, nên bật đúng repo
trước khi chạy `serve` lần đầu.

### Bí mật

- Mặc định chỉ nghe `127.0.0.1`. Giao diện **không có đăng nhập**: `--host 0.0.0.0` là
  mở toàn quyền sửa `.env`, duyệt plan và bật vòng quét cho cả mạng.
- Mọi request ghi (POST/PUT/DELETE) phải kèm header `x-e2ea: 1` — trang web lạ đang mở
  trong trình duyệt không gửi được header này sang localhost, nên không tự duyệt plan
  hay bật vòng quét được (CSRF). Header `Host` phải là `127.0.0.1`/`localhost`/host đã
  bind, chặn DNS rebinding; bind `0.0.0.0` thì bỏ kiểm Host. Gọi API bằng `curl` thì nhớ
  thêm `-H 'x-e2ea: 1'`.
- Mã ticket, run, tên repo, tên file trong URL chỉ nhận `[A-Za-z0-9_][A-Za-z0-9._-]*`,
  không `..` — không đường nào đi ra ngoài thư mục run.
- `.env` và `.e2ea/repos/*.env` ghi quyền `0600`, qua file tạm rồi thay chỗ (`serve` chết
  giữa chừng không để lại `.env` cụt).
- Không endpoint nào trả giá trị token/webhook: chỉ "đã đặt" và ba ký tự cuối. Thử kết
  nối không cần trình duyệt gửi lại token đã lưu — server tự đọc.
- Output của lệnh con đi qua `scrub`/`redact_url` trước khi lên màn hình.

### Sửa giao diện

Mã nguồn ở `ui/` (React 18 + Vite + TypeScript, không thư viện UI). Chuẩn màu, chữ và
trạng thái: [ui/DESIGN.md](ui/DESIGN.md); từ vựng hiển thị gom ở `ui/src/lib/strings.ts`.
Tên bước, câu giải thích lý do/kết cục lấy từ server (`/api/reasons`), không chép vào UI.

```bash
cd ui && npm install
npm run dev      # cổng 5173, proxy /api sang 127.0.0.1:8080 (để `e2ea serve` chạy song song)
npm run build    # tsc + vite build, đổ vào e2e_agent/web/static/ — commit cả bản build
```

Phía server ở `e2e_agent/web/`:

| File | Việc |
|---|---|
| `app.py` | route FastAPI, chốt `x-e2ea`/Host, phục vụ `static/` |
| `workspace.py` | đọc trạng thái thư mục làm việc, che bí mật |
| `env_io.py` | ghi `.env` (giữ nguyên dòng không liên quan) |
| `probe.py` | thử kết nối, không ghi gì |
| `setup.py` · `jobs.py` | dựng repo / doctor / tạo nhãn; việc lâu chạy nền, log gom theo thread |
| `tickets.py` | hàng đợi ticket, duyệt/từ chối plan, nhãn reviewer |
| `run_detail.py` · `progress.py` | biên bản run, SSE, "đang chạy hay mất tín hiệu" |
| `watch.py` · `repos.py` | vòng quét trong tiến trình, nhiều repo, dồn `runs/` cũ |

## Agent tự sửa — người chỉ review khi thật cần

Quy tắc lấy từ hai skill nội bộ `e2e-agent` và `e2e-agent-setup`, áp vào pipeline này. Mục tiêu: agent xử lý được task tới MR; người chỉ ra tay khi
bằng chứng không còn tin được hoặc cần một quyết định máy không có quyền đoán.

| Tình huống | Trước | Bây giờ |
|---|---|---|
| Lint đỏ sẵn trên code chưa sửa | NO_MR | agent sửa bằng commit `chore:` riêng, rồi mới viết test (`limits.baseline_lint_fix`) |
| Test vừa viết không chạy được, pass sẵn trên code cũ, T2 mà đỏ, hoặc có assert rỗng / thiếu marker AC / skip / ra ngoài vùng | NO_MR hoặc người | agent viết lại theo feedback (`limits.testgen_fix_rounds`) rồi mới đóng băng |
| Test mới fail sau khi sửa code | luôn coi là lỗi agent | chạy lại `flaky_rerun` lần: fail đủ mọi lần = AGENT_INTRODUCED → agent sửa; không nhất quán = FLAKY → người |
| Anti-gaming fail: G-3, G-10 (ngoài vùng/phạm vi), G-7 (dòng mới thiếu test), G-4 mutation yếu, G-1/G-2 | cần người | agent nhận feedback, sửa, gate + anti-gaming lại (`limits.antigaming_fix_rounds`); hết vòng → `NO_MR/antigaming` |
| Anti-gaming G-9 (sửa test đã đóng băng) | cần người | hệ thống khôi phục test về mốc đóng băng (không cần LLM), agent sửa code lại |
| G-6 (heuristic assert/mock), G-8 (thiếu marker AC) | fail | `needs_review`: không chặn MR, bắt buộc nằm trong mô tả MR cho reviewer |
| G-4 thiếu bằng chứng fail-trước, G-5 gate lệch commit | cần người | vẫn `NEEDS_HUMAN/antigaming_evidence` — bằng chứng hỏng thì không ai được tự sửa |
| Coverage | không giảm **VÀ** ≥ ngưỡng dòng mới | không giảm **HOẶC** ≥ ngưỡng dòng mới (xoá code đã có test làm % tổng tụt là bình thường) |

**Intake không hỏi lại** (`conventions.intake_mode: assume`, mặc định): ticket mơ hồ thì
agent chọn cách hiểu **hẹp nhất, đúng chữ trên ticket**, ghi vào `assumptions`; việc liên
quan nhưng ticket không yêu cầu ghi vào `out_of_scope` và **không làm**. Cả hai in nổi trong
comment plan để người duyệt gật hoặc bác — plan vẫn là điểm duyệt duy nhất. `fail` readiness
chỉ khi không cách hiểu nào ra được test tất định. Repo muốn được hỏi thì đặt `intake_mode: ask`.

Agent viết test chỉ nhận acceptance criteria và mục **"Test sẽ viết"** của plan — không
nhận "Cách giải"/"File sẽ đụng" — để test không chép lại giả định của cách sửa. Plan
phải có đúng heading `## Test sẽ viết` và `## Rủi ro`; hai mục đó đi thẳng vào mô tả MR.

Mỗi luật anti-gaming khai `remedy` của nó (`implement` / `test_gen` / `restore_tests` /
không có = người) ngay trong `enforce/antigaming.py`; Phase B chỉ đọc kế hoạch đó, không
tự phân loại lại. Số vòng tự sửa ghi trong `evidence.json` → `self_fix` và trong mô tả MR.

### Kết cục: đúng 4 loại (`e2ea reasons`)

Theo skill e2e-agent, một run chỉ kết thúc bằng: ra MR, NO_MR, cần người, hoặc lỗi
hệ thống. Chi tiết "vì sao" nằm ở trường `kind` (log, `outcome.json`, comment ticket
dạng `NO_MR/plan_stale`), không phải một trạng thái riêng.

| Reason | Label | Người phải làm gì | `kind` |
|---|---|---|---|
| `OK` | `agent:mr-created` | review MR (MR `Draft:` — kiểm hành vi bằng tay, lý do ở đầu mô tả MR) | — |
| `NO_MR` | `agent:no-mr` | không gì ngay; agent đã thử và nói rõ vì sao. Với `not_ready`, comment ticket liệt kê từng mục chưa đạt kèm lý do và **câu hỏi cần trả lời** (`readiness_notes`, `questions` trong spec) — trả lời ngay trong ticket rồi đặt lại `agent:try` | `not_ready`, `t4`, `plan_rejected`, `plan_stale` (ticket tự về `agent:try`), `baseline_red`, `testgen_broken`, `test_passes_pre`, `t2_red`, `gate_fail`, `loop_limit`, `antigaming`, `no_change` |
| `NEEDS_HUMAN` | `agent:needs-human` | **phải quyết**: bằng chứng không còn tin được hoặc cấu hình/thao tác của người sai. Sửa xong chuyển `agent:try` là agent làm tiếp | `flaky`, `antigaming_evidence`, `misrouted`, `profile_gap`, `budget`, `stale_run`, `forge_auth` (preflight: token hỏng), `mr_failed` (code xong, push/mở MR hỏng) |
| `ERROR` | `agent:needs-human` | xem log | `system` |

Những chỗ trước đây dừng chờ người nay quay vòng cho agent trong cùng run (số lượt
trong `limits`):

- test viết ra không chạy được / pass sẵn trên code cũ / T2 mà đỏ → agent viết lại
  (`testgen_fix_rounds`), hết lượt mới `NO_MR`;
- gate và anti-gaming fail sửa được → agent sửa (`gate_fix_rounds`, `antigaming_fix_rounds`);
- lint đỏ sẵn → agent sửa bằng commit riêng;
- plan lạc hậu (code trong phạm vi đổi sau khi duyệt) → `NO_MR/plan_stale` và ticket **tự
  quay về `agent:try`**, agent lập plan mới trên code mới, người chỉ duyệt lại.

## Hai hợp đồng mà agent không được phá

**Test là hợp đồng (G-9).** Bước test-gen viết test, hệ thống commit lại và ghi mốc
đó vào `evidence.json`. Từ mốc ấy, mọi thay đổi trong `test_globs` đều là vi phạm —
kể cả khi code cuối cùng đúng. Không có luật này thì đổi `assert total == 93.6` thành
`assert True` là qua được hết: số test không giảm (G-1), vẫn có câu assert (G-6), và
bằng chứng fail-trước đã ghi từ trước khi test bị sửa (G-4).

**Plan là hợp đồng (G-10).** Chỉ được đụng `scope.modules` mà người đã duyệt. G-3 so
với `allowed_paths` của cả repo, rộng hơn một plan rất nhiều. Phạm vi lấy từ spec YAML
chứ không parse mục "File sẽ đụng" trong markdown — spec là dữ liệu có cấu trúc.
Tắt được bằng `conventions.enforce_plan_scope: false` nếu Intake hay khai phạm vi quá hẹp.

## Ticket và code là hai hệ tách rời

`tracker` đọc/ghi **ticket** (Backlog Nulab), `forge` push nhánh và mở **MR** (GitLab).
Chúng không biết gì về nhau: `tracker` không có khái niệm nhánh/MR, `forge` không có
khái niệm ticket. Gộp lại là lỗi thiết kế đã phải sửa một lần.

Backlog không có label tự do như GitLab, nên trạng thái agent map sang **status** (3 status,
xem [Trạng thái trên Backlog](#trạng-thái-trên-backlog)) hoặc **category** — `e2ea labels-init`
tạo sẵn.

## Hồ sơ repo

`profiles/*.yaml` là **file duy nhất** phải sửa khi đưa hệ thống sang repo mới —
không dòng nào trong `e2e_agent/` biết tên repo, đường dẫn hay lệnh của một dự án cụ thể
(có test canh: `tests/test_contract.py`). Đặt hồ sơ **ngoài** repo bị soi, nếu không
G-3 sẽ báo nó nằm ngoài `allowed_paths`.

Hai điều dễ vấp:

- `commands.test` phải chứa `{junit}`. Nên thêm `-o junit_family=xunit1` để junit ghi
  cả đường dẫn file, nhờ đó phân biệt được lỗi sẵn có trong/ngoài phạm vi sắp sửa (B1).
- Check nào chưa có tool thì khai `out_of_scope` **kèm lý do**. `doctor` sẽ chặn nếu
  khai `available` mà không tìm thấy lệnh. Không bao giờ có chuyện im lặng thành `pass`.

## CI đối chứng độc lập

Gate agent tự báo trong sandbox không được tin. Job này chạy lại trên mọi MR:

```yaml
agent_gate:
  image: python:3.12
  variables:
    E2EA_INDEX: "https://gitlab-ci-token:${CI_JOB_TOKEN}@<host>/api/v4/projects/${CI_PROJECT_ID}/packages/pypi/simple"
  script:
    - pip install -q pytest && pip install -q --index-url "$E2EA_INDEX" e2e-agent
    - e2ea check --profile ci/repo-profile.yaml --repo . --task-id "$CI_MERGE_REQUEST_IID"
        --base-sha "$CI_MERGE_REQUEST_DIFF_BASE_SHA" --run-dir runs/ci
        --task-meta-env CI_MERGE_REQUEST_DESCRIPTION
        --work-root "$CI_BUILDS_DIR/e2ea-work" --verify-from-base --quiet
    - e2ea report --run-dir runs/ci --quiet-report
  artifacts:
    when: always
    paths: [runs/ci]
  rules:
    - if: $CI_MERGE_REQUEST_LABELS =~ /agent-generated/
```

`--task-meta-env CI_MERGE_REQUEST_DESCRIPTION` đọc `task_type`, `scope.modules` và danh
sách AC từ payload ẩn mà Phase B nhúng vào mô tả MR — thiếu cờ này G-8 và G-10 luôn SKIP trên CI.

`--verify-from-base` là chỗ quan trọng: CI **tự dựng lại** baseline và bằng chứng
fail-trước từ base commit thay vì đọc file agent nộp. Thiếu cờ này thì G-1 và G-4 —
đúng hai luật nói nhiều nhất về gian lận — luôn ra `out_of_scope`.

## Hook chặn sớm

`hooks/forbidden_paths.py` là PreToolUse hook: chặn ghi vào `forbidden_paths` ngay
lúc agent định ghi, thay vì để nó sửa xong rồi mới rớt G-3. Đây là lưới thứ nhất;
G-3 vẫn chạy sau như lưới thứ hai vì hook không thấy `sed -i` chạy qua Bash.

## Log

Mọi thứ đi qua một nguồn: `runs/<repo>/<task_id>/<run_id>/events.jsonl` (máy đọc) và
console (người đọc). Mỗi run kết thúc bằng **đúng một** sự kiện `decision` mang mã
lý do — không có lối thoát im lặng, và có test canh điều đó. `e2ea report` dựng
`report.md` từ chính file sự kiện đó.

## Test

```bash
pip install -e ".[dev]"
python3 -m pytest tests -q -m "not slow"   # unit test, < 2 giây
python3 -m pytest tests -q                  # + trọn vòng offline (~1 phút)
python3 scripts/e2e_offline.py              # chạy riêng trọn vòng, in từng kịch bản
```

`scripts/e2e_offline.py`: ticket → MR không LLM, không mạng (StubAgent, tracker/forge trên
đĩa), 16 kịch bản: luồng 1 ticket, tự sửa G-10, lint đỏ sẵn, plan lạc hậu, relaxed/strict +
Draft MR, tự duyệt, chạy song song, đổi dependency, no_change, đọc comment trả lời trên ticket. `tests/` phủ hồ sơ/spec,
parser JUnit, luật relaxed (G-3/G-4/G-8/G-10/G-11, hạ mức), cô lập agent (môi trường sạch,
luật deny, sandbox, hook chặn ghi ngoài worktree) và sinh hồ sơ đa ngôn ngữ. CI:
[.gitlab-ci.yml](.gitlab-ci.yml).

## Cô lập agent

Agent có Bash, và nội dung ticket là đầu vào không tin cậy (prompt injection). Bốn lớp,
lớp sau không trông vào lớp trước:

| Lớp | Chặn gì | Khi nào có |
|---|---|---|
| Môi trường sạch | lệnh gate/test/setup không nhận token nào; `claude` chỉ giữ credential của chính nó | luôn |
| Luật deny (`.claude/settings.json` của worktree) | tool Read/Edit không mở được `.env`, `.e2ea/`, `~/.ssh`, `~/.git-credentials`, credential Claude… | luôn |
| Sandbox Claude Code (`agent.sandbox: auto\|on\|off`) | Bash không đọc được các file trên, không ghi ngoài worktree/cache, mạng chỉ tới registry gói (`agent.allowed_domains` để thêm) | máy có `bubblewrap` + `socat`; Docker cần `security_opt` unconfined (compose đã đặt) |
| Hook PreToolUse | tool ghi ra ngoài worktree (trừ `/tmp`, và không bao giờ vào thư mục làm việc) | luôn |

Đã thử với Claude thật: không sandbox thì `env` và tool Read bị chặn nhưng `cat .env` qua
Bash vẫn đọc được — nên `e2ea doctor` báo ⚠ khi sandbox chưa bật. Máy thật:
`sudo apt install bubblewrap socat`. Trong Docker sandbox chạy chế độ nested: token để trong
`workspace/.env` (sandbox chặn đọc), không để trong biến môi trường của container.
