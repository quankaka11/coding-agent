"""Dựng repo: clone → venv → hồ sơ → doctor → category.

Dùng lại đúng các hàm mà `e2ea init`/`up` gọi. Viết lại bước nào ở đây là tự
nhận một bản sao sẽ lệch dần với CLI.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ..tracker.base import ALL_LABELS
from . import jobs, workspace as ws_mod


def run(root: Path, *, no_venv: bool = False, force: bool = False) -> jobs.Job:
    """Job: clone (nếu thiếu) + venv + sinh hồ sơ. Không ghi đè hồ sơ đã sửa tay."""

    def work(job: jobs.Job) -> dict:
        from ..cli import _clone_if_missing, _layout, _prepare_env, _suggester
        from ..config import autoprofile
        args = SimpleNamespace(dir=str(root), no_venv=no_venv)
        lay = _layout(args)
        _clone_if_missing(lay)
        python, env_todos = _prepare_env(args, lay)

        if lay["profile"].exists() and not force:
            print(f"đã có hồ sơ {lay['profile'].name} — giữ nguyên")
        else:
            if lay["profile"].exists():
                # Hồ sơ là nơi người ghi những thứ máy không đoán được: biến môi
                # trường cho lệnh test, lý do tắt một check. Sinh lại là xoá hết
                # những dòng đó, nên luôn để lại một bản trước khi ghi đè.
                import shutil
                import time
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = lay["profile"].with_suffix(f".yaml.{stamp}.bak")
                shutil.copyfile(lay["profile"], backup)
                print(f"hồ sơ cũ đã cất ở {backup.name}")
            draft = autoprofile.generate(lay["repo"], lay["name"], tracker=lay["tracker"],
                                         forge=lay["forge"], notify=lay["notify"], python=python,
                                         suggest=_suggester(lay))
            draft.todos = env_todos + draft.todos
            lay["profile"].parent.mkdir(parents=True, exist_ok=True)
            lay["profile"].write_text(draft.dumps(), encoding="utf-8")
            print(f"đã sinh {lay['profile'].name}")
            for note in draft.notes:
                print(f"đoán: {note}")
            for todo in draft.todos:
                print(f"TODO: {todo}")
        return {"profile": str(lay["profile"]), "repo": str(lay["repo"]), "name": lay["name"]}

    return jobs.start("setup", work)


def doctor(root: Path) -> dict:
    """Soát hồ sơ với repo thật. Trả về từng mục cho giao diện dựng bảng."""
    from ..cli import PACKAGE_ROOT
    from ..config import profile as profile_mod
    from ..enforce import gate as gate_mod
    ws = ws_mod.load(root)
    if not ws.layout:
        return {"ok": False, "rows": [], "problem": ws.problem}
    path, repo = ws.layout["profile"], ws.layout["repo"]
    if not path.is_file():
        return {"ok": False, "rows": [], "problem": "chưa có hồ sơ repo"}
    prof = profile_mod.load(str(path))
    rows = [{"name": name, "ok": ok, "note": note}
            for name, ok, note in profile_mod.doctor(prof, repo, PACKAGE_ROOT)
            + profile_mod.tracker_rows(prof, path.parent)]
    rows += [{"name": f"check {check}", "ok": False, "note": "chưa khai available/out_of_scope"}
             for check in gate_mod.assert_all_declared(prof)]
    return {"ok": all(r["ok"] for r in rows), "rows": rows, "problem": None}


def labels_init(root: Path) -> dict:
    """Tạo trạng thái agent trên tracker (status hoặc category). Chạy lại được, không đẻ trùng."""
    from ..config import profile as profile_mod
    from .. import tracker as tracker_mod
    ws = ws_mod.load(root)
    if not ws.layout or not ws.layout["profile"].is_file():
        return {"ok": False, "created": [], "detail": "chưa có hồ sơ repo"}
    prof = profile_mod.load(str(ws.layout["profile"]))
    tracker = tracker_mod.make(prof, ws.layout["profile"].parent)
    try:
        created = tracker.ensure_labels(ALL_LABELS)
    except Exception as exc:          # thiếu quyền admin / gói không có status tự đặt
        return {"ok": False, "created": [], "detail": " ".join(str(exc).split())[:400]}
    return {"ok": True, "created": created,
            "detail": f"đã tạo {len(created)} mục mới: {', '.join(created)}" if created
                      else "đủ trạng thái agent trên tracker, không phải tạo thêm"}
