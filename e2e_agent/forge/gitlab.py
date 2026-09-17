"""Forge GitLab — push nhánh và mở MR. Không biết gì về ticket."""
from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ..core.secrets import read_secret, scrub
from .base import MergeRequest


class GitLabError(RuntimeError):
    pass


class GitLabForge:
    def __init__(self, url: str, project: str, token_env: str = "GITLAB_TOKEN",
                 timeout: int = 30) -> None:
        self.url = url.rstrip("/")
        self.project_path = str(project)
        self.project = urllib.parse.quote(self.project_path, safe="")
        self.timeout = timeout
        self.token = read_secret(token_env)
        if not self.token:
            raise GitLabError(f"thiếu token: đặt biến môi trường {token_env} hoặc dòng "
                              f"{token_env.lower()}=… trong .env")

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
                  body: str, labels: list[str]) -> MergeRequest:
        raw = self._call("POST", "/merge_requests", {
            "source_branch": source_branch, "target_branch": target_branch,
            "title": title, "description": body, "labels": ",".join(labels),
            "remove_source_branch": True})
        return MergeRequest(id=str(raw["iid"]), url=raw["web_url"], source_branch=source_branch)

    def whoami(self) -> dict:
        project = self._call("GET", "")
        access = (project.get("permissions") or {}).get("project_access") or {}
        return {"project": project.get("path_with_namespace"),
                "default_branch": project.get("default_branch"),
                "access_level": access.get("access_level")}
