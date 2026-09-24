"""Backlog (Nulab) — nơi chứa ticket.

API v2, xác thực bằng apiKey trên query string. Trạng thái agent biểu diễn bằng
**category** vì Backlog không có label tự do như GitLab: category là chuỗi tự do,
nhiều giá trị, nên map gần 1-1 với máy trạng thái đã thiết kế.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from ..core.secrets import read_secret
from .base import STATE_LABELS, Ticket, meta as _meta


class BacklogError(RuntimeError):
    pass


#: Backlog cố định 4 trạng thái chuẩn: 1 Open, 2 In Progress, 3 Resolved, 4 Closed.
#: Trạng thái tự tạo có id lớn hơn và đều được coi là "đang mở".
CLOSED_STATUS_ID = 4


class BacklogTracker:
    def __init__(self, space: str, project: str, api_key_env: str = "BACKLOG_API_KEY",
                 issue_type_id: str | int | None = None, timeout: int = 30,
                 api_key: str | None = None) -> None:
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
        self._project_info: dict | None = None
        self._categories: dict[str, int] | None = None
        self._open_statuses: list[int] | None = None

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

    def ensure_labels(self, names: list[str]) -> list[str]:
        """Tạo trước các category còn thiếu — tương đương labels-init bên GitLab."""
        existing = self._category_ids(refresh=True)
        created = []
        for name in names:
            if name not in existing:
                self._call("POST", f"/projects/{self.project}/categories", [("name", name)])
                created.append(name)
        if created:
            self._category_ids(refresh=True)
        return created

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
        return {"space": self.base, "project": self.info.get("projectKey"),
                "project_id": self.info.get("id"), "user": me.get("userId"),
                "categories": sorted(self._category_ids(refresh=True))}

    # -- ticket ----------------------------------------------------------
    def _to_ticket(self, raw: dict) -> Ticket:
        body = raw.get("description") or ""
        return Ticket(
            id=raw["issueKey"], title=raw.get("summary", ""), body=body,
            labels=[c["name"] for c in raw.get("category") or []],
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
        ids = self._category_ids()
        if label not in ids:
            return []
        params = [("projectId[]", str(self.info["id"])), ("categoryId[]", str(ids[label])),
                  ("count", "50"), ("sort", "created"), ("order", "asc")]
        # Ticket đã Closed nhưng còn mang category `agent:try` không được chạy lại
        # — GitLab lọc `state=opened`, Backlog phải lọc bằng statusId.
        params += [("statusId[]", str(i)) for i in self._open_status_ids()]
        return [self._to_ticket(x) for x in self._call("GET", "/issues", params)]

    def get(self, ticket_id: str) -> Ticket:
        return self._to_ticket(self._call("GET", f"/issues/{ticket_id}"))

    def comment(self, ticket_id: str, body: str) -> None:
        self._call("POST", f"/issues/{ticket_id}/comments", [("content", body)])

    def comments(self, ticket_id: str) -> list[str]:
        raw = self._call("GET", f"/issues/{ticket_id}/comments",
                         [("count", "100"), ("order", "asc")])
        return [c.get("content") or "" for c in raw]

    def set_state(self, ticket_id: str, label: str) -> None:
        """Gỡ hết category trạng thái cũ, đặt cái mới. Category khác giữ nguyên."""
        ids = self._category_ids()
        if label not in ids:
            self.ensure_labels([label])
            ids = self._category_ids()
        current = self.get(ticket_id).labels
        keep = [name for name in current if name not in STATE_LABELS]
        params = [("categoryId[]", str(ids[name])) for name in keep + [label] if name in ids]
        self._call("PATCH", f"/issues/{ticket_id}", params or [("categoryId[]", "")])

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
        return self._to_ticket(self._call("POST", "/issues", params))

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
