"""Forge GitLab — push nhánh và mở MR. Không biết gì về ticket."""
from __future__ import annotations

import json
import subprocess
from datetime import date
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..core.secrets import read_secret, scrub
from .base import DRAFT_PREFIX, MergeRequest

#: GitLab access level: Developer trở lên mới push nhánh và mở MR được.
DEVELOPER = 30


class GitLabError(RuntimeError):
    pass


class GitLabForge:
    def __init__(self, url: str, project: str, token_env: str = "GITLAB_TOKEN",
                 timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.project_path = str(project)
        self.project = urllib.parse.quote(self.project_path, safe="")
        self.timeout = timeout
        self.token_env = token_env
        if not self.token:
            raise GitLabError(f"thiếu token: đặt biến môi trường {token_env} hoặc dòng "
                              f"{token_env.lower()}=… trong .env")

    @property
    def token(self) -> str:
        """Đọc lại MỖI lần gọi. Token hết hạn → người sửa .env → chuyển ticket về cho agent:
        đọc một lần lúc khởi động thì lần thử lại vẫn dùng token cũ và hỏng y như trước."""
        return read_secret(self.token_env)

    def _call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{self.url}/api/v4/projects/{self.project}{path}", data=data, method=method,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            hint = (" — push nhánh và tạo MR cần vai trò Developer trở lên."
                    if exc.code == 403 else "")
            raise GitLabError(f"{method} {path} → {exc.code}: {detail}{hint}") from None

    def fetch_url(self) -> str:
        """URL có token để fetch/clone repo private mà không lưu token vào .git/config."""
        host = self.url.split("://", 1)[-1]
        return f"https://oauth2:{self.token}@{host}/{self.project_path}.git"

    def push_branch(self, repo: Path, branch: str) -> None:
        """Push nhánh mới. KHÔNG force.

        Mỗi run dùng một tên nhánh riêng nên không có gì để ghi đè. `--force-with-lease`
        trên URL thô còn hỏng theo hướng khác: không có remote-tracking ref thì lease
        rỗng, git từ chối ngay lần thứ hai với `stale info`.
        """
        host = self.url.split("://", 1)[-1]
        remote = f"https://oauth2:{self.token}@{host}/{self.project_path}.git"
        proc = subprocess.run(["git", "push", remote, f"HEAD:{branch}"],
                              cwd=repo, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            raise GitLabError(f"push nhánh {branch} thất bại: {scrub(proc.stderr, self.token)}")

    def create_mr(self, source_branch: str, target_branch: str, title: str,
                  body: str, labels: list[str], draft: bool = False) -> MergeRequest:
        if draft and not title.startswith(DRAFT_PREFIX):
            title = DRAFT_PREFIX + title
        raw = self._call("POST", "/merge_requests", {
            "source_branch": source_branch, "target_branch": target_branch,
            "title": title, "description": body, "labels": ",".join(labels),
            "remove_source_branch": True})
        return MergeRequest(id=str(raw["iid"]), url=raw["web_url"], source_branch=source_branch)

    def update_mr(self, mr_id: str, title: str, body: str, draft: bool = False) -> MergeRequest:
        """Cập nhật mô tả MR sau khi agent đẩy thêm commit theo góp ý (bằng chứng mới)."""
        if draft and not title.startswith(DRAFT_PREFIX):
            title = DRAFT_PREFIX + title
        raw = self._call("PUT", f"/merge_requests/{mr_id}", {"title": title, "description": body})
        return MergeRequest(id=str(raw["iid"]), url=raw["web_url"],
                            source_branch=raw.get("source_branch", ""))

    def whoami(self) -> dict:
        project = self._call("GET", "")
        perms = project.get("permissions") or {}
        # Quyền kế thừa từ group nằm ở group_access, không phải project_access.
        levels = [(perms.get(k) or {}).get("access_level") or 0
                  for k in ("project_access", "group_access")]
        return {"project": project.get("path_with_namespace"),
                "default_branch": project.get("default_branch"),
                "access_level": max(levels) or None}

    def check(self) -> list[str]:
        """Preflight trước Phase B: token còn dùng được để push và mở MR không.

        Trả về danh sách vấn đề (rỗng = ổn). Chạy TRƯỚC khi tốn tiền cho agent: token hết
        hạn mà để tới bước tạo MR mới biết là mất trắng cả một run.
        """
        try:
            me = self.whoami()
        except (GitLabError, OSError) as exc:        # OSError: mất mạng, DNS, timeout
            return [f"không gọi được GitLab bằng token `{self.token_env}`: {exc}"]
        problems = []
        level = me.get("access_level")
        if level is not None and level < DEVELOPER:
            problems.append(f"token chỉ có quyền mức {level} trên {me.get('project')} — push nhánh "
                            f"và mở MR cần Developer ({DEVELOPER}) trở lên")
        try:
            tok = self._raw("GET", "/personal_access_tokens/self")
        except GitLabError:
            return problems          # GitLab cũ hoặc loại token không hỗ trợ: whoami đã đủ
        if tok.get("revoked") or tok.get("active") is False:
            problems.append(f"token `{tok.get('name', '?')}` đã bị thu hồi hoặc hết hạn")
        elif (exp := tok.get("expires_at")) and exp < date.today().isoformat():
            problems.append(f"token `{tok.get('name', '?')}` hết hạn ngày {exp}")
        return problems

    def _raw(self, method: str, path: str):
        """Gọi API ngoài phạm vi project (vd /personal_access_tokens/self)."""
        req = urllib.request.Request(f"{self.url}/api/v4{path}", method=method,
                                     headers={"PRIVATE-TOKEN": self.token})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            raise GitLabError(f"{method} {path} → {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise GitLabError(f"{method} {path}: {exc}") from None
