from e2e_agent.core import parsers

CASE = '<testsuite><testcase classname="m" name="{n}">{body}</testcase></testsuite>'


def test_junit_reads_directory_of_reports(tmp_path):
    d = tmp_path / "junit.xml"          # đường dẫn {junit} được dùng như THƯ MỤC
    d.mkdir()
    (d / "a.xml").write_text(CASE.format(n="ok", body=""))
    (d / "sub").mkdir()
    (d / "sub" / "b.xml").write_text(CASE.format(n="bad", body="<failure/>"))
    rep = parsers.junit(d)
    assert rep.total == 2 and rep.failed == ["m::bad"]


def test_junit_skips_malformed_file(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "good.xml").write_text(CASE.format(n="ok", body=""))
    (d / "broken.xml").write_text("<testsuite><testcase")
    assert parsers.junit(d).total == 1
    bad = tmp_path / "single.xml"
    bad.write_text("không phải xml")
    assert parsers.junit(bad).total == 0


def test_reset_removes_stale_results(tmp_path):
    f = tmp_path / "gate-junit.xml"
    f.write_text(CASE.format(n="old", body=""))
    parsers.reset(f)
    assert not f.exists() and parsers.junit(f).total == 0
    d = tmp_path / "dir.xml"
    d.mkdir()
    (d / "x.xml").write_text("x")
    parsers.reset(d)
    assert not d.exists()
    parsers.reset(tmp_path / "missing.xml")      # không có gì để xoá thì im lặng
