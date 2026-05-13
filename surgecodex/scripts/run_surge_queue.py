#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from uuid import uuid4


ROOT = Path("/opt/surgecodex").resolve()
CLIENTS_DIR = ROOT / "clients"
RUNTIME_DIR = ROOT / "runtime"
AGENTS_DIR = ROOT / "agents"
SCHEMAS_DIR = ROOT / "schemas"
SCRIPTS_DIR = ROOT / "scripts"

STATUS_SUBMITTED = os.getenv("SURGE_SUBMITTED_STATUS", "submitted")
STATUS_DESIGN = os.getenv("SURGE_DESIGN_STATUS", "design")
STATUS_ANALYSIS = os.getenv("SURGE_ANALYSIS_STATUS", "analysis")
STATUS_DEVELOPMENT = os.getenv("SURGE_DEVELOPMENT_STATUS", "development")
STATUS_QA = os.getenv("SURGE_QA_STATUS", "testing")
STATUS_AWAITING_CLIENT = os.getenv("SURGE_AWAITING_CLIENT_STATUS", "awaiting_client")
STATUS_TRIAGED = os.getenv("SURGE_TRIAGED_STATUS", "triaged")
STATUS_CLOSED = os.getenv("SURGE_CLOSED_STATUS", "closed")
POLL_SECONDS = int(os.getenv("SURGE_QUEUE_POLL_SECONDS", "3600"))
MAX_PARALLEL_CLIENTS = int(os.getenv("SURGE_MAX_PARALLEL_CLIENTS", "4"))
MAX_PARALLEL_TASKS = int(os.getenv("SURGE_MAX_PARALLEL_TASKS", "4"))
MAX_QA_FAILURES = int(os.getenv("SURGE_MAX_QA_FAILURES", "3"))
ALLOW_HARD_RESET = os.getenv("SURGE_ALLOW_HARD_RESET", "false").lower() in {"1", "true", "yes"}
ROLE_USER_IDS = {
    "product-owner": os.getenv("SURGE_PRODUCT_OWNER_USER_ID", "a38fa02b-43da-4337-8fa4-24b01975faf0"),
    "designer": os.getenv("SURGE_DESIGNER_USER_ID", "28b28cb9-5182-4a92-9d4b-a74a2334cef4"),
    "analyst": os.getenv("SURGE_ANALYST_USER_ID", "118bc954-af05-436d-8b96-76af47bb65b0"),
    "developer": os.getenv("SURGE_DEVELOPER_USER_ID", "99e7e11b-a5df-4814-b0f5-05e85175f3e8"),
    "peer-reviewer": os.getenv("SURGE_PEER_REVIEW_USER_ID", "e9f2b39a-2c0d-4d24-bc73-61fe0f1494ae"),
    "qa-tester": os.getenv("SURGE_QA_USER_ID", "e042ca88-189d-4dd7-b53e-11a55099dcb0"),
    "release-manager": os.getenv("SURGE_RELEASE_USER_ID", "1404101a-6eb2-4a74-b5c6-635892f11696"),
}
ROLE_DISPLAY = {
    "product-owner": "Priya Bennett",
    "designer": "Lena Ortiz",
    "analyst": "Ethan Cole",
    "developer": "Maya Chen",
    "peer-reviewer": "Adrian Park",
    "qa-tester": "Noah Patel",
    "release-manager": "Grace Monroe",
}
ROLE_TITLES = {
    "product-owner": "Product Owner",
    "designer": "Designer",
    "analyst": "Analyst",
    "developer": "Developer",
    "peer-reviewer": "Peer Reviewer",
    "qa-tester": "QA Tester",
    "release-manager": "Release Manager",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ACTIVE_QUEUE_STATUSES = (
    STATUS_SUBMITTED,
    STATUS_DESIGN,
    STATUS_ANALYSIS,
    STATUS_DEVELOPMENT,
    STATUS_QA,
)
STATUS_SORT_ORDER = {
    STATUS_DESIGN: 0,
    STATUS_ANALYSIS: 1,
    STATUS_DEVELOPMENT: 2,
    STATUS_QA: 3,
    STATUS_SUBMITTED: 4,
}
NOTE_KIND = "note"
MAX_PROMPT_IMAGES = 24
VIDEO_FRAME_INTERVAL_SECONDS = 1
VIDEO_MAX_FRAME_COUNT = int(os.getenv("SURGE_VIDEO_MAX_FRAME_COUNT", "180"))

LOCK = threading.Lock()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_env_file(ROOT / ".env")
load_env_file(ROOT / ".env.local")

SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_TRANSCRIPTION_MODEL = os.getenv("SURGE_OPENAI_TRANSCRIPTION_MODEL", "whisper-1")
CODEX_SANDBOX_MODE = os.getenv("SURGE_CODEX_SANDBOX", "danger-full-access")


def build_git_env() -> Dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    shared_ssh_dir = Path(os.getenv("SURGE_SHARED_SSH_DIR", str(ROOT / ".ssh")))
    if shared_ssh_dir.is_dir():
        config_file = shared_ssh_dir / "config"
        known_hosts_file = shared_ssh_dir / "known_hosts"
        identity_file = shared_ssh_dir / "id_ed25519"
        ssh_cmd = ["ssh", "-F", "/dev/null"]
        if config_file.exists():
            ssh_cmd.extend(["-F", str(config_file)])
        if known_hosts_file.exists():
            ssh_cmd.extend(["-o", f"UserKnownHostsFile={known_hosts_file}"])
        if identity_file.exists():
            ssh_cmd.extend(["-i", str(identity_file), "-o", "IdentitiesOnly=yes"])
        env["GIT_SSH_COMMAND"] = " ".join(ssh_cmd)
    return env


GIT_ENV = build_git_env()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_root() -> None:
    if ROOT != Path("/opt/surgecodex"):
        raise RuntimeError("runner is hard-coded to /opt/surgecodex")
    if not ROOT.exists():
        raise RuntimeError("/opt/surgecodex does not exist")


def run_command(cmd: Sequence[str], cwd: Optional[Path] = None, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=GIT_ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def safe_slug_path(slug: str) -> Path:
    path = (CLIENTS_DIR / slug).resolve()
    if CLIENTS_DIR.resolve() not in path.parents:
        raise RuntimeError(f"unsafe client path for slug {slug}")
    return path


@dataclass
class Attachment:
    id: str
    ticket_id: str
    bucket_id: str
    object_path: str
    file_name: str
    mime_type: str
    media_type: str
    source_app_key: str
    created_at: str


@dataclass
class Ticket:
    id: str
    ticket_number: int
    client_id: str
    client_slug: str
    client_name: str
    kind: str
    status: str
    priority: str
    title: str
    description: str
    source_app_key: str
    has_media: bool
    metadata: Dict
    created_at: str


@dataclass
class NoteRecord:
    id: str
    ticket_id: str
    author_user_id: Optional[str]
    note_kind: str
    is_internal: bool
    body: str
    metadata: Dict
    created_at: str


@dataclass
class TaskState:
    task_id: str
    title: str
    goal: str
    dependency_ids: List[str]
    target_paths: List[str]
    acceptance_criteria: List[str]
    test_focus: str
    status: str
    branch: str
    worktree_path: str
    commit_sha: str
    result: Dict


@dataclass
class TaskExecutionResult:
    task_id: str
    branch: str
    worktree_path: Path
    commit_sha: str
    developer_result: Dict
    peer_review_result: Dict


class AutomationPausedError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        pause_reason: str,
        resume_status: str,
        metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        super().__init__(message)
        self.pause_reason = pause_reason
        self.resume_status = resume_status
        self.metadata = metadata or {}


class SupabaseClient:
    def __init__(self, base_url: str, service_role_key: str):
        if not base_url or not service_role_key:
            raise RuntimeError("missing Supabase configuration")
        self.base_url = base_url
        self.service_role_key = service_role_key
        self.unsupported_ticket_statuses: set[str] = set()

    def _headers(self) -> Dict[str, str]:
        return {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, *, query: Optional[Dict[str, str]] = None, body: Optional[Dict] = None) -> object:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Supabase request failed {exc.code}: {detail}") from exc
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def _invalid_ticket_status_from_error(self, error: RuntimeError) -> Optional[str]:
        match = re.search(
            r'invalid input value for enum surge_ticket_status: (?:"|\\")([^"\\]+)(?:"|\\")',
            str(error),
        )
        if not match:
            return None
        return match.group(1)

    def _mark_status_unsupported(self, status: str) -> None:
        if status in self.unsupported_ticket_statuses:
            return
        self.unsupported_ticket_statuses.add(status)
        logging.warning(
            "Supabase enum surge_ticket_status does not support %s on this deployment; using compatibility fallback",
            status,
        )

    def _note_author_missing_from_users(self, error: RuntimeError) -> bool:
        message = str(error)
        return (
            'surge_devops_notes_author_user_id_fkey' in message
            and 'author_user_id' in message
            and 'table "users"' in message
        )

    def supports_ticket_status(self, status: str) -> bool:
        if status in self.unsupported_ticket_statuses:
            return False
        query = {"select": "id", "status": f"eq.{status}", "limit": "1"}
        try:
            self._request("GET", "/rest/v1/surge_devops", query=query)
        except RuntimeError as exc:
            invalid_status = self._invalid_ticket_status_from_error(exc)
            if invalid_status == status:
                self._mark_status_unsupported(status)
                return False
            raise
        return True

    def fetch_active_tickets(self) -> List[Ticket]:
        statuses = [status for status in ACTIVE_QUEUE_STATUSES if status not in self.unsupported_ticket_statuses]
        if not statuses:
            logging.warning("no supported active queue statuses remain after compatibility filtering")
            return []
        while True:
            query = {
                "select": "id,ticket_number,client_id,kind,status,priority,title,description,source_app_key,has_media,metadata,created_at,surge_clients(name,slug)",
                "status": f"in.({','.join(statuses)})",
                "order": "created_at.asc",
            }
            try:
                rows = self._request("GET", "/rest/v1/surge_devops", query=query) or []
                break
            except RuntimeError as exc:
                invalid_status = self._invalid_ticket_status_from_error(exc)
                if invalid_status and invalid_status in statuses:
                    self._mark_status_unsupported(invalid_status)
                    statuses = [status for status in statuses if status != invalid_status]
                    if not statuses:
                        logging.warning("active queue query has no remaining supported statuses after compatibility filtering")
                        return []
                    continue
                raise
        tickets: List[Ticket] = []
        for row in rows:
            client = row.get("surge_clients") or {}
            slug = client.get("slug")
            if not slug:
                logging.warning("ticket %s has no client slug", row.get("ticket_number"))
                continue
            tickets.append(
                Ticket(
                    id=row["id"],
                    ticket_number=row["ticket_number"],
                    client_id=row["client_id"],
                    client_slug=slug,
                    client_name=client.get("name", slug),
                    kind=row["kind"],
                    status=row["status"],
                    priority=row["priority"],
                    title=row["title"],
                    description=row["description"],
                    source_app_key=row["source_app_key"],
                    has_media=row["has_media"],
                    metadata=row.get("metadata") or {},
                    created_at=row["created_at"],
                )
            )
        return tickets

    def fetch_triaged_tickets(self) -> List[Ticket]:
        if STATUS_TRIAGED in self.unsupported_ticket_statuses:
            return []
        query = {
            "select": "id,ticket_number,client_id,kind,status,priority,title,description,source_app_key,has_media,metadata,created_at,surge_clients(name,slug)",
            "status": f"eq.{STATUS_TRIAGED}",
            "order": "created_at.asc",
        }
        try:
            rows = self._request("GET", "/rest/v1/surge_devops", query=query) or []
        except RuntimeError as exc:
            invalid_status = self._invalid_ticket_status_from_error(exc)
            if invalid_status == STATUS_TRIAGED:
                self._mark_status_unsupported(STATUS_TRIAGED)
                return []
            raise
        tickets: List[Ticket] = []
        for row in rows:
            client = row.get("surge_clients") or {}
            slug = client.get("slug")
            if not slug:
                logging.warning("triaged ticket %s has no client slug", row.get("ticket_number"))
                continue
            tickets.append(
                Ticket(
                    id=row["id"],
                    ticket_number=row["ticket_number"],
                    client_id=row["client_id"],
                    client_slug=slug,
                    client_name=client.get("name", slug),
                    kind=row["kind"],
                    status=row["status"],
                    priority=row["priority"],
                    title=row["title"],
                    description=row["description"],
                    source_app_key=row["source_app_key"],
                    has_media=row["has_media"],
                    metadata=row.get("metadata") or {},
                    created_at=row["created_at"],
                )
            )
        return tickets

    def fetch_attachments(self, ticket_id: str) -> List[Attachment]:
        query = {
            "select": "id,ticket_id,bucket_id,object_path,file_name,mime_type,media_type,source_app_key,created_at",
            "ticket_id": f"eq.{ticket_id}",
            "order": "created_at.asc",
        }
        rows = self._request("GET", "/rest/v1/surge_devops_attachments", query=query) or []
        return [
            Attachment(
                id=row["id"],
                ticket_id=row["ticket_id"],
                bucket_id=row["bucket_id"],
                object_path=row["object_path"],
                file_name=row["file_name"],
                mime_type=row["mime_type"],
                media_type=row["media_type"],
                source_app_key=row["source_app_key"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_note(
        self,
        ticket: Ticket,
        body: str,
        *,
        author_user_id: Optional[str] = None,
        is_internal: bool = True,
        metadata: Optional[Dict] = None,
    ) -> None:
        payload = {
            "ticket_id": ticket.id,
            "author_user_id": author_user_id,
            "note_kind": NOTE_KIND,
            "is_internal": is_internal,
            "body": body,
            "source_app_key": ticket.source_app_key,
            "metadata": metadata or {},
        }
        try:
            self._request("POST", "/rest/v1/surge_devops_notes", query={"select": "id"}, body=payload)
        except RuntimeError as exc:
            if not author_user_id or not self._note_author_missing_from_users(exc):
                raise
            fallback_metadata = dict(metadata or {})
            fallback_metadata.setdefault("author_user_id_fallback", True)
            fallback_metadata.setdefault("requested_author_user_id", author_user_id)
            payload["author_user_id"] = None
            payload["metadata"] = fallback_metadata
            logging.warning(
                "note author %s is missing from users on this deployment; retrying note for ticket %s without author_user_id",
                author_user_id,
                ticket.ticket_number,
            )
            self._request("POST", "/rest/v1/surge_devops_notes", query={"select": "id"}, body=payload)

    def fetch_ticket_notes(self, ticket_id: str, *, since: Optional[str] = None) -> List[NoteRecord]:
        query = {
            "select": "id,ticket_id,author_user_id,note_kind,is_internal,body,metadata,created_at",
            "ticket_id": f"eq.{ticket_id}",
            "order": "created_at.asc",
        }
        if since:
            query["created_at"] = f"gt.{since}"
        rows = self._request("GET", "/rest/v1/surge_devops_notes", query=query) or []
        return [
            NoteRecord(
                id=row["id"],
                ticket_id=row["ticket_id"],
                author_user_id=row.get("author_user_id"),
                note_kind=row.get("note_kind") or "",
                is_internal=bool(row.get("is_internal")),
                body=row.get("body") or "",
                metadata=row.get("metadata") or {},
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def update_ticket_status(self, ticket: Ticket, status: str, metadata_patch: Optional[Dict] = None) -> None:
        patch_body: Dict[str, object] = {"status": status}
        if metadata_patch:
            merged = dict(ticket.metadata)
            merged.update(metadata_patch)
            patch_body["metadata"] = merged
        self._request("PATCH", "/rest/v1/surge_devops", query={"id": f"eq.{ticket.id}"}, body=patch_body)

    def update_ticket_kind(self, ticket: Ticket, kind: str) -> None:
        self._request("PATCH", "/rest/v1/surge_devops", query={"id": f"eq.{ticket.id}"}, body={"kind": kind})

    def download_attachment(self, attachment: Attachment, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        object_path = "/".join(urllib.parse.quote(part, safe="") for part in attachment.object_path.split("/"))
        url = f"{self.base_url}/storage/v1/object/{attachment.bucket_id}/{object_path}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                destination.write_bytes(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"attachment download failed {exc.code}: {detail}") from exc


def sort_tickets(tickets: Iterable[Ticket]) -> List[Ticket]:
    return sorted(
        tickets,
        key=lambda item: (
            STATUS_SORT_ORDER.get(item.status, 99),
            PRIORITY_ORDER.get(item.priority, 99),
            item.created_at,
            item.ticket_number,
        ),
    )


def client_lock_path(slug: str) -> Path:
    return (RUNTIME_DIR / slug / "state" / "active-ticket.json").resolve()


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_client_lock(ticket: Ticket) -> bool:
    lock_path = client_lock_path(ticket.client_slug)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
        existing_pid = int(existing.get("runner_pid") or 0)
        if existing_pid and not process_is_running(existing_pid):
            logging.warning(
                "reclaiming stale client lock for %s at %s (ticket_id=%s, runner_pid=%s)",
                ticket.client_slug,
                lock_path,
                existing.get("ticket_id"),
                existing_pid,
            )
            lock_path.unlink()
        else:
            logging.info(
                "client lock already exists for %s at %s (ticket_id=%s)",
                ticket.client_slug,
                lock_path,
                existing.get("ticket_id"),
            )
            return existing.get("ticket_id") == ticket.id
    lock_payload = {
        "ticket_id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "client_slug": ticket.client_slug,
        "started_at": now_iso(),
        "runner_pid": os.getpid(),
    }
    lock_path.write_text(json.dumps(lock_payload, indent=2), encoding="utf-8")
    logging.info("acquired client lock for %s at %s", ticket.client_slug, lock_path)
    return True


def release_client_lock(ticket: Ticket) -> None:
    lock_path = client_lock_path(ticket.client_slug)
    if lock_path.exists():
        lock_path.unlink()
        logging.info("released client lock for %s at %s", ticket.client_slug, lock_path)


def ensure_ticket_dirs(ticket: Ticket) -> Dict[str, Path]:
    client_root = safe_slug_path(ticket.client_slug)
    runtime_root = (RUNTIME_DIR / ticket.client_slug).resolve()
    work_root = runtime_root / "tickets" / str(ticket.ticket_number)
    attachments_dir = work_root / "attachments"
    responses_dir = work_root / "responses"
    state_dir = runtime_root / "state"
    for directory in (work_root, attachments_dir, responses_dir, state_dir):
        directory.mkdir(parents=True, exist_ok=True)
    logging.info("prepared work package for ticket %s at %s", ticket.ticket_number, work_root)
    return {
        "client_root": client_root,
        "runtime_root": runtime_root,
        "work_root": work_root,
        "attachments_dir": attachments_dir,
        "responses_dir": responses_dir,
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "task"


def tasks_state_path(work_root: Path) -> Path:
    return work_root / "tasks.json"


def task_artifacts_dir(work_root: Path, task_id: str) -> Path:
    return work_root / "tasks" / task_id


def ticket_branch_name(ticket: Ticket) -> str:
    prefix = {
        "bug": "bugfix",
        "issue": "chore",
        "change_request": "feature",
    }.get(ticket.kind, "feature")
    return f"{prefix}/ticket-{ticket.ticket_number}-integration"


def task_branch_name(ticket: Ticket, task: TaskState) -> str:
    prefix = {
        "bug": "bugfix",
        "issue": "chore",
        "change_request": "feature",
    }.get(ticket.kind, "feature")
    return f"{prefix}/ticket-{ticket.ticket_number}-{slugify(task.task_id)}"


def task_worktree_path(dirs: Dict[str, Path], task: TaskState) -> Path:
    return dirs["work_root"] / "worktrees" / task.task_id


def task_status_is_terminal(status: str) -> bool:
    return status in {"integrated", "skipped"}


def task_dependencies_satisfied(task: TaskState, tasks_by_id: Dict[str, TaskState]) -> bool:
    for dependency_id in task.dependency_ids:
        dependency = tasks_by_id.get(dependency_id)
        if not dependency or dependency.status != "integrated":
            return False
    return True


def save_task_states(work_root: Path, tasks: Sequence[TaskState]) -> None:
    write_json(
        tasks_state_path(work_root),
        [
            {
                "task_id": task.task_id,
                "title": task.title,
                "goal": task.goal,
                "dependency_ids": task.dependency_ids,
                "target_paths": task.target_paths,
                "acceptance_criteria": task.acceptance_criteria,
                "test_focus": task.test_focus,
                "status": task.status,
                "branch": task.branch,
                "worktree_path": task.worktree_path,
                "commit_sha": task.commit_sha,
                "result": task.result,
            }
            for task in tasks
        ],
    )


def load_task_states(work_root: Path) -> List[TaskState]:
    path = tasks_state_path(work_root)
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    tasks: List[TaskState] = []
    for row in rows:
        tasks.append(
            TaskState(
                task_id=row["task_id"],
                title=row["title"],
                goal=row["goal"],
                dependency_ids=list(row.get("dependency_ids") or []),
                target_paths=list(row.get("target_paths") or []),
                acceptance_criteria=list(row.get("acceptance_criteria") or []),
                test_focus=row.get("test_focus") or "",
                status=row.get("status") or "pending",
                branch=row.get("branch") or "",
                worktree_path=row.get("worktree_path") or "",
                commit_sha=row.get("commit_sha") or "",
                result=row.get("result") or {},
            )
        )
    return tasks


def persist_analysis_tasks(dirs: Dict[str, Path], analyst_result: Dict) -> List[TaskState]:
    tasks: List[TaskState] = []
    seen_ids = set()
    for index, row in enumerate(analyst_result["tasks"], start=1):
        task_id = slugify(row["task_id"])
        if task_id in seen_ids:
            task_id = f"{task_id}-{index}"
        seen_ids.add(task_id)
        tasks.append(
            TaskState(
                task_id=task_id,
                title=row["title"],
                goal=row["goal"],
                dependency_ids=[slugify(value) for value in row.get("dependency_ids") or []],
                target_paths=list(row.get("target_paths") or []),
                acceptance_criteria=list(row.get("acceptance_criteria") or []),
                test_focus=row.get("test_focus") or "",
                status="pending",
                branch="",
                worktree_path="",
                commit_sha="",
                result={},
            )
        )
    save_task_states(dirs["work_root"], tasks)
    return tasks


def reopen_tasks_for_qa_rework(work_root: Path) -> List[TaskState]:
    tasks = load_task_states(work_root)
    if not tasks:
        return []
    for task in tasks:
        if task.status == "skipped":
            continue
        task.status = "pending"
        task.branch = ""
        task.worktree_path = ""
        task.commit_sha = ""
        task.result = {}
    save_task_states(work_root, tasks)
    return tasks


def ticket_qa_failure_count(ticket: Ticket) -> int:
    value = ticket.metadata.get("qa_failure_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def render_task_plan_markdown(tasks: Sequence[TaskState]) -> str:
    lines = ["# Task Plan", ""]
    for task in tasks:
        lines.append(f"## {task.task_id}: {task.title}")
        lines.append("")
        lines.append(f"- status: `{task.status}`")
        lines.append(f"- goal: {task.goal}")
        lines.append(f"- target_paths: {', '.join(task.target_paths) if task.target_paths else 'unspecified'}")
        lines.append(
            f"- dependencies: {', '.join(task.dependency_ids) if task.dependency_ids else 'none'}"
        )
        if task.acceptance_criteria:
            lines.append("- acceptance_criteria:")
            for criterion in task.acceptance_criteria:
                lines.append(f"  - {criterion}")
        if task.test_focus:
            lines.append(f"- test_focus: {task.test_focus}")
        if task.branch:
            lines.append(f"- branch: `{task.branch}`")
        if task.commit_sha:
            lines.append(f"- commit_sha: `{task.commit_sha}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def build_multipart_form(fields: Dict[str, str], file_field: str, file_path: Path, content_type: str) -> tuple[bytes, str]:
    boundary = f"----SurgeCodex{uuid4().hex}"
    parts: List[bytes] = []

    for key, value in fields.items():
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")

    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), boundary


def transcribe_audio_with_openai(audio_path: Path, transcript_dir: Path) -> Optional[Path]:
    if not OPENAI_API_KEY:
        logging.warning("OPENAI_API_KEY is missing; audio transcription is unavailable")
        return None

    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{audio_path.stem}.txt"
    mime_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
    payload, boundary = build_multipart_form(
        {
            "model": OPENAI_TRANSCRIPTION_MODEL,
            "response_format": "text",
        },
        "file",
        audio_path,
        mime_type,
    )

    request = urllib.request.Request(
        f"{OPENAI_BASE_URL}/audio/transcriptions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=1800) as response:
            transcript_text = response.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        logging.warning("OpenAI transcription failed for %s: %s %s", audio_path, exc.code, detail)
        return None

    transcript_path.write_text(transcript_text, encoding="utf-8")
    return transcript_path


def role_docs(role: str) -> str:
    role_dir = AGENTS_DIR / role
    parts = []
    for name in ("soul.md", "do.md", "do-not.md"):
        parts.append(f"## {name}\n{(role_dir / name).read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def build_media_summary(ticket: Ticket, attachments: List[Attachment], attachments_dir: Path, media_manifest: List[Dict]) -> str:
    lines = [
        "# Media Summary",
        "",
        f"Ticket: {ticket.ticket_number}",
        "",
    ]
    if not attachments:
        lines.append("No attachments found.")
        return "\n".join(lines) + "\n"
    lines.append("## Attachments")
    lines.append("")
    for attachment, manifest in zip(attachments, media_manifest):
        local_path = attachments_dir / attachment.file_name
        lines.append(f"- `{attachment.file_name}`")
        lines.append(f"  - media_type: `{attachment.media_type}`")
        lines.append(f"  - mime_type: `{attachment.mime_type}`")
        lines.append(f"  - local_path: `{local_path}`")
        if manifest.get("extracted_frames"):
            lines.append(f"  - extracted_frames: `{len(manifest['extracted_frames'])}`")
        if manifest.get("transcript_path"):
            lines.append(f"  - transcript_path: `{manifest['transcript_path']}`")
        if attachment.media_type == "video":
            if manifest.get("processing_status") == "processed":
                lines.append("  - note: video frames were extracted for review.")
            else:
                lines.append(f"  - note: {manifest.get('processing_note', 'video downloaded but not further processed.')}")
        if attachment.media_type == "image":
            lines.append("  - note: image available for direct Codex prompt attachment.")
        if attachment.media_type == "other":
            lines.append(f"  - note: {manifest.get('processing_note', 'attachment downloaded for reference.')}")
    return "\n".join(lines) + "\n"


def write_initial_work_package(ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment], media_manifest: List[Dict]) -> None:
    write_json(dirs["work_root"] / "ticket.json", {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "client_id": ticket.client_id,
        "client_slug": ticket.client_slug,
        "client_name": ticket.client_name,
        "kind": ticket.kind,
        "status": ticket.status,
        "priority": ticket.priority,
        "title": ticket.title,
        "description": ticket.description,
        "source_app_key": ticket.source_app_key,
        "metadata": ticket.metadata,
        "created_at": ticket.created_at,
    })
    write_json(dirs["work_root"] / "attachments.json", [attachment.__dict__ for attachment in attachments])
    write_json(dirs["work_root"] / "media-manifest.json", media_manifest)
    (dirs["work_root"] / "media-summary.md").write_text(
        build_media_summary(ticket, attachments, dirs["attachments_dir"], media_manifest),
        encoding="utf-8",
    )


def process_media_attachments(ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> List[Dict]:
    media_root = dirs["work_root"] / "media"
    frames_root = media_root / "frames"
    audio_root = media_root / "audio"
    transcripts_root = media_root / "transcripts"
    for directory in (media_root, frames_root, audio_root, transcripts_root):
        directory.mkdir(parents=True, exist_ok=True)

    ffmpeg_available = command_exists("ffmpeg")
    ffprobe_available = command_exists("ffprobe")
    manifest: List[Dict] = []

    for attachment in attachments:
        local_path = dirs["attachments_dir"] / attachment.file_name
        item: Dict[str, object] = {
            "attachment_id": attachment.id,
            "file_name": attachment.file_name,
            "media_type": attachment.media_type,
            "mime_type": attachment.mime_type,
            "local_path": str(local_path),
            "processing_status": "downloaded",
            "processing_note": "downloaded",
            "extracted_frames": [],
            "audio_path": None,
            "transcript_path": None,
        }

        if attachment.media_type == "image":
            item["processing_status"] = "ready"
            item["processing_note"] = "image available for direct review"
            manifest.append(item)
            continue

        if attachment.media_type != "video":
            item["processing_status"] = "downloaded"
            item["processing_note"] = "non-image attachment downloaded for reference"
            manifest.append(item)
            continue

        if not ffmpeg_available:
            item["processing_note"] = "ffmpeg is not installed, so frame extraction and audio extraction are unavailable"
            manifest.append(item)
            continue

        stem = local_path.stem
        frame_pattern = frames_root / f"{stem}-%03d.jpg"
        audio_path = audio_root / f"{stem}.wav"
        frame_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(local_path),
            "-vf",
            f"fps=1/{VIDEO_FRAME_INTERVAL_SECONDS},scale=1280:-1",
            "-frames:v",
            str(VIDEO_MAX_FRAME_COUNT),
            str(frame_pattern),
        ]
        frame_proc = run_command(frame_cmd, cwd=dirs["work_root"], timeout=900)
        frame_files = sorted(frames_root.glob(f"{stem}-*.jpg"))
        if frame_proc.returncode == 0 and frame_files:
            item["extracted_frames"] = [str(path) for path in frame_files]
            item["processing_status"] = "processed"
            item["processing_note"] = (
                f"extracted {len(frame_files)} frame(s) at 1 frame per {VIDEO_FRAME_INTERVAL_SECONDS} second(s)"
            )
        else:
            item["processing_note"] = "ffmpeg is installed, but frame extraction failed"

        audio_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(local_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
        ]
        audio_proc = run_command(audio_cmd, cwd=dirs["work_root"], timeout=900)
        if audio_proc.returncode == 0 and audio_path.exists():
            item["audio_path"] = str(audio_path)
            transcript_dir = transcripts_root / stem
            transcript_path = transcribe_audio_with_openai(audio_path, transcript_dir)
            if transcript_path and transcript_path.exists():
                item["transcript_path"] = str(transcript_path)
                if item["processing_status"] != "processed":
                    item["processing_status"] = "processed"
                item["processing_note"] = f"{item['processing_note']}; full transcript created via OpenAI"
            else:
                item["processing_note"] = f"{item['processing_note']}; full audio extracted but transcription failed or OPENAI_API_KEY is missing"
        else:
            item["processing_note"] = f"{item['processing_note']}; audio extraction failed"

        if ffprobe_available:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(local_path),
            ]
            probe_proc = run_command(probe_cmd, cwd=dirs["work_root"], timeout=60)
            if probe_proc.returncode == 0 and probe_proc.stdout.strip():
                item["duration_seconds"] = probe_proc.stdout.strip()

        manifest.append(item)

    return manifest


def schema_for_role(role: str) -> Path:
    mapping = {
        "product-owner": SCHEMAS_DIR / "product-owner-result.schema.json",
        "designer": SCHEMAS_DIR / "designer-result.schema.json",
        "analyst": SCHEMAS_DIR / "analyst-result.schema.json",
        "developer": SCHEMAS_DIR / "developer-result.schema.json",
        "peer-reviewer": SCHEMAS_DIR / "peer-review-result.schema.json",
        "qa-tester": SCHEMAS_DIR / "qa-result.schema.json",
        "release-manager": SCHEMAS_DIR / "release-result.schema.json",
    }
    return mapping[role]


def artifact_for_role(role: str, work_root: Path) -> Path:
    mapping = {
        "product-owner": work_root / "01-intake.md",
        "designer": work_root / "02-design.md",
        "analyst": work_root / "03-analysis.md",
        "developer": work_root / "04-dev-plan.md",
        "qa-tester": work_root / "05-qa-report.md",
        "release-manager": work_root / "06-release.md",
    }
    return mapping[role]


def prompt_for_role(role: str, ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> str:
    ticket_json = (dirs["work_root"] / "ticket.json").read_text(encoding="utf-8")
    attachments_json = (dirs["work_root"] / "attachments.json").read_text(encoding="utf-8")
    media_manifest_json = (dirs["work_root"] / "media-manifest.json").read_text(encoding="utf-8")
    media_summary = (dirs["work_root"] / "media-summary.md").read_text(encoding="utf-8")
    tasks_summary = "No task plan has been created yet."
    tasks_path = tasks_state_path(dirs["work_root"])
    if tasks_path.exists():
        tasks_summary = tasks_path.read_text(encoding="utf-8")
    previous = []
    for name in ("01-intake.md", "02-design.md", "03-analysis.md", "04-dev-plan.md", "05-qa-report.md", "06-release.md"):
        path = dirs["work_root"] / name
        if path.exists():
            previous.append(f"## {name}\n{path.read_text(encoding='utf-8')}")
    previous_docs = "\n\n".join(previous) if previous else "No prior stage artefacts."
    common = f"""
You are running as the `{role}` agent for the SurgeCodex workflow.
Your display name is `{ROLE_DISPLAY[role]}`.

Hard constraints:
- Work only inside the current client workspace.
- Never read or edit files outside the current client root.
- The ticket work package is at `{dirs["work_root"]}`.
- Leave your main handoff artefact in the markdown string required by the schema.
- Keep notes factual and auditable.
- Write notes like a real delivery team member speaking clearly to internal stakeholders.
- Write notes at a high level only: what happened and what happens next.
- Keep `note_body` short and succinct. Prefer 2 to 4 short sentences.
- Do not paste detailed analysis, code walkthroughs, or long test narratives into `note_body`.
- If you need more information from the client, say so clearly.
- Use downloaded images and any extracted video frames as evidence where available.
- If video analysis could not be completed, say exactly what media processing was unavailable.

Ticket data:
```json
{ticket_json}
```

Attachment data:
```json
{attachments_json}
```

Media manifest:
```json
{media_manifest_json}
```

Media summary:
{media_summary}

Task state:
```json
{tasks_summary}
```

Prior artefacts:
{previous_docs}

Role instructions:
{role_docs(role)}
""".strip()

    stage_prompt = {
        "product-owner": """
Classify the request strictly and keep scope under control.

Classification rules:
- `bug`: an existing requirement is supposed to work and currently does not work.
- `issue`: a requirement should already exist in the current product but is missing or incomplete.
- `change_request`: genuinely new functionality or a new capability that was not previously required.

Guardrails:
- Bugs and issues must stay tightly bounded to the exact reported problem.
- If a supposed bug or issue is actually new functionality, reclassify it as `change_request`.
- If the request is not real, not valuable, not viable, too vague, or too large in scope for safe automation, do not let it proceed as normal delivery.

Valid outcomes:
- `analysis` for a real, bounded `bug` or `issue`
- `design` for a viable and valuable `change_request`
- `triaged` for work that needs human review, needs client clarification, is too large, low value, not viable, or is otherwise unsuitable for automatic progression right now
- `closed` if the ticket is invalid or should not proceed

Return `kind_assessment` as one of:
- `bug`
- `issue`
- `change_request`

Return JSON that matches the schema exactly.
""",
        "designer": """
This stage is only for approved change requests.

Read the current codebase and existing flow carefully before proposing the feature design.
Your job is to fit the new feature into the current product coherently, with strong UI and UX fundamentals.

You must:
- preserve the existing product language and interaction patterns where possible
- define practical user flows and states for the feature
- call out validation, empty states, errors, permissions, and edge cases
- translate the feature into concrete design requirements the Analyst can incorporate into the formal requirements set

Return JSON that matches the schema exactly.
""",
        "analyst": """
Produce as-is requirements, to-be requirements, BDD acceptance criteria, impacted code areas, and the test strategy.

Also split the approved change into the smallest practical set of implementation tasks.
Task decomposition rules:
- Each task must have a stable `task_id`.
- Each task must be independently executable when its dependencies are satisfied.
- Use dependencies only where one task truly needs another task's code first.
- Prefer target paths that narrow the expected file scope.
- If the change is small, return one task.
- If the change contains multiple requirements or separable workstreams, return multiple tasks.

Return JSON that matches the schema exactly.
""",
        "developer": """
Implement the change in the current client repo if the analysis is actionable.
Update code and tests as needed inside the client workspace.
Summarize exactly what changed and what QA should verify.

Return JSON that matches the schema exactly.
""",
        "peer-reviewer": """
Review the developer's task implementation as a senior engineer.
You may edit the code directly to improve correctness, maintainability, and test coverage, but you must stay inside the assigned task scope.

Decision rules:
- `approved` if the task is ready to integrate
- `blocked` if the task cannot be safely corrected within scope

Return JSON that matches the schema exactly.
""",
        "qa-tester": """
Review the implementation against the analysis and acceptance criteria.
Run targeted checks where practical.

Decision rules:
- `pass` if ready for release
- `fail` if the developer must rework
- `awaiting_client` is reserved for the final post-release handoff state, not for clarification during QA
- if more client information is required before release, return `awaiting_client` in the payload and the runner will park the ticket in `triaged` until a follow-up note arrives
- `closed` if the ticket should be closed instead

Return JSON that matches the schema exactly.
""",
        "release-manager": """
Apply release policy.
Never push to `main` or `master`.
Prefer `dev` when available; otherwise create a safe descriptive branch.
Record what branch should be used and the release outcome.

Return JSON that matches the schema exactly.
""",
    }[role].strip()

    return f"{common}\n\n{stage_prompt}\n"


def run_codex_json(
    *,
    role: str,
    prompt: str,
    cwd: Path,
    output_path: Path,
    schema_path: Path,
    image_paths: Optional[Sequence[str]] = None,
) -> Dict:
    cmd = [
        "codex",
        "exec",
        "-C",
        str(cwd),
        "--sandbox",
        CODEX_SANDBOX_MODE,
        "-c",
        "approval_policy=\"never\"",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    image_args: List[str] = []
    for image_path in image_paths or []:
        image_args.extend(["--image", image_path])
    cmd[2:2] = image_args
    logging.info(
        "starting role %s in %s with codex sandbox=%s",
        role,
        cwd,
        CODEX_SANDBOX_MODE,
    )
    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(cwd),
        timeout=3600,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{role} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    logging.info("completed role %s", role)
    return json.loads(output_path.read_text(encoding="utf-8"))


def role_prompt_images(dirs: Dict[str, Path], attachments: List[Attachment]) -> List[str]:
    image_paths: List[str] = []
    media_manifest = json.loads((dirs["work_root"] / "media-manifest.json").read_text(encoding="utf-8"))
    for attachment in attachments:
        if attachment.media_type == "image":
            image_paths.append(str(dirs["attachments_dir"] / attachment.file_name))
    for item in media_manifest:
        for frame in item.get("extracted_frames", [])[:MAX_PROMPT_IMAGES]:
            image_paths.append(frame)
    return image_paths[: MAX_PROMPT_IMAGES * 2]


def run_codex_role(role: str, ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> Dict:
    prompt = prompt_for_role(role, ticket, dirs, attachments)
    output_path = dirs["responses_dir"] / f"{role}.json"
    image_paths: List[str] = []
    if role in {"product-owner", "designer", "analyst", "qa-tester"}:
        image_paths = role_prompt_images(dirs, attachments)
    return run_codex_json(
        role=role,
        prompt=prompt,
        cwd=dirs["client_root"],
        output_path=output_path,
        schema_path=schema_for_role(role),
        image_paths=image_paths,
    )


def prompt_for_task_role(
    role: str,
    ticket: Ticket,
    dirs: Dict[str, Path],
    task: TaskState,
    attachments: List[Attachment],
    worktree_path: Path,
    developer_result: Optional[Dict] = None,
) -> str:
    task_dir = task_artifacts_dir(dirs["work_root"], task.task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    ticket_json = (dirs["work_root"] / "ticket.json").read_text(encoding="utf-8")
    analysis_markdown = artifact_for_role("analyst", dirs["work_root"]).read_text(encoding="utf-8")
    qa_report_path = artifact_for_role("qa-tester", dirs["work_root"])
    qa_report_markdown = qa_report_path.read_text(encoding="utf-8") if qa_report_path.exists() else "No QA report yet."
    media_summary = (dirs["work_root"] / "media-summary.md").read_text(encoding="utf-8")
    task_json = json.dumps(
        {
            "task_id": task.task_id,
            "title": task.title,
            "goal": task.goal,
            "dependency_ids": task.dependency_ids,
            "target_paths": task.target_paths,
            "acceptance_criteria": task.acceptance_criteria,
            "test_focus": task.test_focus,
        },
        indent=2,
    )
    prior_dev = json.dumps(developer_result, indent=2) if developer_result else "No developer result yet."
    return f"""
You are running as the `{role}` agent for a single SurgeCodex task.
Your display name is `{ROLE_DISPLAY[role]}`.

Hard constraints:
- Work only inside `{worktree_path}`.
- Treat the assigned task as a hard scope boundary.
- Focus on the listed target paths first.
- Do not widen scope to other tasks.
- Keep note output concise and auditable.

Ticket:
```json
{ticket_json}
```

Analysis:
{analysis_markdown}

Latest QA report:
{qa_report_markdown}

Task:
```json
{task_json}
```

Media summary:
{media_summary}

Developer result:
```json
{prior_dev}
```

Role instructions:
{role_docs(role)}
""".strip() + (
        """

Implement the assigned task only.
Make the code and test changes needed for this task.
Summarize what changed and what the next reviewer or integrator should know.
Return JSON that matches the schema exactly.
"""
        if role == "developer"
        else """

Review and improve the assigned task implementation.
Edit the code directly when needed, but stay within task scope.
Return `approved` only when this task is ready to integrate.
Return JSON that matches the schema exactly.
"""
    )


def sync_client_repo(slug: str) -> None:
    logging.info("syncing client repo for %s using %s", slug, SCRIPTS_DIR / "git_sync_client.sh")
    proc = run_command([str(SCRIPTS_DIR / "git_sync_client.sh"), slug], cwd=ROOT, timeout=900)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "git sync failed"
        if "could not read Username for 'https://github.com'" in message:
            raise RuntimeError(
                "git sync failed: GitHub credentials are not available for non-interactive cron runs; "
                "configure the client repo remote with a deploy key, credential helper, or token-backed auth"
            )
        raise RuntimeError(message)
    if proc.stdout.strip():
        logging.info("git sync output for %s: %s", slug, proc.stdout.strip())
    logging.info("git sync completed for %s", slug)


def repo_has_uncommitted_changes(client_root: Path) -> bool:
    proc = run_command(["git", "status", "--porcelain"], cwd=client_root, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git status failed before sync check")
    return bool(proc.stdout.strip())


def repo_status_summary(client_root: Path) -> str:
    proc = run_command(["git", "status", "--short", "--branch"], cwd=client_root, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git status failed while capturing repo summary")
    return proc.stdout.strip() or "clean"


def sync_client_repo_for_stage(ticket: Ticket, client_root: Path) -> None:
    dirty = repo_has_uncommitted_changes(client_root)
    if dirty:
        status_summary = repo_status_summary(client_root)
        logging.warning(
            "ticket %s cannot continue in status=%s because the client repo has uncommitted changes: %s",
            ticket.ticket_number,
            ticket.status,
            status_summary.replace("\n", " | "),
        )
        raise AutomationPausedError(
            (
                f"Client repo has uncommitted changes while ticket {ticket.ticket_number} is in `{ticket.status}`. "
                "Automation cannot safely continue until the integration checkout is cleaned up."
            ),
            pause_reason="dirty_client_repo",
            resume_status=ticket.status,
            metadata={
                "ticket_status": ticket.status,
                "git_status": status_summary,
            },
        )
    try:
        sync_client_repo(ticket.client_slug)
    except RuntimeError as exc:
        if "repo has uncommitted changes, refusing to sync" not in str(exc):
            raise
        raise AutomationPausedError(
            (
                f"Client repo has uncommitted changes while ticket {ticket.ticket_number} is in `{ticket.status}`. "
                "Automation cannot safely continue until the client checkout is cleaned up."
            ),
            pause_reason="dirty_client_repo",
            resume_status=ticket.status,
            metadata={
                "ticket_status": ticket.status,
                "git_status": repo_status_summary(client_root),
            },
        ) from exc


def maybe_hard_reset(client_root: Path) -> None:
    if not ALLOW_HARD_RESET:
        return
    proc1 = run_command(["git", "reset", "--hard", "HEAD"], cwd=client_root, timeout=300)
    proc2 = run_command(["git", "clean", "-fd"], cwd=client_root, timeout=300)
    if proc1.returncode != 0 or proc2.returncode != 0:
        raise RuntimeError(f"hard reset failed: {proc1.stderr} {proc2.stderr}")


def choose_integration_base_branch(client_root: Path) -> str:
    candidates = ("dev", "develop", "main", "master")
    for branch in candidates:
        if run_command(
            ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
            cwd=client_root,
            timeout=60,
        ).returncode == 0:
            return branch
    current_branch = run_command(["git", "branch", "--show-current"], cwd=client_root, timeout=60)
    value = current_branch.stdout.strip()
    if value:
        return value
    raise RuntimeError("unable to determine a safe base branch for integration")


def ensure_ticket_integration_branch(client_root: Path, ticket: Ticket) -> str:
    branch = ticket_branch_name(ticket)
    exists = run_command(["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=client_root, timeout=60)
    if exists.returncode == 0:
        checkout = run_command(["git", "checkout", branch], cwd=client_root, timeout=180)
        if checkout.returncode != 0:
            raise RuntimeError(checkout.stderr.strip() or f"failed to checkout integration branch {branch}")
        return branch
    base_branch = choose_integration_base_branch(client_root)
    base_ref = f"origin/{base_branch}" if run_command(
        ["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{base_branch}"],
        cwd=client_root,
        timeout=60,
    ).returncode == 0 else base_branch
    checkout = run_command(["git", "checkout", "-b", branch, base_ref], cwd=client_root, timeout=180)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or f"failed to create integration branch {branch}")
    return branch


def remove_task_worktree(client_root: Path, worktree_path: Path, branch: str) -> None:
    if worktree_path.exists():
        proc = run_command(["git", "worktree", "remove", "--force", str(worktree_path)], cwd=client_root, timeout=180)
        if proc.returncode != 0:
            logging.warning("failed to remove worktree %s: %s", worktree_path, proc.stderr.strip() or proc.stdout.strip())
    if branch:
        proc = run_command(["git", "branch", "-D", branch], cwd=client_root, timeout=120)
        if proc.returncode != 0:
            logging.warning("failed to delete task branch %s: %s", branch, proc.stderr.strip() or proc.stdout.strip())


def prepare_task_worktree(client_root: Path, dirs: Dict[str, Path], ticket: Ticket, task: TaskState, integration_branch: str) -> Path:
    branch = task_branch_name(ticket, task)
    worktree_path = task_worktree_path(dirs, task)
    remove_task_worktree(client_root, worktree_path, branch)
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    add = run_command(
        ["git", "worktree", "add", "-B", branch, str(worktree_path), integration_branch],
        cwd=client_root,
        timeout=300,
    )
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or add.stdout.strip() or f"failed to create worktree for {task.task_id}")
    task.branch = branch
    task.worktree_path = str(worktree_path)
    return worktree_path


def commit_with_message(client_root: Path, message: str) -> str:
    status = run_command(["git", "status", "--porcelain"], cwd=client_root, timeout=60)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status failed before commit")
    if not status.stdout.strip():
        return ""
    add = run_command(["git", "add", "-A"], cwd=client_root, timeout=300)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add failed")
    commit = run_command(["git", "commit", "-m", message], cwd=client_root, timeout=300)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    rev = run_command(["git", "rev-parse", "HEAD"], cwd=client_root, timeout=60)
    if rev.returncode != 0:
        raise RuntimeError(rev.stderr.strip() or "git rev-parse failed after commit")
    return rev.stdout.strip()


def integrate_task_commit(client_root: Path, integration_branch: str, task: TaskState) -> None:
    checkout = run_command(["git", "checkout", integration_branch], cwd=client_root, timeout=180)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or f"failed to checkout integration branch {integration_branch}")
    if not task.commit_sha:
        task.status = "integrated"
        return
    cherry_pick = run_command(["git", "cherry-pick", task.commit_sha], cwd=client_root, timeout=300)
    if cherry_pick.returncode != 0:
        abort = run_command(["git", "cherry-pick", "--abort"], cwd=client_root, timeout=120)
        if abort.returncode != 0:
            logging.warning("failed to abort cherry-pick for task %s", task.task_id)
        raise RuntimeError(cherry_pick.stderr.strip() or cherry_pick.stdout.strip() or f"failed to integrate task {task.task_id}")
    task.status = "integrated"


def integrate_reviewed_tasks(client_root: Path, dirs: Dict[str, Path], tasks: Sequence[TaskState], integration_branch: str) -> None:
    pending_reviewed = [task for task in tasks if task.status == "reviewed"]
    for task in pending_reviewed:
        logging.info("retrying integration for reviewed task %s", task.task_id)
        try:
            integrate_task_commit(client_root, integration_branch, task)
        except Exception as exc:  # noqa: BLE001
            task.result = {
                **dict(task.result or {}),
                "integration_error": str(exc),
                "integration_failed_at": now_iso(),
            }
            save_task_states(dirs["work_root"], tasks)
            raise AutomationPausedError(
                (
                    f"Reviewed task `{task.task_id}` could not be integrated into `{integration_branch}` automatically. "
                    f"Manual cleanup is required before automation can resume. Git reported: {exc}"
                ),
                pause_reason="task_integration_failed",
                resume_status=STATUS_DEVELOPMENT,
                metadata={
                    "task_id": task.task_id,
                    "task_branch": task.branch,
                    "task_commit_sha": task.commit_sha,
                    "integration_branch": integration_branch,
                    "git_status": repo_status_summary(client_root),
                },
            ) from exc
        save_task_states(dirs["work_root"], tasks)
        if task.worktree_path:
            remove_task_worktree(client_root, Path(task.worktree_path), task.branch)
            task.worktree_path = ""
            save_task_states(dirs["work_root"], tasks)


def choose_release_branch(client_root: Path, ticket: Ticket) -> str:
    for branch in ("dev", "develop"):
        if run_command(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd=client_root, timeout=60).returncode == 0:
            logging.info("ticket %s will release on branch %s", ticket.ticket_number, branch)
            return branch
    logging.info("ticket %s has no non-production remote branch; defaulting release branch to dev", ticket.ticket_number)
    return "dev"


def ensure_release_branch(client_root: Path, branch: str) -> None:
    if branch in {"main", "master"}:
        raise RuntimeError("release branch cannot be main or master")
    remote_check = run_command(["git", "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd=client_root, timeout=60)
    if remote_check.returncode == 0:
        logging.info("checking out existing release branch %s", branch)
        checkout = run_command(["git", "checkout", branch], cwd=client_root, timeout=180)
        if checkout.returncode != 0:
            raise RuntimeError(checkout.stderr.strip() or "failed to checkout release branch")
        pull = run_command(["git", "pull", "--ff-only", "origin", branch], cwd=client_root, timeout=300)
        if pull.returncode != 0:
            raise RuntimeError(pull.stderr.strip() or "failed to pull release branch")
        logging.info("updated existing release branch %s from origin", branch)
        return

    base = None
    if run_command(["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"], cwd=client_root, timeout=60).returncode == 0:
        base = "origin/main"
    elif run_command(["git", "show-ref", "--verify", "--quiet", "refs/remotes/origin/master"], cwd=client_root, timeout=60).returncode == 0:
        base = "origin/master"
    if not base:
        raise RuntimeError("cannot create dev because neither origin/main nor origin/master exists")

    logging.info("creating new release branch %s from %s", branch, base)
    checkout_new = run_command(["git", "checkout", "-b", branch, base], cwd=client_root, timeout=180)
    if checkout_new.returncode != 0:
        raise RuntimeError(checkout_new.stderr.strip() or "failed to create release branch")
    push_new = run_command(["git", "push", "-u", "origin", branch], cwd=client_root, timeout=300)
    if push_new.returncode != 0:
        raise RuntimeError(push_new.stderr.strip() or "failed to publish new dev branch")


def merge_integration_into_release_branch(client_root: Path, integration_branch: str, release_branch: str, ticket: Ticket) -> str:
    ensure_release_branch(client_root, release_branch)
    checkout = run_command(["git", "checkout", release_branch], cwd=client_root, timeout=180)
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or f"failed to checkout release branch {release_branch}")
    pull = run_command(["git", "pull", "--ff-only", "origin", release_branch], cwd=client_root, timeout=300)
    if pull.returncode != 0:
        raise RuntimeError(pull.stderr.strip() or f"failed to update release branch {release_branch}")
    merge = run_command(
        [
            "git",
            "merge",
            "--no-ff",
            "-m",
            f"{ticket.kind}: integrate ticket {ticket.ticket_number} - {ticket.title}",
            integration_branch,
        ],
        cwd=client_root,
        timeout=300,
    )
    if merge.returncode != 0:
        abort = run_command(["git", "merge", "--abort"], cwd=client_root, timeout=120)
        if abort.returncode != 0:
            logging.warning("failed to abort release merge for ticket %s", ticket.ticket_number)
        raise RuntimeError(merge.stderr.strip() or merge.stdout.strip() or "git merge failed")
    rev = run_command(["git", "rev-parse", "HEAD"], cwd=client_root, timeout=60)
    if rev.returncode != 0:
        raise RuntimeError(rev.stderr.strip() or "failed to capture release merge commit")
    return rev.stdout.strip()


def maybe_push_release_branch(client_root: Path, branch: str) -> str:
    logging.info("pushing branch %s to origin", branch)
    push = run_command(["git", "push", "-u", "origin", branch], cwd=client_root, timeout=600)
    if push.returncode != 0:
        raise RuntimeError(push.stderr.strip() or push.stdout.strip() or "git push failed")
    return push.stdout.strip() or "push complete"


def rollback_release_attempt(client_root: Path, *, committed: bool) -> None:
    target = "HEAD~1" if committed else "HEAD"
    reset = run_command(["git", "reset", "--hard", target], cwd=client_root, timeout=300)
    clean = run_command(["git", "clean", "-fd"], cwd=client_root, timeout=300)
    if reset.returncode != 0 or clean.returncode != 0:
        raise RuntimeError(
            f"release rollback failed: {reset.stderr.strip() or reset.stdout.strip()} {clean.stderr.strip() or clean.stdout.strip()}"
        )


def commit_all_changes(client_root: Path, ticket: Ticket) -> str:
    current_name = run_command(["git", "config", "--get", "user.name"], cwd=client_root, timeout=60)
    current_email = run_command(["git", "config", "--get", "user.email"], cwd=client_root, timeout=60)
    if not current_name.stdout.strip():
        run_command(["git", "config", "user.name", ROLE_DISPLAY["release-manager"]], cwd=client_root, timeout=60)
    if not current_email.stdout.strip():
        run_command(["git", "config", "user.email", "release-manager@surgecodex.local"], cwd=client_root, timeout=60)
    status = run_command(["git", "status", "--porcelain"], cwd=client_root, timeout=60)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status failed before commit")
    if not status.stdout.strip():
        logging.info("ticket %s produced no git changes to commit", ticket.ticket_number)
        return "no changes to commit"
    add = run_command(["git", "add", "-A"], cwd=client_root, timeout=300)
    if add.returncode != 0:
        raise RuntimeError(add.stderr.strip() or "git add failed")
    message = f"{ticket.kind}: ticket {ticket.ticket_number} - {ticket.title}"
    logging.info("committing changes for ticket %s with message: %s", ticket.ticket_number, message)
    commit = run_command(["git", "commit", "-m", message], cwd=client_root, timeout=300)
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or commit.stdout.strip() or "git commit failed")
    return message


def update_task_status(dirs: Dict[str, Path], tasks: Sequence[TaskState], task_id: str, **updates: object) -> None:
    for task in tasks:
        if task.task_id == task_id:
            for key, value in updates.items():
                setattr(task, key, value)
            break
    save_task_states(dirs["work_root"], tasks)


def write_task_role_artifact(dirs: Dict[str, Path], task_id: str, name: str, content: str) -> None:
    target = task_artifacts_dir(dirs["work_root"], task_id) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def run_task_worker(
    ticket: Ticket,
    dirs: Dict[str, Path],
    attachments: List[Attachment],
    integration_branch: str,
    task: TaskState,
) -> TaskExecutionResult:
    client_root = dirs["client_root"]
    worktree_path = prepare_task_worktree(client_root, dirs, ticket, task, integration_branch)
    image_paths = role_prompt_images(dirs, attachments)
    developer_output = task_artifacts_dir(dirs["work_root"], task.task_id) / "developer.json"
    developer_result = run_codex_json(
        role="developer",
        prompt=prompt_for_task_role("developer", ticket, dirs, task, attachments, worktree_path),
        cwd=worktree_path,
        output_path=developer_output,
        schema_path=schema_for_role("developer"),
        image_paths=image_paths,
    )
    write_task_role_artifact(dirs, task.task_id, "developer.md", developer_result["dev_plan_markdown"])

    peer_output = task_artifacts_dir(dirs["work_root"], task.task_id) / "peer-reviewer.json"
    peer_review_result = run_codex_json(
        role="peer-reviewer",
        prompt=prompt_for_task_role(
            "peer-reviewer",
            ticket,
            dirs,
            task,
            attachments,
            worktree_path,
            developer_result=developer_result,
        ),
        cwd=worktree_path,
        output_path=peer_output,
        schema_path=schema_for_role("peer-reviewer"),
        image_paths=image_paths,
    )
    write_task_role_artifact(dirs, task.task_id, "peer-review.md", peer_review_result["review_markdown"])
    if peer_review_result["review_decision"] != "approved":
        raise RuntimeError(f"peer reviewer blocked task {task.task_id}: {peer_review_result['summary']}")

    commit_sha = commit_with_message(
        worktree_path,
        f"{ticket.kind}: ticket {ticket.ticket_number} task {task.task_id} - {task.title}",
    )
    return TaskExecutionResult(
        task_id=task.task_id,
        branch=task.branch,
        worktree_path=worktree_path,
        commit_sha=commit_sha,
        developer_result=developer_result,
        peer_review_result=peer_review_result,
    )


def build_development_result(ticket: Ticket, dirs: Dict[str, Path], tasks: Sequence[TaskState]) -> Dict:
    plan_markdown = render_task_plan_markdown(tasks)
    completed = [task for task in tasks if task.status == "integrated"]
    summary = f"Completed {len(completed)} task(s) for ticket {ticket.ticket_number}; ready for QA."
    note_body = "Parallel task execution completed. Each task was developed in an isolated worktree, peer reviewed, and integrated back into the ticket branch."
    return {
        "summary": summary,
        "note_body": note_body,
        "dev_plan_markdown": plan_markdown,
        "next_stage": "qa-tester",
        "files_touched": sorted({path for task in tasks for path in task.target_paths}),
        "test_commands": sorted(
            {
                command
                for task in tasks
                for command in ((task.result.get("developer") or {}).get("test_commands") or []) + ((task.result.get("peer-reviewer") or {}).get("test_commands") or [])
            }
        ),
    }


def execute_task_graph(db: SupabaseClient, ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> Dict:
    tasks = load_task_states(dirs["work_root"])
    if not tasks:
        raise RuntimeError("analysis produced no persisted tasks")
    integration_branch = ensure_ticket_integration_branch(dirs["client_root"], ticket)
    save_task_states(dirs["work_root"], tasks)

    while True:
        integrate_reviewed_tasks(dirs["client_root"], dirs, tasks, integration_branch)
        tasks_by_id = {task.task_id: task for task in tasks}
        if all(task_status_is_terminal(task.status) for task in tasks):
            break
        ready = [
            task for task in tasks
            if task.status in {"pending", "failed"} and task_dependencies_satisfied(task, tasks_by_id)
        ]
        if not ready:
            blocked = [task.task_id for task in tasks if not task_status_is_terminal(task.status)]
            raise RuntimeError(f"task graph deadlock or blocked dependencies: {', '.join(blocked)}")

        for task in ready:
            task.status = "running"
        save_task_states(dirs["work_root"], tasks)

        results: List[TaskExecutionResult] = []
        workers = min(MAX_PARALLEL_TASKS, len(ready))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(run_task_worker, ticket, dirs, attachments, integration_branch, task): task
                for task in ready
            }
            for future in as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    update_task_status(
                        dirs,
                        tasks,
                        task.task_id,
                        status="reviewed",
                        branch=result.branch,
                        worktree_path=str(result.worktree_path),
                        commit_sha=result.commit_sha,
                        result={
                            "developer": result.developer_result,
                            "peer-reviewer": result.peer_review_result,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    update_task_status(dirs, tasks, task.task_id, status="failed", result={"error": str(exc)})
                    raise

        for result in sorted(results, key=lambda item: item.task_id):
            task = next(task for task in tasks if task.task_id == result.task_id)
            try:
                integrate_task_commit(dirs["client_root"], integration_branch, task)
            except Exception as exc:  # noqa: BLE001
                task.result = {
                    **dict(task.result or {}),
                    "integration_error": str(exc),
                    "integration_failed_at": now_iso(),
                }
                save_task_states(dirs["work_root"], tasks)
                raise AutomationPausedError(
                    (
                        f"Reviewed task `{task.task_id}` could not be integrated into `{integration_branch}` automatically. "
                        f"Manual cleanup is required before automation can resume. Git reported: {exc}"
                    ),
                    pause_reason="task_integration_failed",
                    resume_status=STATUS_DEVELOPMENT,
                    metadata={
                        "task_id": task.task_id,
                        "task_branch": task.branch,
                        "task_commit_sha": task.commit_sha,
                        "integration_branch": integration_branch,
                        "git_status": repo_status_summary(dirs["client_root"]),
                    },
                ) from exc
            save_task_states(dirs["work_root"], tasks)
            remove_task_worktree(dirs["client_root"], result.worktree_path, result.branch)
            task.worktree_path = ""
            save_task_states(dirs["work_root"], tasks)

    return build_development_result(ticket, dirs, tasks)


def write_stage_artifact(role: str, dirs: Dict[str, Path], result: Dict) -> None:
    key_map = {
        "product-owner": "intake_markdown",
        "designer": "design_markdown",
        "analyst": "analysis_markdown",
        "developer": "dev_plan_markdown",
        "qa-tester": "qa_report_markdown",
        "release-manager": "release_markdown",
    }
    artifact_for_role(role, dirs["work_root"]).write_text(result[key_map[role]], encoding="utf-8")


def post_stage_note(db: SupabaseClient, ticket: Ticket, role: str, result: Dict, dirs: Dict[str, Path]) -> None:
    author_user_id = ROLE_USER_IDS.get(role) or None
    body = (
        f"{ROLE_DISPLAY[role]} ({ROLE_TITLES[role]}):\n\n"
        f"{result['note_body']}\n\n"
        f"Next: {result['summary']}"
    )
    db.add_note(
        ticket,
        body,
        author_user_id=author_user_id,
        metadata={
            "stage": role,
            "summary": result["summary"],
            "role_display_name": ROLE_DISPLAY[role],
        },
    )


def set_ticket_status(db: SupabaseClient, ticket: Ticket, status: str, *, last_stage: str, extra_metadata: Optional[Dict[str, object]] = None) -> None:
    metadata_patch: Dict[str, object] = {"last_stage": last_stage, "updated_at": now_iso()}
    if extra_metadata:
        metadata_patch.update(extra_metadata)
    db.update_ticket_status(ticket, status, metadata_patch)
    ticket.status = status
    ticket.metadata = {**ticket.metadata, **metadata_patch}
    logging.info("ticket %s moved to %s", ticket.ticket_number, status)


AUTOMATION_NOTE_STAGES = {"system", *ROLE_USER_IDS.keys()}
AUTOMATION_AUTHOR_USER_IDS = {user_id for user_id in ROLE_USER_IDS.values() if user_id}


def park_ticket_for_clarification(
    db: SupabaseClient,
    ticket: Ticket,
    *,
    last_stage: str,
    resume_status: str,
    extra_metadata: Optional[Dict[str, object]] = None,
) -> None:
    metadata_patch = {
        "triage_waiting_for_response": True,
        "triage_requested_at": now_iso(),
        "triage_resume_status": resume_status,
        "triage_resume_stage": last_stage,
    }
    if extra_metadata:
        metadata_patch.update(extra_metadata)
    if db.supports_ticket_status(STATUS_TRIAGED):
        set_ticket_status(
            db,
            ticket,
            STATUS_TRIAGED,
            last_stage=last_stage,
            extra_metadata=metadata_patch,
        )
        return

    db.add_note(
        ticket,
        "Automation fallback: this deployment does not support the `triaged` ticket status yet, so the ticket was parked in `awaiting_client` instead.",
        author_user_id=ROLE_USER_IDS.get(last_stage) or None,
        metadata={"stage": last_stage, "requested_status": STATUS_TRIAGED, "fallback_status": STATUS_AWAITING_CLIENT},
    )
    set_ticket_status(
        db,
        ticket,
        STATUS_AWAITING_CLIENT,
        last_stage=last_stage,
        extra_metadata={
            **metadata_patch,
            "requested_status": STATUS_TRIAGED,
            "status_fallback": STATUS_AWAITING_CLIENT,
        },
    )


def note_is_human_follow_up(note: NoteRecord) -> bool:
    if (note.metadata.get("stage") or "") in AUTOMATION_NOTE_STAGES:
        return False
    if note.author_user_id and note.author_user_id in AUTOMATION_AUTHOR_USER_IDS:
        return False
    if note.body.startswith("[system]"):
        return False
    return True


def resume_triaged_tickets(db: SupabaseClient) -> int:
    resumed = 0
    for ticket in db.fetch_triaged_tickets():
        if not ticket.metadata.get("triage_waiting_for_response"):
            continue
        requested_at = ticket.metadata.get("triage_requested_at")
        if not isinstance(requested_at, str) or not requested_at:
            continue
        notes = db.fetch_ticket_notes(ticket.id, since=requested_at)
        if not any(note_is_human_follow_up(note) for note in notes):
            continue
        resume_status = ticket.metadata.get("triage_resume_status") or STATUS_SUBMITTED
        resume_stage = ticket.metadata.get("triage_resume_stage") or "system"
        resume_metadata: Dict[str, object] = {
            "triage_waiting_for_response": False,
            "triage_resumed_at": now_iso(),
            "triage_resumed_from_stage": resume_stage,
        }
        if ticket.metadata.get("triage_reason") == "qa_retry_limit":
            resume_metadata.update(
                {
                    "qa_failure_count": 0,
                    "qa_retry_limit_reached": False,
                }
            )
        set_ticket_status(
            db,
            ticket,
            resume_status,
            last_stage="system",
            extra_metadata=resume_metadata,
        )
        db.add_note(
            ticket,
            f"[system] Client follow-up detected after triage. Returning the ticket to `{resume_status}` for automation.",
            metadata={"stage": "system", "triage_resume_status": resume_status, "triage_resume_stage": resume_stage},
        )
        resumed += 1
    return resumed


def set_ticket_kind(db: SupabaseClient, ticket: Ticket, kind: str) -> None:
    if ticket.kind == kind:
        return
    db.update_ticket_kind(ticket, kind)
    ticket.kind = kind
    logging.info("ticket %s kind updated to %s", ticket.ticket_number, kind)


def process_ticket(db: SupabaseClient, ticket: Ticket) -> None:
    logging.info(
        "processing ticket %s for client %s (%s) in %s with priority=%s status=%s",
        ticket.ticket_number,
        ticket.client_slug,
        ticket.client_name,
        safe_slug_path(ticket.client_slug),
        ticket.priority,
        ticket.status,
    )
    if not acquire_client_lock(ticket):
        logging.info("client %s is already locked", ticket.client_slug)
        return
    dirs = ensure_ticket_dirs(ticket)
    client_root = dirs["client_root"]
    release_started = False
    release_committed = False
    try:
        attachments = db.fetch_attachments(ticket.id)
        logging.info(
            "ticket %s has %s attachment(s)",
            ticket.ticket_number,
            len(attachments),
        )
        for attachment in attachments:
            destination = dirs["attachments_dir"] / attachment.file_name
            try:
                db.download_attachment(attachment, destination)
                logging.info(
                    "downloaded attachment for ticket %s: %s -> %s",
                    ticket.ticket_number,
                    attachment.object_path,
                    destination,
                )
            except Exception as exc:  # noqa: BLE001
                logging.warning("attachment download failed for ticket %s: %s", ticket.ticket_number, exc)
        media_manifest = process_media_attachments(ticket, dirs, attachments)
        write_initial_work_package(ticket, dirs, attachments, media_manifest)

        sync_client_repo_for_stage(ticket, client_root)

        if ticket.status == STATUS_SUBMITTED:
            po_result = run_codex_role("product-owner", ticket, dirs, attachments)
            write_stage_artifact("product-owner", dirs, po_result)
            post_stage_note(db, ticket, "product-owner", po_result, dirs)
            logging.info(
                "product owner decision for ticket %s: %s",
                ticket.ticket_number,
                po_result["decision"],
            )
            set_ticket_kind(db, ticket, po_result["kind_assessment"])

            if po_result["decision"] == "closed":
                set_ticket_status(db, ticket, STATUS_CLOSED, last_stage="product-owner")
                return
            if po_result["decision"] == "awaiting_client":
                park_ticket_for_clarification(
                    db,
                    ticket,
                    last_stage="product-owner",
                    resume_status=STATUS_SUBMITTED,
                )
                return
            if po_result["decision"] == "triaged":
                park_ticket_for_clarification(
                    db,
                    ticket,
                    last_stage="product-owner",
                    resume_status=STATUS_SUBMITTED,
                )
                return
            if po_result["decision"] == "design":
                if db.supports_ticket_status(STATUS_DESIGN):
                    set_ticket_status(db, ticket, STATUS_DESIGN, last_stage="product-owner")
                    return
                logging.warning(
                    "ticket %s requested design stage, but this deployment does not support status %s; running designer inline",
                    ticket.ticket_number,
                    STATUS_DESIGN,
                )
                designer_result = run_codex_role("designer", ticket, dirs, attachments)
                write_stage_artifact("designer", dirs, designer_result)
                post_stage_note(db, ticket, "designer", designer_result, dirs)
                set_ticket_status(
                    db,
                    ticket,
                    STATUS_ANALYSIS,
                    last_stage="designer",
                    extra_metadata={"requested_status": STATUS_DESIGN, "status_fallback": STATUS_ANALYSIS},
                )
                return

            set_ticket_status(db, ticket, STATUS_ANALYSIS, last_stage="product-owner")
            return

        if ticket.status == STATUS_DESIGN:
            designer_result = run_codex_role("designer", ticket, dirs, attachments)
            write_stage_artifact("designer", dirs, designer_result)
            post_stage_note(db, ticket, "designer", designer_result, dirs)
            set_ticket_status(db, ticket, STATUS_ANALYSIS, last_stage="designer")
            return

        if ticket.status == STATUS_ANALYSIS:
            analyst_result = run_codex_role("analyst", ticket, dirs, attachments)
            write_stage_artifact("analyst", dirs, analyst_result)
            persist_analysis_tasks(dirs, analyst_result)
            post_stage_note(db, ticket, "analyst", analyst_result, dirs)
            set_ticket_status(db, ticket, STATUS_DEVELOPMENT, last_stage="analyst")
            return

        if ticket.status == STATUS_DEVELOPMENT:
            if ticket.metadata.get("last_stage") == "qa-tester":
                reopened_tasks = reopen_tasks_for_qa_rework(dirs["work_root"])
                if reopened_tasks:
                    logging.info(
                        "reopened %s task(s) for ticket %s after QA failure",
                        len(reopened_tasks),
                        ticket.ticket_number,
                    )
            developer_result = execute_task_graph(db, ticket, dirs, attachments)
            write_stage_artifact("developer", dirs, developer_result)
            post_stage_note(db, ticket, "developer", developer_result, dirs)
            set_ticket_status(db, ticket, STATUS_QA, last_stage="developer")
            return

        if ticket.status == STATUS_QA:
            ensure_ticket_integration_branch(dirs["client_root"], ticket)
            qa_result = run_codex_role("qa-tester", ticket, dirs, attachments)
            write_stage_artifact("qa-tester", dirs, qa_result)
            post_stage_note(db, ticket, "qa-tester", qa_result, dirs)
            logging.info(
                "qa decision for ticket %s: %s",
                ticket.ticket_number,
                qa_result["decision"],
            )

            if qa_result["decision"] == "closed":
                set_ticket_status(db, ticket, STATUS_CLOSED, last_stage="qa-tester")
                return
            if qa_result["decision"] == "fail":
                qa_failure_count = ticket_qa_failure_count(ticket) + 1
                qa_failure_metadata = {
                    "qa_failure_count": qa_failure_count,
                    "qa_retry_limit": MAX_QA_FAILURES,
                    "qa_retry_limit_reached": qa_failure_count >= MAX_QA_FAILURES,
                    "last_qa_failure_at": now_iso(),
                    "last_qa_failure_summary": qa_result["summary"],
                }
                if qa_failure_count >= MAX_QA_FAILURES:
                    db.add_note(
                        ticket,
                        (
                            f"[system] QA failed {qa_failure_count} consecutive time(s), which reached the automatic "
                            f"retry limit of {MAX_QA_FAILURES}. Parking the ticket in `triaged` for human review "
                            "instead of sending it back to development again."
                        ),
                        metadata={
                            "stage": "system",
                            "qa_failure_count": qa_failure_count,
                            "qa_retry_limit": MAX_QA_FAILURES,
                            "triage_reason": "qa_retry_limit",
                        },
                    )
                    park_ticket_for_clarification(
                        db,
                        ticket,
                        last_stage="qa-tester",
                        resume_status=STATUS_DEVELOPMENT,
                        extra_metadata={**qa_failure_metadata, "triage_reason": "qa_retry_limit"},
                    )
                    logging.info(
                        "ticket %s moved to triaged after %s QA failure(s)",
                        ticket.ticket_number,
                        qa_failure_count,
                    )
                    return
                set_ticket_status(
                    db,
                    ticket,
                    STATUS_DEVELOPMENT,
                    last_stage="qa-tester",
                    extra_metadata=qa_failure_metadata,
                )
                logging.info(
                    "ticket %s returned to development after QA failure %s/%s",
                    ticket.ticket_number,
                    qa_failure_count,
                    MAX_QA_FAILURES,
                )
                return
            if qa_result["decision"] == "awaiting_client":
                park_ticket_for_clarification(
                    db,
                    ticket,
                    last_stage="qa-tester",
                    resume_status=STATUS_QA,
                )
                return

            release_started = True
            release_result = run_codex_role("release-manager", ticket, dirs, attachments)
        else:
            raise RuntimeError(f"unsupported queue status for automation: {ticket.status}")

        release_branch = choose_release_branch(client_root, ticket)
        integration_branch = ticket_branch_name(ticket)
        commit_message = merge_integration_into_release_branch(client_root, integration_branch, release_branch, ticket)
        release_committed = True
        push_output = maybe_push_release_branch(client_root, release_branch)
        logging.info(
            "release complete for ticket %s on branch %s (%s)",
            ticket.ticket_number,
            release_branch,
            push_output,
        )
        release_payload = dict(release_result)
        release_payload["release_markdown"] = (
            f"{release_result['release_markdown']}\n\n## Branch Outcome\n\n- target_branch: `{release_branch}`\n- commit_message: `{commit_message}`\n- push_output: `{push_output}`\n"
        )
        write_stage_artifact("release-manager", dirs, release_payload)
        release_note = dict(release_result)
        release_note["note_body"] = (
            f"{release_result['note_body']}\n\n"
            f"Commit: `{commit_message}`\n"
            f"Branch: `{release_branch}`\n"
            f"Push: {push_output}"
        )
        release_note["summary"] = f"Committed and pushed to `{release_branch}` with `{commit_message}`."
        post_stage_note(db, ticket, "release-manager", release_note, dirs)
        set_ticket_status(
            db,
            ticket,
            STATUS_AWAITING_CLIENT,
            last_stage="release-manager",
            extra_metadata={"release_branch": release_branch},
        )
    except AutomationPausedError as exc:
        logging.warning("ticket %s automation paused: %s", ticket.ticket_number, exc)
        pause_metadata = {"stage": "system", "automation_pause_reason": exc.pause_reason, **exc.metadata}
        try:
            db.add_note(
                ticket,
                f"[system] Ticket automation paused: {exc}",
                metadata=pause_metadata,
            )
        except Exception:  # noqa: BLE001
            logging.exception("failed to add pause note for ticket %s", ticket.ticket_number)
        park_ticket_for_clarification(
            db,
            ticket,
            last_stage="system",
            resume_status=exc.resume_status,
            extra_metadata=pause_metadata,
        )
        return
    except Exception as exc:  # noqa: BLE001
        logging.exception("ticket %s failed", ticket.ticket_number)
        if release_started:
            try:
                rollback_release_attempt(client_root, committed=release_committed)
                set_ticket_status(db, ticket, STATUS_SUBMITTED, last_stage="system")
            except Exception:  # noqa: BLE001
                logging.exception("release rollback failed for client %s", ticket.client_slug)
        try:
            db.add_note(
                ticket,
                f"[system] Ticket automation failed: {exc}",
                metadata={"stage": "system", "error": str(exc)},
            )
        except Exception:  # noqa: BLE001
            logging.exception("failed to add error note for ticket %s", ticket.ticket_number)
        try:
            maybe_hard_reset(client_root)
        except Exception:  # noqa: BLE001
            logging.exception("hard reset failed for client %s", ticket.client_slug)
        raise
    finally:
        release_client_lock(ticket)


def choose_batch(tickets: Iterable[Ticket]) -> List[Ticket]:
    selected: List[Ticket] = []
    seen_clients = set()
    for ticket in sort_tickets(tickets):
        if ticket.client_slug in seen_clients:
            continue
        if client_lock_path(ticket.client_slug).exists():
            continue
        seen_clients.add(ticket.client_slug)
        selected.append(ticket)
    return selected


def process_once(db: SupabaseClient) -> int:
    failures = 0
    cycle = 0
    processed_any = False
    attempted_ticket_ids = set()

    while True:
        cycle += 1
        resumed_triaged = resume_triaged_tickets(db)
        if resumed_triaged:
            logging.info("cycle %s: resumed %s triaged ticket(s) after follow-up notes", cycle, resumed_triaged)
        tickets = db.fetch_active_tickets()
        queued_count = len(tickets)
        tickets = [ticket for ticket in tickets if ticket.id not in attempted_ticket_ids]
        logging.info(
            "cycle %s: found %s active ticket(s) in the queue (%s remaining after this run's attempts)",
            cycle,
            queued_count,
            len(tickets),
        )
        batch = choose_batch(tickets)
        if not batch:
            if not processed_any:
                logging.info("no tickets ready")
            else:
                logging.info("queue drained for this run")
            return failures

        processed_any = True
        attempted_ticket_ids.update(ticket.id for ticket in batch)
        logging.info(
            "cycle %s: selected %s ticket(s) for this run: %s",
            cycle,
            len(batch),
            ", ".join(f"{ticket.ticket_number}:{ticket.client_slug}:{ticket.priority}" for ticket in batch),
        )

        workers = min(MAX_PARALLEL_CLIENTS, len(batch))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_ticket, db, ticket): ticket for ticket in batch}
            for future in as_completed(futures):
                ticket = futures[future]
                try:
                    future.result()
                except Exception:  # noqa: BLE001
                    failures += 1
                    logging.exception("ticket %s processing failed", ticket.ticket_number)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SurgeCodex Supabase ticket queue")
    parser.add_argument("--once", action="store_true", help="process one queue batch and exit")
    parser.add_argument("--poll-seconds", type=int, default=POLL_SECONDS, help="poll interval in seconds")
    parser.add_argument("--log-level", default="INFO", help="logging level")
    return parser.parse_args()


def main() -> int:
    ensure_root()
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")
    logging.info("using Supabase URL %s", SUPABASE_URL)
    logging.info("client root is %s", CLIENTS_DIR)
    logging.info("runtime root is %s", RUNTIME_DIR)
    db = SupabaseClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    if args.once:
        return 1 if process_once(db) else 0
    while True:
        process_once(db)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
