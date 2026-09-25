"""Backlog (Nulab) — nơi chứa ticket.

API v2, xác thực bằng apiKey trên query string. Máy trạng thái của agent (`agent:try`,
`agent:plan-ready`…) giữ nguyên; backend này chọn cách thể hiện nó ra Backlog:

- `state_field: status` (mặc định): mỗi trạng thái là một **status** tự đặt. Người đổi
  status ngay trong khung comment — viết câu trả lời/lý do và chọn status trong MỘT lần
  Submit — thay vì mở form sửa ticket để gỡ category cũ, gắn category mới. Status chỉ có
  một giá trị nên không thể mang hai trạng thái cùng lúc, và board chia cột theo status.
  Category chỉ còn là nhãn kết cục để lọc.
- `state_field: category`: cách cũ, cho space không tạo được status tự đặt.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from ..core.secrets import read_secret
from . import base as L
from .base import STATE_LABELS, Ticket, meta as _meta


class BacklogError(RuntimeError):
    pass


#: Backlog cố định 4 trạng thái chuẩn: 1 Open, 2 In Progress, 3 Resolved, 4 Closed.
#: Trạng thái tự tạo có id lớn hơn và đều được coi là "đang mở".
CLOSED_STATUS_ID = 4

#: Chế độ status: trạng thái logic → tên status trên Backlog. Bốn status mặc định (Open, In
#: Progress, Resolved, Closed) để nguyên cho đội dùng; agent chỉ đụng ticket ở các status này.
#: NO_MR và NEEDS_HUMAN chung một status: với người dùng cả hai đều là "tới lượt bạn xem",
#: lý do cụ thể nằm ở category kết cục và comment. Backlog cho tối đa 8 status tự đặt.
STATUS_OF = {
    L.TRY: "agent_assign",
    L.RUNNING: "agent_working",
    L.PLAN_READY: "human_review_plan",
    L.PLAN_APPROVED: "human_approve",
    L.PLAN_REJECTED: "human_reject",
    L.NO_MR: "human_needed",
    L.NEEDS_HUMAN: "human_needed",
    L.MR_CREATED: "human_review_mr",
}
#: Status → trạng thái logic. `human_needed` tách lại bằng category kết cục — chỉ để hiển
#: thị: orchestrator không bao giờ quét hai trạng thái đó.
_STATE_OF = {status: state for state, status in STATUS_OF.items() if state != L.NO_MR}
#: Nhãn kết cục (category). Agent ghi để người lọc được; không đọc để ra quyết định.
OUTCOME_TAG = {L.NO_MR: "agent:no-mr", L.NEEDS_HUMAN: "agent:needs-human",
               L.MR_CREATED: "agent:mr-created"}
#: Tới lượt người: gán ticket cho người tạo (tracker.assign=creator). Còn lại là lượt agent.
HUMAN_TURN = {L.PLAN_READY, L.NO_MR, L.NEEDS_HUMAN, L.MR_CREATED}
#: Thứ tự trên board / dropdown, và màu (API chỉ nhận 10 mã màu cố định).
_STATUS_COLOR = {"agent_assign": "#3b9dbd", "agent_working": "#868cb7",
                 "human_review_plan": "#eda62a", "human_approve": "#4caf93",
                 "human_reject": "#e07b9a", "human_needed": "#ea2c00",
                 "human_review_mr": "#b0be3c"}
_STATE_NAME = re.compile(r"`(agent:[a-z-]+)`")


class BacklogTracker:
    def __init__(self, space: str, project: str, api_key_env: str = "BACKLOG_API_KEY",
                 issue_type_id: str | int | None = None, timeout: int = 30,
                 api_key: str | None = None, state_field: str = "status",
                 assign: str = "creator") -> None:
        self.base = space.rstrip("/")
        if not self.base.startswith("http"):
            self.base = f"https://{self.base}"
        self.project = str(project)
        self.issue_type_id = issue_type_id
        self.timeout = timeout
        # `api_key` truyền thẳng: màn hình cấu hình thử khoá chưa lưu vào .env.
        self.api_key = api_key or read_secret(api_key_env)
        if not self.api_key:
            raise BacklogError(f"thiếu API key: đặt biến môi trường {api_key_env} hoặc dòng "
                               f"{api_key_env.lower()}=… trong .env")
        self.by_status = state_field == "status"
        self.assign = assign
        self._project_info: dict | None = None
        self._categories: dict[str, int] | None = None
        self._statuses: dict[str, int] | None = None
        self._open_statuses: list[int] | None = None
        self._me: dict | None = None

    # -- REST ------------------------------------------------------------
    def _call(self, method: str, path: str, params: list[tuple[str, str]] | None = None):
        url = f"{self.base}/api/v2{path}?apiKey={urllib.parse.quote(self.api_key)}"
        data = None
        if method == "GET" and params:
            url += "&" + urllib.parse.urlencode(params)
        elif params:
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            hint = " — kiểm tra API key và quyền trên project." if exc.code in (401, 403) else ""
            raise BacklogError(f"{method} {path} → {exc.code}: {detail}{hint}") from None

    # -- metadata project ------------------------------------------------
    @property
    def info(self) -> dict:
        if self._project_info is None:
            self._project_info = self._call("GET", f"/projects/{self.project}")
        return self._project_info

    def _category_ids(self, refresh: bool = False) -> dict[str, int]:
        if self._categories is None or refresh:
            self._categories = {c["name"]: c["id"]
                                for c in self._call("GET", f"/projects/{self.project}/categories")}
        return self._categories

    def _status_ids(self, refresh: bool = False) -> dict[str, int]:
        if self._statuses is None or refresh:
            self._statuses = {s["name"]: s["id"]
                              for s in self._call("GET", f"/projects/{self.project}/statuses")}
        return self._statuses

    def _needed(self, states: list[str]) -> tuple[list[str], list[str]]:
        """(status, category) phải có trên project để thể hiện được các trạng thái này."""
        if not self.by_status:
            return [], list(dict.fromkeys(states))
        wanted = {STATUS_OF[s] for s in states if s in STATUS_OF}
        statuses = [name for name in _STATUS_COLOR if name in wanted]      # thứ tự board
        tags = list(dict.fromkeys(OUTCOME_TAG[s] for s in states if s in OUTCOME_TAG))
        return statuses, tags

    def missing_states(self, states: list[str] | None = None) -> list[str]:
        """Status/category còn thiếu trên project — cho doctor và màn hình cấu hình."""
        statuses, tags = self._needed(states or L.ALL_LABELS)
        have_s = self._status_ids(refresh=True) if statuses else {}
        have_c = self._category_ids(refresh=True) if tags else {}
        return [s for s in statuses if s not in have_s] + [c for c in tags if c not in have_c]

    def ensure_labels(self, names: list[str]) -> list[str]:
        """Tạo trước status/category còn thiếu — tương đương labels-init bên GitLab."""
        statuses, tags = self._needed(names)
        created = []
        if statuses:
            have = self._status_ids(refresh=True)
            for name in statuses:
                if name in have:
                    continue
                try:
                    self._call("POST", f"/projects/{self.project}/statuses",
                               [("name", name), ("color", _STATUS_COLOR.get(name, "#393939"))])
                except BacklogError as exc:
                    raise BacklogError(
                        f"không tạo được status `{name}`: {exc}. Status tự đặt cần gói Backlog "
                        f"Starter trở lên, quyền admin project, và project còn chỗ (tối đa 8). "
                        f"Không được thì đặt `tracker.state_field: category` trong hồ sơ repo.") from None
                created.append(name)
            if created:
                self._order_statuses()
        if tags:
            existing = self._category_ids(refresh=True)
            for name in tags:
                if name not in existing:
                    self._call("POST", f"/projects/{self.project}/categories", [("name", name)])
                    created.append(name)
            self._category_ids(refresh=True)
        return created

    def _order_statuses(self) -> None:
        """Open → luồng agent → In Progress/Resolved/Closed. Hỏng thì thôi: chỉ là thứ tự."""
        ids = self._status_ids(refresh=True)
        flow = [ids[n] for n in _STATUS_COLOR if n in ids]
        rest = [i for n, i in ids.items() if i not in flow and i != 1]
        order = ([1] if 1 in ids.values() else []) + flow + rest
        try:
            self._call("PATCH", f"/projects/{self.project}/statuses/updateDisplayOrder",
                       [("statusId[]", str(i)) for i in order])
        except BacklogError:
            pass

    def words(self, text: str) -> str:
        """Tên trạng thái trong câu viết cho người: `agent:try` → `agent_assign`.

        Mọi comment và thông báo viết theo tên trạng thái logic; người dùng Backlog phải thấy
        đúng tên họ chọn được trong dropdown, không phải tên nội bộ.
        """
        if not self.by_status:
            return text
        return _STATE_NAME.sub(lambda m: f"`{STATUS_OF.get(m.group(1), m.group(1))}`", text)

    def formatting(self) -> tuple[bool, str]:
        """Project có hiển thị Markdown không (textFormattingRule).

        Comment của agent (plan, lý do NO_MR, link MR) viết bằng Markdown và mang dấu máy
        `<!-- e2ea:… -->`. Project để kiểu "backlog" thì người duyệt thấy `##`, `**`, và cả
        các dấu HTML thô — máy vẫn đọc đúng, nhưng plan gần như không đọc nổi.
        """
        rule = str(self.info.get("textFormattingRule") or "?")
        if rule == "markdown":
            return True, "project hiển thị Markdown"
        return False, (f"project đang dùng định dạng '{rule}' — comment plan của agent (Markdown) sẽ hiện "
                       "thô. Admin project: Project settings → General → Text formatting rule → Markdown")

    def whoami(self) -> dict:
        me = self._call("GET", "/users/myself")
        out = {"space": self.base, "project": self.info.get("projectKey"),
               "project_id": self.info.get("id"), "user": me.get("userId"),
               "state_field": "status" if self.by_status else "category",
               "categories": sorted(self._category_ids(refresh=True))}
        if self.by_status:
            out["statuses"] = list(self._status_ids(refresh=True))
        return out

    def _myself(self) -> dict:
        if self._me is None:
            self._me = self._call("GET", "/users/myself")
        return self._me

    # -- ticket ----------------------------------------------------------
    def _to_ticket(self, raw: dict) -> Ticket:
        body = raw.get("description") or ""
        labels = [c["name"] for c in raw.get("category") or []]
        if self.by_status:
            # Status là nguồn sự thật duy nhất: category `agent:*` sót lại từ chế độ cũ
            # không được làm ticket trông như đang ở trạng thái khác.
            state = _state_of((raw.get("status") or {}).get("name"), labels)
            labels = ([state] if state else []) + [c for c in labels if c not in L.ALL_LABELS]
        return Ticket(
            id=raw["issueKey"], title=raw.get("summary", ""), body=body, labels=labels,
            url=f"{self.base}/view/{raw['issueKey']}",
            parent=_meta(body, "parent"), base_sha=_meta(body, "base_sha"))

    def _open_status_ids(self) -> list[int]:
        """Mọi trạng thái trừ Closed. Không đọc được thì trả rỗng = không lọc."""
        if self._open_statuses is None:
            try:
                statuses = self._call("GET", f"/projects/{self.project}/statuses")
                self._open_statuses = [int(s["id"]) for s in statuses
                                       if int(s["id"]) != CLOSED_STATUS_ID]
            except (BacklogError, KeyError, ValueError):
                self._open_statuses = []
        return self._open_statuses

    def list_by_label(self, label: str) -> list[Ticket]:
        if self.by_status:
            return self._list_by_status(label)
        ids = self._category_ids()
        if label not in ids:
            return []
        params = [("projectId[]", str(self.info["id"])), ("categoryId[]", str(ids[label])),
                  ("count", "50"), ("sort", "created"), ("order", "asc")]
        # Ticket đã Closed nhưng còn mang category `agent:try` không được chạy lại
        # — GitLab lọc `state=opened`, Backlog phải lọc bằng statusId.
        params += [("statusId[]", str(i)) for i in self._open_status_ids()]
        return [self._to_ticket(x) for x in self._call("GET", "/issues", params)]

    def _list_by_status(self, label: str) -> list[Ticket]:
        status = STATUS_OF.get(label)
        if status is None:
            return []                   # vd `agent:impl` của phiên bản cũ: không có status
        ids = self._status_ids()
        if status not in ids:
            ids = self._status_ids(refresh=True)
        if status not in ids:
            # Nói to thay vì trả rỗng: trả rỗng là vòng quét chạy mãi mà không nhận ticket nào.
            raise BacklogError(f"project chưa có status `{status}` — chạy `e2ea labels-init` "
                               f"(hoặc nút Tạo nhãn ở tab Cấu hình)")
        params = [("projectId[]", str(self.info["id"])), ("statusId[]", str(ids[status])),
                  ("count", "50"), ("sort", "created"), ("order", "asc")]
        tickets = [self._to_ticket(x) for x in self._call("GET", "/issues", params)]
        return [t for t in tickets if label in t.labels]

    def get(self, ticket_id: str) -> Ticket:
        return self._to_ticket(self._call("GET", f"/issues/{ticket_id}"))

    def comment(self, ticket_id: str, body: str) -> None:
        self._call("POST", f"/issues/{ticket_id}/comments", [("content", body)])

    def comments(self, ticket_id: str) -> list[str]:
        """100 comment MỚI NHẤT, trả về cũ → mới.

        API trả tối đa 100 mỗi lần. `order=asc` là 100 cái CŨ nhất — ticket trao đổi dài thì
        đúng câu trả lời mới nhất của người, plan mới nhất và mốc chạy mới nhất bị cắt mất.
        """
        raw = self._call("GET", f"/issues/{ticket_id}/comments",
                         [("count", "100"), ("order", "desc")])
        return [c.get("content") or "" for c in reversed(raw)]

    def set_state(self, ticket_id: str, label: str) -> None:
        """Gỡ hết category trạng thái cũ, đặt cái mới. Category khác giữ nguyên."""
        if self.by_status:
            return self._set_status(ticket_id, label)
        ids = self._category_ids()
        if label not in ids:
            self.ensure_labels([label])
            ids = self._category_ids()
        current = self._call("GET", f"/issues/{ticket_id}").get("category") or []
        # id lấy từ ticket, không tra qua cache (cache cũ hơn category đội vừa tạo).
        keep = [c["id"] for c in current if c["name"] not in STATE_LABELS] + [ids[label]]
        self._call("PATCH", f"/issues/{ticket_id}", [("categoryId[]", str(i)) for i in keep])

    def _set_status(self, ticket_id: str, label: str) -> None:
        """Đổi status; category kết cục thay cho cái cũ; gán người theo lượt."""
        status = STATUS_OF.get(label)
        if status is None:
            raise BacklogError(f"`{label}` không có status tương ứng ở chế độ state_field=status")
        if status not in self._status_ids() or (
                label in OUTCOME_TAG and OUTCOME_TAG[label] not in self._category_ids()):
            self.ensure_labels([label])
        raw = self._call("GET", f"/issues/{ticket_id}")
        # id category của đội lấy thẳng từ ticket: cache có thể cũ hơn category đội vừa tạo,
        # và tra qua cache thì category đó bị gỡ khỏi ticket trong im lặng.
        keep = [c["id"] for c in raw.get("category") or [] if c["name"] not in L.ALL_LABELS]
        keep += [self._category_ids()[OUTCOME_TAG[label]]] if label in OUTCOME_TAG else []
        params = [("statusId", str(self._status_ids()[status]))]
        params += [("categoryId[]", str(i)) for i in keep] or [("categoryId[]", "")]
        who = self._assignee(label, raw)
        try:
            self._call("PATCH", f"/issues/{ticket_id}",
                       params + ([("assigneeId", str(who))] if who else []))
        except BacklogError:
            if not who:
                raise
            # Người tạo đã rời project thì không gán được — đổi status vẫn phải xong.
            self._call("PATCH", f"/issues/{ticket_id}", params)

    def _assignee(self, label: str, raw: dict) -> int | None:
        if self.assign != "creator":
            return None
        if label in HUMAN_TURN:
            return (raw.get("createdUser") or {}).get("id")
        return self._myself().get("id")

    def create_ticket(self, title: str, body: str, labels: list[str],
                      parent: str | None = None) -> Ticket:
        if parent:
            body = f"<!-- parent: {parent} -->\n{body}"
        ids = self._category_ids()
        missing = [l for l in labels if l not in ids]
        if missing:
            self.ensure_labels(missing)
            ids = self._category_ids()
        params = [("projectId", str(self.info["id"])), ("summary", title),
                  ("description", body),
                  ("issueTypeId", str(self._default("issueTypes"))),
                  ("priorityId", str(self._default("priorities")))]
        params += [("categoryId[]", str(ids[l])) for l in labels if l in ids]
        ticket = self._to_ticket(self._call("POST", "/issues", params))
        # Chế độ status: ticket mới luôn ra đời ở Open — đưa nó vào đúng trạng thái sau.
        if self.by_status and (state := next((l for l in labels if l in STATUS_OF), None)):
            self._set_status(ticket.id, state)
            ticket = self.get(ticket.id)
        return ticket

    def notify(self, ticket_id: str, message: str) -> None:
        self.comment(ticket_id, message)

    def _default(self, kind: str) -> int:
        """issueType lấy của project, priority lấy toàn space.

        Priority ưu tiên "Normal" — lấy mục đầu tiên sẽ ra "High", tự nâng mức
        khẩn cho mọi ticket agent tạo là sai. issueType thì lấy mục đầu tiên
        (thường là Task), hồ sơ repo khai đè được.
        """
        if kind == "issueTypes":
            if override := self.issue_type_id:
                return int(override)
            items = self._call("GET", f"/projects/{self.project}/issueTypes")
        else:
            items = self._call("GET", "/priorities")
            for item in items:
                if item["name"].lower() == "normal":
                    return item["id"]
        if not items:
            raise BacklogError(f"project không có {kind} nào")
        return items[0]["id"]


def _state_of(status: str | None, categories: list[str]) -> str | None:
    """Status → trạng thái logic. `human_needed` tách NO_MR/NEEDS_HUMAN bằng category."""
    if status == STATUS_OF[L.NO_MR]:
        return L.NO_MR if OUTCOME_TAG[L.NO_MR] in categories else L.NEEDS_HUMAN
    return _STATE_OF.get(status or "")
