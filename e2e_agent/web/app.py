"""Server của giao diện. Đọc từ đĩa, phục vụ bản build của ui/.

Mặc định chỉ nghe 127.0.0.1: thư mục làm việc chứa .env với token GitLab và
Backlog, không có lý do gì để nó nghe ra ngoài máy.

Ranh giới của lớp này: nó đọc, nó ghi .env và hồ sơ, nó đổi category ticket.
Nó KHÔNG tự chạy pipeline — việc đó là của vòng quét, và chỉ có một vòng quét.
"""
from __future__ import annotations

from pathlib import Path

from . import env_io, jobs, probe, repos, run_detail, setup as setup_mod
from . import tickets, watch, workspace as ws_mod

STATIC = Path(__file__).resolve().parent / "static"

#: Header mà giao diện gửi kèm mọi request ghi. Trang web lạ không gửi được header
#: tự đặt sang localhost mà không qua preflight CORS — và server không trả CORS —
#: nên thiếu nó là request từ chỗ khác: chặn. Không có chốt này thì bất kỳ tab
#: nào đang mở cũng POST được `/api/tickets/42/approve` (không cần body).
CSRF_HEADER = "x-e2ea"

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: Nghe mọi địa chỉ là người đã chủ động mở ra mạng; lúc đó không đoán được Host.
ANY_HOST = frozenset({"0.0.0.0", "::", ""})


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):                     # [::1]:8080
        return host[1:host.find("]")]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def create_app(root: Path, host: str = "127.0.0.1"):
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    root = Path(root).resolve()
    app = FastAPI(title="e2ea", docs_url=None, redoc_url=None)
    allowed = None if host in ANY_HOST else LOCAL_HOSTS | {host.lower()}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host lạ = DNS rebinding: tên miền của kẻ khác trỏ về 127.0.0.1 để đọc
        # được phản hồi như cùng nguồn.
        if allowed is not None and _hostname(request.headers.get("host", "")) not in allowed:
            return JSONResponse({"detail": "Host không hợp lệ"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": f"thiếu header {CSRF_HEADER}"}, status_code=403)
        return await call_next(request)

    def seg(*values: str) -> None:
        """Mã ticket/run/repo lấy từ URL: sai dạng là 404, không bao giờ chạm đĩa."""
        try:
            for value in values:
                ws_mod.safe_segment(value)
        except ValueError:
            raise HTTPException(404, "không có")

    def workspace() -> ws_mod.Workspace:
        # Đọc lại mỗi lần: .env và hồ sơ sửa được ngay trên giao diện, cache ở
        # đây nghĩa là người lưu xong vẫn thấy giá trị cũ.
        return ws_mod.load(root)

    # -- trạng thái ----------------------------------------------------------

    @app.get("/api/state")
    def api_state() -> dict:
        state = ws_mod.state(workspace())
        state["watcher"] = {**state["watcher"], **watch.status(root)}
        state["overridden"] = env_io.overridden_by_process()
        return state

    @app.get("/api/runs")
    def api_runs(limit: int = 25) -> dict:
        return {"runs": ws_mod.runs_index(workspace().runs_root, limit=limit,
                                          current=tickets.current_states(root))}

    @app.get("/api/metrics")
    def api_metrics() -> dict:
        """Rollup từ chính events.jsonl — không có đường thống kê riêng nào."""
        import dataclasses
        from .. import metrics as metrics_mod
        return dataclasses.asdict(metrics_mod.collect(workspace().runs_root))

    @app.get("/api/reasons")
    def api_reasons() -> dict:
        """Câu giải thích lấy thẳng từ core.reasons: giao diện không chép lại."""
        from ..core.reasons import EXPLAIN, EXPLAIN_KIND, LABEL
        from . import progress
        return {"reason": {k.value: v for k, v in EXPLAIN.items()},
                "kind": {k.value: v for k, v in EXPLAIN_KIND.items()},
                "label": {k.value: v for k, v in LABEL.items()},
                # Tên bước cũng lấy từ đây: SSE đẩy tên máy, và giao diện không
                # được giữ bản sao thứ hai của bảng dịch.
                "step": dict(progress.STEP_WORDS),
                "phase": dict(progress.PHASE_WORDS),
                "review": tickets.review_options()}

    # -- .env ----------------------------------------------------------------

    @app.get("/api/env")
    def api_env() -> dict:
        """Chỉ đặt/chưa đặt và ba ký tự cuối với khoá bí mật — không trả giá trị."""
        return {"keys": ws_mod.env_view(),
                "editable": list(ws_mod.EDITABLE_KEYS),
                "readonly": list(ws_mod.READONLY_KEYS),
                "overridden": env_io.overridden_by_process()}

    @app.put("/api/env")
    def api_env_write(updates: dict = Body(...)) -> dict:
        # Cất bản TRƯỚC khi ghi: người đổi repo_url sang repo khác thì repo cũ vẫn
        # còn trong bộ chuyển, với đúng token mới nhất của nó.
        repos.save_current(root)
        try:
            changed = env_io.write(root / ".env", {k: str(v) for k, v in updates.items()})
        except env_io.EnvError as exc:
            raise HTTPException(400, str(exc))
        repos.save_current(root)
        restart = watch.stop(root) if changed else {}
        return {"ok": True, "changed": changed, "keys": ws_mod.env_view(),
                "watcher": restart.get("detail")}

    # -- thử kết nối ---------------------------------------------------------

    def _secret(body: dict, key: str) -> str:
        """Khoá bí mật người chưa gõ lại thì lấy cái đang lưu — trình duyệt không
        bao giờ cầm token để gửi ngược lên."""
        from ..core.secrets import read_secret
        return (body.get(key) or "").strip() or read_secret(key)

    @app.post("/api/probe/code")
    def api_probe_code(body: dict = Body(...)) -> dict:
        return probe.code(body.get("repo_url", ""), _secret(body, "gitlab_token"))

    @app.post("/api/probe/tracker")
    def api_probe_tracker(body: dict = Body(...)) -> dict:
        return probe.tracker(body.get("backlog_space", ""), body.get("backlog_project", ""),
                             _secret(body, "backlog_api_key"))

    @app.post("/api/probe/notify")
    def api_probe_notify(body: dict = Body(...)) -> dict:
        return probe.notify(_secret(body, "google_chat_webhook"))

    # -- dựng repo -----------------------------------------------------------

    @app.post("/api/setup")
    def api_setup(body: dict = Body(default={})) -> dict:
        job = setup_mod.run(root, no_venv=bool(body.get("no_venv")), force=bool(body.get("force")))
        return job.view()

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str, since: int = 0) -> dict:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "không có job này")
        return job.view(since)

    @app.post("/api/doctor")
    def api_doctor() -> dict:
        return setup_mod.doctor(root)

    @app.post("/api/labels-init")
    def api_labels() -> dict:
        return setup_mod.labels_init(root)

    @app.get("/api/profile")
    def api_profile() -> dict:
        ws = workspace()
        path = ws.layout["profile"] if ws.layout else None
        text = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        return {"path": str(path) if path else None, "text": text,
                "view": ws_mod._profile_view(path) if path else None}

    @app.put("/api/profile")
    def api_profile_write(body: dict = Body(...)) -> dict:
        import yaml
        ws = workspace()
        if not ws.layout:
            raise HTTPException(400, ws.problem or "chưa có repo")
        text = body.get("text", "")
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise HTTPException(400, f"YAML sai: {' '.join(str(exc).split())[:200]}")
        ws.layout["profile"].write_text(text, encoding="utf-8")
        return {"ok": True, "doctor": setup_mod.doctor(root)}

    # -- vòng quét -----------------------------------------------------------

    @app.post("/api/watch")
    def api_watch(body: dict = Body(default={})) -> dict:
        if body.get("on", True):
            return watch.start(root, interval=int(body.get("interval") or 60))
        return watch.stop(root)

    # -- repo đã cấu hình ----------------------------------------------------

    @app.get("/api/repos")
    def api_repos() -> dict:
        return repos.listing(root)

    @app.post("/api/repos/{name}/activate")
    def api_repo_activate(name: str) -> dict:
        seg(name)
        result = repos.activate(root, name)
        tickets.invalidate(root)            # danh sách ticket đệm là của repo cũ
        return result

    @app.post("/api/repos/save")
    def api_repo_save() -> dict:
        name = repos.save_current(root)
        return {"ok": bool(name), "name": name}

    @app.delete("/api/repos/{name}")
    def api_repo_forget(name: str, purge: bool = False) -> dict:
        seg(name)
        return repos.forget(root, name, purge=purge)

    # -- ticket: duyệt plan --------------------------------------------------

    @app.get("/api/tickets")
    def api_tickets() -> dict:
        try:
            return tickets.queue(root)
        except RuntimeError as exc:
            import time as _t
            return {"error": str(exc), "by_label": {}, "waiting": 0, "fetched_at": _t.time()}

    @app.get("/api/tickets/{ticket_id}/plan")
    def api_plan(ticket_id: str) -> dict:
        seg(ticket_id)
        try:
            return tickets.plan(root, ticket_id)
        except Exception as exc:
            raise HTTPException(400, " ".join(str(exc).split())[:300])

    @app.post("/api/tickets/{ticket_id}/approve")
    def api_approve(ticket_id: str) -> dict:
        seg(ticket_id)
        return tickets.approve(root, ticket_id)

    @app.post("/api/tickets/{ticket_id}/reject")
    def api_reject(ticket_id: str, body: dict = Body(...)) -> dict:
        seg(ticket_id)
        return tickets.reject(root, ticket_id, body.get("why", ""))

    # -- run: biên bản -------------------------------------------------------

    @app.get("/api/runs/{task_id}/{run_id}")
    def api_run(task_id: str, run_id: str) -> dict:
        data = run_detail.detail(root, task_id, run_id)
        if not data["found"]:
            raise HTTPException(404, "không thấy run")
        return data

    @app.get("/api/runs/{task_id}/{run_id}/artifact/{name}", response_class=PlainTextResponse)
    def api_artifact(task_id: str, run_id: str, name: str) -> str:
        text = run_detail.artifact(root, task_id, run_id, name)
        if text is None:
            raise HTTPException(404, "không thấy file")
        return text

    @app.get("/api/runs/{task_id}/{run_id}/stream")
    def api_stream(task_id: str, run_id: str, from_line: int = 0):
        seg(task_id, run_id)
        return StreamingResponse(run_detail.stream(root, task_id, run_id, from_line),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{task_id}/{run_id}/review")
    def api_review(task_id: str, run_id: str, body: dict = Body(...)) -> dict:
        return tickets.review(root, task_id, run_id, body.get("outcome", ""),
                              body.get("test_value", ""), body.get("note", ""),
                              body.get("reviewer", ""))

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        def unbuilt() -> str:
            return _UNBUILT

    return app


#: Giao diện chưa build. Nói đúng việc phải làm, không xin lỗi.
_UNBUILT = """<!doctype html>
<html lang="vi"><meta charset="utf-8">
<title>e2ea — giao diện chưa build</title>
<style>
  body{background:#e7eaec;color:#15202e;font:15px/1.6 system-ui,sans-serif;
       margin:0;display:grid;place-items:center;min-height:100vh;padding:24px}
  div{max-width:44ch}
  h1{font-size:19px;margin:0 0 12px}
  code{background:#f5f7f8;padding:2px 5px}
  p{margin:0 0 12px}
</style>
<div>
  <h1>Giao diện chưa được build</h1>
  <p>API đã chạy: thử <code>/api/state</code>. Phần hiển thị cần build một lần:</p>
  <p><code>cd ui &amp;&amp; npm install &amp;&amp; npm run build</code></p>
  <p>Đang sửa giao diện thì chạy <code>npm run dev</code> ở <code>ui/</code> —
     cổng 5173 gọi thẳng sang server này.</p>
</div>
</html>"""


def serve(root: Path, host: str = "127.0.0.1", port: int = 8080, watch_interval: int | None = None) -> int:
    """`watch_interval`: bật luôn vòng quét trong tiến trình này (Docker/systemd) — một
    tiến trình giữ cả giao diện lẫn vòng quét, nên khoá watch không phải so PID giữa hai
    container vốn không nhìn thấy nhau."""
    import uvicorn
    root = Path(root).resolve()
    migration = repos.migrate_runs(root)
    if migration["blocked"]:
        print(f"CHƯA dồn được run cũ: {migration['reason']}")
    elif migration["moved"]:
        print(f"đã dồn {len(migration['moved'])} thư mục run cũ vào runs/<tên repo>/")
    repos.save_current(root)              # có .env là cất lại, để còn quay về
    print(f"e2ea → http://{host}:{port}   (thư mục làm việc: {root})")
    if watch_interval:
        from . import watch
        started = watch.start(root, interval=watch_interval)
        print(f"vòng quét: {started['detail']}" + ("" if started["ok"] else
              " — dựng repo ở Cấu hình rồi bật bằng nút ở thanh bên"))
    # uvicorn bắt SIGTERM/SIGINT, tắt server, rồi khôi phục handler CŨ và phát lại tín
    # hiệu. Handler cũ là mặc định thì tiến trình chết ngay tại đó — vòng quét (thread
    # daemon) bị giết giữa run: ticket kẹt `agent:running`, sandbox bẩn. Cài sẵn một
    # handler chỉ ghi nhận, để tín hiệu phát lại rơi vào đây và code dưới kịp chạy.
    import signal
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: None)
    uvicorn.run(create_app(root, host=host), host=host, port=port, log_level="warning")
    from . import watch
    if watch.status(root).get("mine"):
        print("đang đợi run hiện tại xong rồi mới thoát…", flush=True)
        watch.stop(root, timeout=None)
    return 0
