#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
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
STATUS_ANALYSIS = os.getenv("SURGE_ANALYSIS_STATUS", "analysis")
STATUS_DEVELOPMENT = os.getenv("SURGE_DEVELOPMENT_STATUS", "development")
STATUS_QA = os.getenv("SURGE_QA_STATUS", "testing")
STATUS_AWAITING_CLIENT = os.getenv("SURGE_AWAITING_CLIENT_STATUS", "awaiting_client")
STATUS_CLOSED = os.getenv("SURGE_CLOSED_STATUS", "closed")
POLL_SECONDS = int(os.getenv("SURGE_QUEUE_POLL_SECONDS", "3600"))
MAX_PARALLEL_CLIENTS = int(os.getenv("SURGE_MAX_PARALLEL_CLIENTS", "4"))
ALLOW_HARD_RESET = os.getenv("SURGE_ALLOW_HARD_RESET", "false").lower() in {"1", "true", "yes"}
ROLE_USER_IDS = {
    "product-owner": os.getenv("SURGE_PRODUCT_OWNER_USER_ID", "a38fa02b-43da-4337-8fa4-24b01975faf0"),
    "analyst": os.getenv("SURGE_ANALYST_USER_ID", "118bc954-af05-436d-8b96-76af47bb65b0"),
    "developer": os.getenv("SURGE_DEVELOPER_USER_ID", "99e7e11b-a5df-4814-b0f5-05e85175f3e8"),
    "qa-tester": os.getenv("SURGE_QA_USER_ID", "e042ca88-189d-4dd7-b53e-11a55099dcb0"),
    "release-manager": os.getenv("SURGE_RELEASE_USER_ID", "1404101a-6eb2-4a74-b5c6-635892f11696"),
}
ROLE_DISPLAY = {
    "product-owner": "Priya Bennett",
    "analyst": "Ethan Cole",
    "developer": "Maya Chen",
    "qa-tester": "Noah Patel",
    "release-manager": "Grace Monroe",
}
ROLE_TITLES = {
    "product-owner": "Product Owner",
    "analyst": "Analyst",
    "developer": "Developer",
    "qa-tester": "QA Tester",
    "release-manager": "Release Manager",
}

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
ACTIVE_QUEUE_STATUSES = (
    STATUS_SUBMITTED,
    STATUS_ANALYSIS,
    STATUS_DEVELOPMENT,
    STATUS_QA,
)
STATUS_SORT_ORDER = {
    STATUS_ANALYSIS: 0,
    STATUS_DEVELOPMENT: 1,
    STATUS_QA: 2,
    STATUS_SUBMITTED: 3,
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


class SupabaseClient:
    def __init__(self, base_url: str, service_role_key: str):
        if not base_url or not service_role_key:
            raise RuntimeError("missing Supabase configuration")
        self.base_url = base_url
        self.service_role_key = service_role_key

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

    def fetch_active_tickets(self) -> List[Ticket]:
        query = {
            "select": "id,ticket_number,client_id,kind,status,priority,title,description,source_app_key,has_media,metadata,created_at,surge_clients(name,slug)",
            "status": f"in.({','.join(ACTIVE_QUEUE_STATUSES)})",
            "order": "created_at.asc",
        }
        rows = self._request("GET", "/rest/v1/surge_devops", query=query) or []
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
        self._request("POST", "/rest/v1/surge_devops_notes", query={"select": "id"}, body=payload)

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


def acquire_client_lock(ticket: Ticket) -> bool:
    lock_path = client_lock_path(ticket.client_slug)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8"))
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
        "analyst": SCHEMAS_DIR / "analyst-result.schema.json",
        "developer": SCHEMAS_DIR / "developer-result.schema.json",
        "qa-tester": SCHEMAS_DIR / "qa-result.schema.json",
        "release-manager": SCHEMAS_DIR / "release-result.schema.json",
    }
    return mapping[role]


def artifact_for_role(role: str, work_root: Path) -> Path:
    mapping = {
        "product-owner": work_root / "01-intake.md",
        "analyst": work_root / "02-analysis.md",
        "developer": work_root / "03-dev-plan.md",
        "qa-tester": work_root / "04-qa-report.md",
        "release-manager": work_root / "05-release.md",
    }
    return mapping[role]


def prompt_for_role(role: str, ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> str:
    ticket_json = (dirs["work_root"] / "ticket.json").read_text(encoding="utf-8")
    attachments_json = (dirs["work_root"] / "attachments.json").read_text(encoding="utf-8")
    media_manifest_json = (dirs["work_root"] / "media-manifest.json").read_text(encoding="utf-8")
    media_summary = (dirs["work_root"] / "media-summary.md").read_text(encoding="utf-8")
    previous = []
    for name in ("01-intake.md", "02-analysis.md", "03-dev-plan.md", "04-qa-report.md", "05-release.md"):
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

Prior artefacts:
{previous_docs}

Role instructions:
{role_docs(role)}
""".strip()

    stage_prompt = {
        "product-owner": """
Decide whether this ticket is real and actionable, and whether the submitted kind is correct.

Valid outcomes:
- `analysis` if the ticket is real and ready for analysis
- `closed` if more information is needed, the ticket is invalid, not real, or not appropriate

Return `kind_assessment` as the normalized ticket kind you think this work should be:
- `bug`
- `issue`
- `change_request`

Return JSON that matches the schema exactly.
""",
        "analyst": """
Produce as-is requirements, to-be requirements, BDD acceptance criteria, impacted code areas, and the test strategy.

Return JSON that matches the schema exactly.
""",
        "developer": """
Implement the change in the current client repo if the analysis is actionable.
Update code and tests as needed inside the client workspace.
Summarize exactly what changed and what QA should verify.

Return JSON that matches the schema exactly.
""",
        "qa-tester": """
Review the implementation against the analysis and acceptance criteria.
Run targeted checks where practical.

Decision rules:
- `pass` if ready for release
- `fail` if the developer must rework
- `awaiting_client` if only client-side validation can resolve the remaining uncertainty
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


def run_codex_role(role: str, ticket: Ticket, dirs: Dict[str, Path], attachments: List[Attachment]) -> Dict:
    prompt = prompt_for_role(role, ticket, dirs, attachments)
    output_path = dirs["responses_dir"] / f"{role}.json"
    cmd = [
        "codex",
        "exec",
        "-C",
        str(dirs["client_root"]),
        "--sandbox",
        CODEX_SANDBOX_MODE,
        "-c",
        "approval_policy=\"never\"",
        "--output-schema",
        str(schema_for_role(role)),
        "--output-last-message",
        str(output_path),
        "-",
    ]
    image_args: List[str] = []
    if role in {"product-owner", "analyst", "qa-tester"}:
        media_manifest = json.loads((dirs["work_root"] / "media-manifest.json").read_text(encoding="utf-8"))
        for attachment in attachments:
            if attachment.media_type == "image":
                image_args.extend(["--image", str(dirs["attachments_dir"] / attachment.file_name)])
        for item in media_manifest:
            for frame in item.get("extracted_frames", [])[:MAX_PROMPT_IMAGES]:
                image_args.extend(["--image", frame])
        image_args = image_args[: MAX_PROMPT_IMAGES * 2]
    cmd[2:2] = image_args
    logging.info(
        "starting role %s for ticket %s in %s with codex sandbox=%s",
        role,
        ticket.ticket_number,
        dirs["client_root"],
        CODEX_SANDBOX_MODE,
    )
    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(dirs["client_root"]),
        timeout=3600,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{role} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    logging.info("completed role %s for ticket %s", role, ticket.ticket_number)
    return json.loads(output_path.read_text(encoding="utf-8"))


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


def sync_client_repo_for_stage(ticket: Ticket, client_root: Path) -> None:
    dirty = repo_has_uncommitted_changes(client_root)
    if dirty and ticket.status in {STATUS_DEVELOPMENT, STATUS_QA}:
        logging.info(
            "skipping git sync for ticket %s in status=%s because the client repo has active uncommitted changes",
            ticket.ticket_number,
            ticket.status,
        )
        return
    sync_client_repo(ticket.client_slug)


def maybe_hard_reset(client_root: Path) -> None:
    if not ALLOW_HARD_RESET:
        return
    proc1 = run_command(["git", "reset", "--hard", "HEAD"], cwd=client_root, timeout=300)
    proc2 = run_command(["git", "clean", "-fd"], cwd=client_root, timeout=300)
    if proc1.returncode != 0 or proc2.returncode != 0:
        raise RuntimeError(f"hard reset failed: {proc1.stderr} {proc2.stderr}")


def choose_release_branch(client_root: Path, ticket: Ticket) -> str:
    logging.info("ticket %s will release on branch dev only", ticket.ticket_number)
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


def write_stage_artifact(role: str, dirs: Dict[str, Path], result: Dict) -> None:
    key_map = {
        "product-owner": "intake_markdown",
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
                set_ticket_status(db, ticket, STATUS_AWAITING_CLIENT, last_stage="product-owner")
                return

            set_ticket_status(db, ticket, STATUS_ANALYSIS, last_stage="product-owner")
            return

        if ticket.status == STATUS_ANALYSIS:
            analyst_result = run_codex_role("analyst", ticket, dirs, attachments)
            write_stage_artifact("analyst", dirs, analyst_result)
            post_stage_note(db, ticket, "analyst", analyst_result, dirs)
            set_ticket_status(db, ticket, STATUS_DEVELOPMENT, last_stage="analyst")
            return

        if ticket.status == STATUS_DEVELOPMENT:
            developer_result = run_codex_role("developer", ticket, dirs, attachments)
            write_stage_artifact("developer", dirs, developer_result)
            post_stage_note(db, ticket, "developer", developer_result, dirs)
            set_ticket_status(db, ticket, STATUS_QA, last_stage="developer")
            return

        if ticket.status == STATUS_QA:
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
                set_ticket_status(db, ticket, STATUS_DEVELOPMENT, last_stage="qa-tester")
                logging.info("ticket %s returned to development after QA failure", ticket.ticket_number)
                return

            release_started = True
            release_result = run_codex_role("release-manager", ticket, dirs, attachments)
        else:
            raise RuntimeError(f"unsupported queue status for automation: {ticket.status}")

        release_branch = choose_release_branch(client_root, ticket)
        ensure_release_branch(client_root, release_branch)
        commit_message = commit_all_changes(client_root, ticket)
        release_committed = commit_message != "no changes to commit"
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
