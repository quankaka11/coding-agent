"""Agent phải đọc được comment trên ticket — câu trả lời của người, chỉ dẫn sau khi duyệt."""
from e2e_agent.pipeline import handoff
from e2e_agent.pipeline.orchestrator import (AGENT_MARK, MR_MARK, REJECT_MARK, RUNNING_MARK,
                                             conversation)
from e2e_agent.tracker.backlog import BacklogTracker

PLAN = handoff.pack("## Plan chờ duyệt", base_sha="abc", spec_yaml="objective: x", plan="p")


def test_conversation_keeps_questions_and_answers_drops_machine_comments():
    comments = [
        f"{AGENT_MARK}\n{RUNNING_MARK} 1 r-1 -->\nAgent bắt đầu xử lý",
        f"{AGENT_MARK}\n**NO_MR/not_ready** — chưa rõ\n\n1. quantity mặc định là bao nhiêu?\n\nLog: `/x/runs/T-1/r-1`",
        "",                                             # Backlog: comment rỗng khi đổi category
        "quantity không có thì coi là 1",
        f"{AGENT_MARK}\n{PLAN}",
        f"{AGENT_MARK}\n{MR_MARK}\n**MR đã mở:** http://x",
    ]
    talk = conversation(comments)
    assert talk == [("agent", "**NO_MR/not_ready** — chưa rõ\n\n1. quantity mặc định là bao nhiêu?"),
                    ("human", "quantity không có thì coi là 1")]


def test_conversation_since_last_plan_only_human_after_approval():
    comments = ["câu trả lời cũ, trước plan", f"{AGENT_MARK}\n{PLAN}",
                f"{AGENT_MARK}\n{REJECT_MARK}\n**Plan bị từ chối (lần 1/2):** x",
                "dùng i.get('quantity', 1), đừng sửa hàm khác"]
    assert conversation(comments, since_last_plan=True) == [
        ("human", "dùng i.get('quantity', 1), đừng sửa hàm khác")]


def test_conversation_is_bounded():
    talk = conversation([f"c{i} " + "x" * 3000 for i in range(30)], limit=5, per_comment=100)
    assert len(talk) == 5 and talk[0][1].startswith("c25") and len(talk[0][1]) == 101


def test_backlog_comments_returns_newest_100_in_chronological_order(monkeypatch):
    tr = BacklogTracker("x.backlog.com", "P", api_key="k")
    seen = {}

    def fake_call(method, path, params=None):
        seen["params"] = dict(params)
        return [{"content": "mới nhất"}, {"content": "giữa"}, {"content": "cũ"}]   # API trả desc
    monkeypatch.setattr(tr, "_call", fake_call)
    assert tr.comments("P-1") == ["cũ", "giữa", "mới nhất"]
    assert seen["params"]["order"] == "desc"
