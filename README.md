# E2E Coding Agent — lớp cưỡng chế

Phần "phán quyết" của hệ thống: baseline, gate, anti-gaming. Đây là code có exit
code, không phải prompt — agent không đọc, không sửa, không tự chấm được
(nguyên tắc R-6 trong [docs/e2e-coding-agent-implementation-plan.md](docs/e2e-coding-agent-implementation-plan.md)).

Lớp "phán đoán" (Intake / Discovery / Planning / Test-gen / Implement) là skill của
Claude Code headless, nằm ở `skills/`.

**Hướng dẫn chạy full luồng: [docs/HUONG-DAN-SU-DUNG.md](docs/HUONG-DAN-SU-DUNG.md). Cách test ba mức (offline → Claude thật → Backlog/GitLab thật): [TESTING.md](TESTING.md).**

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
  skills/    5 skill cho agent (đi kèm trong gói)
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
```

```bash
cd thu-muc-lam-viec && e2ea up
```

`up` clone repo vào `repos/`, tạo venv riêng trong `venvs/<tên>` và cài dependency của
repo cùng pytest, pytest-cov, ruff vào đó (đọc `pyproject.toml` kể cả extras, `setup.py`,
`requirements*.txt`), thử `pytest --collect-only` để chắc môi trường chạy được, soi repo
để sinh `profiles/<tên>.yaml` (thư mục nguồn, thư mục test, lint, coverage — lệnh test trỏ
thẳng vào venv), chạy `doctor`, tạo 9 category trên Backlog rồi vào vòng `watch`. Từ đó chỉ cần tạo ticket với category `agent:try` trên Backlog và đổi
sang `agent:plan-approved` khi duyệt plan. Hồ sơ sinh ra ghi rõ từng điều đã đoán ở đầu
file; sửa tay xong thì `up` lần sau giữ nguyên. Thiếu hai dòng `backlog_*` hoặc `repo_url`
là đường dẫn trên đĩa thì chạy offline: ticket và MR ghi vào `profiles/backlog/`.

`e2ea init` chỉ làm bước clone, venv và sinh hồ sơ, để xem trước khi chạy. `--no-venv`
bỏ qua bước cài và dùng `python3` của máy.

Hai việc còn phải làm tay: đăng nhập `claude` trên máy chạy `up`, và duyệt plan trên Backlog.

## Dùng

```bash
e2ea doctor       --profile p.yaml --repo R      # soát hồ sơ với repo thật
e2ea labels-init  --profile p.yaml --repo R      # tạo label trên tracker

e2ea new-ticket --profile p.yaml --repo R --title "..." --file t.md   # tạo ticket để thử
e2ea phase-a  --profile p.yaml --repo R --ticket 42     # Intake → Discovery → Planning
e2ea approve  --profile p.yaml --repo R --ticket 42     # người duyệt → tạo ticket chi tiết
e2ea reject   --profile p.yaml --repo R --ticket 42 --why "..."
e2ea phase-b  --profile p.yaml --repo R --ticket 43     # Baseline → … → MR
e2ea watch    --profile p.yaml --repo R --interval 60   # VÒNG TỰ ĐỘNG: quét → xử lý → lặp
e2ea scan     --profile p.yaml --repo R                 # một vòng rồi thoát (cho cron)

e2ea check    --profile p.yaml --repo R --base-sha <sha> \
              --modules src/cart.py                       # gate + anti-gaming (CI dùng)
e2ea report   --run-dir runs/43/<run_id>
e2ea tickets  --profile p.yaml --repo R
e2ea label    --task-id 43 --outcome merged-as-is --test-value real --note "..."
e2ea metrics
e2ea reasons
```

**Exit code là hợp đồng với CI:** `0` đủ điều kiện mở MR · `1` NO_MR · `2` cần người.

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

Backlog không có label tự do như GitLab, nên 9 trạng thái agent map sang **category** —
`e2ea labels-init` tạo sẵn.

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

Mọi thứ đi qua một nguồn: `runs/<task_id>/<run_id>/events.jsonl` (máy đọc) và
console (người đọc). Mỗi run kết thúc bằng **đúng một** sự kiện `decision` mang mã
lý do — không có lối thoát im lặng, và có test canh điều đó. `e2ea report` dựng
`report.md` từ chính file sự kiện đó.

## Test

```bash
python3 scripts/e2e_offline.py   # trọn vòng ticket → MR, không LLM, không mạng (~30 giây)
python3 -m pytest tests/ -q      # 130 test, ~3 phút (tests/ hiện chưa nằm trong git)
```

Không dùng repo thật: `tests/conftest.py` dựng repo git tí hon ngay lúc chạy, mỗi
luật G có một fixture **cố tình gian lận** phải bị bắt và một fixture hợp lệ không
được bắt oan. `tests/test_pipeline.py` chạy trọn vòng ticket → MR bằng agent giả và
backlog trên đĩa, nên không cần LLM lẫn mạng.
