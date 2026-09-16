"""Ghép các bước thành Phase A / Phase B và vòng quét label."""


def verify_from_base(ctx, prof, repo, base_sha, work_root):
    """Dựng lại baseline và bằng chứng fail-trước NGAY TRÊN CI, không tin file agent nộp.

    Không có bước này thì G-1 và G-4 luôn out_of_scope khi CI chạy lại — mà đó
    đúng là hai luật nói nhiều nhất về gian lận.
    """
    from pathlib import Path

    from ..core import gitutil, parsers
    from ..config.profile import matches
    from ..enforce import baseline as baseline_mod
    from . import sandbox

    work = sandbox.create_detached(repo, Path(work_root).resolve() / "verify-base", base_sha)
    try:
        with ctx.stage("verify_baseline"):
            base = baseline_mod.capture(ctx, prof, work, strict=False)

        with ctx.stage("verify_fail_before"):
            tests = [f for f in gitutil.changed_files(repo, base_sha)
                     if matches(f, prof.test_globs)]
            evidence = {"tests_checked": tests}
            if not tests:
                evidence["note"] = "MR không đổi file test nào"
            else:
                # Chỉ mang file TEST của MR về code cũ. Test phải đỏ — nếu xanh
                # thì hoặc không có bug, hoặc test không kiểm cái nó tuyên bố kiểm.
                for rel in tests:
                    content = gitutil.file_at(repo, "HEAD", rel)
                    if content is not None:
                        target = work / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
                junit = ctx.run_dir / "verify-failbefore-junit.xml"
                ctx.cmd(prof.commands["test"].format(junit=str(junit)), cwd=work,
                        timeout=prof.limit("cmd_timeout_sec"))
                report = parsers.junit(junit)
                new_failures = sorted(set(report.failed) - set(base.failed_tests))
                evidence["failed_before"] = bool(new_failures)
                evidence["tests"] = new_failures[:20]
                evidence["passed_after"] = True   # gate trên HEAD đã chứng minh
                ctx.emit("verify.fail_before", failed=len(new_failures),
                         tests=new_failures[:5], level="info" if new_failures else "warn")
        return base, {"fail_before_pass_after": evidence}
    finally:
        sandbox.remove(repo, work)
