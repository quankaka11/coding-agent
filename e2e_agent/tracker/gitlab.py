"""Backlog trên GitLab. Chỉ REST v4 + git CLI, không thêm dependency.

Token đọc từ biến môi trường, không bao giờ nằm trong hồ sơ repo và không bao
giờ vào log (core.log đã che pattern token).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from ..core.secrets import read_secret
from .base import STATE_LABELS, Ticket, meta as _meta


class GitLabError(RuntimeError):
    pass


class GitLabTracker:
    def __init__(self, url: str, project: str, token_env: str = "GITLAB_TOKEN",
                 timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.project = urllib.parse.quote(str(project), safe="")
        self.timeout = timeout
        self.token_env = token_env
        if not self.token:
            raise GitLabError(f"thiếu token: đặt biến môi trường {token_env} hoặc dòng "
                              f"{token_env.lower()}=… trong .env")

    @property
    def token(self) -> str:
        """Đọc lại mỗi lần: sửa token trong .env có hiệu lực ngay, không phải khởi động lại."""
        return read_secret(self.token_env)

    # -- REST ------------------------------------------------------------
    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.url}/api/v4{path}", data=data, method=method,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            hint = ""
            if exc.code == 403:
                hint = (" — token thiếu quyền. Push nhánh và tạo MR cần vai trò Developer; "
                        "token vai trò Guest chỉ thao tác được với issue.")
            raise GitLabError(f"{method} {path} → {exc.code}: {detail}{hint}") from None

    def _project_path(self, suffix: str) -> str:
        return f"/projects/{self.project}{suffix}"

    # -- ticket ----------------------------------------------------------
    def _to_ticket(self, raw: dict) -> Ticket:
        body = raw.get("description") or ""
        return Ticket(id=str(raw["iid"]), title=raw.get("title", ""), body=body,
                      labels=list(raw.get("labels") or []), url=raw.get("web_url", ""),
                      parent=_meta(body, "parent"), base_sha=_meta(body, "base_sha"))

    def list_by_label(self, label: str) -> list[Ticket]:
        raw = self._call("GET", self._project_path(
            f"/issues?labels={urllib.parse.quote(label)}&state=opened&per_page=50"))
        return [self._to_ticket(x) for x in raw]

    def get(self, ticket_id: str) -> Ticket:
        return self._to_ticket(self._call("GET", self._project_path(f"/issues/{ticket_id}")))

    def comment(self, ticket_id: str, body: str) -> None:
        self._call("POST", self._project_path(f"/issues/{ticket_id}/notes"), {"body": body})

    def comments(self, ticket_id: str) -> list[str]:
        raw = self._call("GET", self._project_path(
            f"/issues/{ticket_id}/notes?per_page=100&sort=asc&order_by=created_at"))
        return [n.get("body") or "" for n in raw]

    def set_state(self, ticket_id: str, label: str) -> None:
        current = self.get(ticket_id).labels
        keep = [l for l in current if l not in STATE_LABELS]
        self._call("PUT", self._project_path(f"/issues/{ticket_id}"),
                   {"labels": ",".join(keep + [label])})

    def create_ticket(self, title: str, body: str, labels: list[str],
                      parent: str | None = None) -> Ticket:
        if parent:
            body = f"{body}\n\n<!-- parent: {parent} -->"
        raw = self._call("POST", self._project_path("/issues"),
                         {"title": title, "description": body, "labels": ",".join(labels)})
        return self._to_ticket(raw)

    def notify(self, ticket_id: str, message: str) -> None:
        """Thông báo cho người duyệt (yêu cầu kèm theo quyết định A7)."""
        self.comment(ticket_id, message)

    # -- tiện ích --------------------------------------------------------
    def ensure_labels(self, labels: list[str]) -> list[str]:
        """Tạo trước label còn thiếu. Gọi một lần lúc doctor."""
        existing = {x["name"] for x in self._call(
            "GET", self._project_path("/labels?per_page=100"))}
        created = []
        for name in labels:
            if name not in existing:
                self._call("POST", self._project_path("/labels"),
                           {"name": name, "color": "#428BCA"})
                created.append(name)
        return created

    def whoami(self) -> dict:
        project = self._call("GET", self._project_path(""))
        access = (project.get("permissions") or {}).get("project_access") or {}
        return {"project": project.get("path_with_namespace"),
                "default_branch": project.get("default_branch"),
                "access_level": access.get("access_level")}
