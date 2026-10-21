"""Minimized diagnostics and latency spans (Phase 5B final repair).

Default records contain operational metadata and scoped lineage references, not user
messages, source bodies, prompts, conversation history or exception text. Explicit
research-only opt-in uses a separate private metadata directory. Raw body persistence is
disabled: arbitrary body credentials cannot be safely recognized by regex. Metadata
expires after seven days; research metadata after 24 hours. Expiration is enforced
on the next write to that directory; idle deployments must run periodic cleanup.

Redaction is field-aware and value-aware:

- Credential *fields* (`api_key`, `access_token`, `authorization`, `cookie`, ...) are
  replaced with `***`.
- Numeric token-usage counts are preserved. Lexical analysis tokens are content and omitted.
- Credentials embedded in arbitrary strings (URL query parameters, `Authorization` headers,
  bearer tokens, cookies) are scrubbed.
- The diagnostics directory/file are created with `0700`/`0600` permissions and `.runtime/`
  is Git-ignored, so private traces never enter version control.
"""

from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import unquote
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Exact credential field names. "token" alone is a credential only as a whole field name;
# `*_tokens` analysis fields are explicitly preserved (see _TELEMETRY_FIELDS).
_SECRET_FIELDS = {
    "api_key", "apikey", "api-key", "access_key", "secret_key", "private_key", "signing_key",
    "access_token", "refresh_token", "auth_token", "id_token", "token", "bearer",
    "secret", "client_secret", "password", "passwd", "pwd",
    "authorization", "auth", "cookie", "cookies", "set-cookie", "credential", "credentials",
    "session_key", "x-api-key",
}

# Telemetry fields that contain "token"/"key" as a substring but are not secrets.
_TELEMETRY_FIELDS = {
    "subject_tokens", "required_tokens", "matched_tokens", "candidate_tokens", "query_tokens",
    "token_count", "token_usage", "tokens_used", "max_tokens", "prompt_tokens",
    "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
    "measure_keys", "requested_measure_keys", "keys", "columns", "applied_fields",
}

_SECRET_SUFFIXES = ("_api_key", "_apikey", "_secret", "_password", "_credential", "_private_key", "_access_token", "_refresh_token", "_auth_token")

_SECRET_QUERY_RE = re.compile(
    r"(?i)((?:[?&]|\b)(?:api[_-]?key|access[_-]?key|secret[_-]?key|auth|token|access[_-]?token|"
    r"refresh[_-]?token|id[_-]?token|password|passwd|pwd|secret|key)=)([^&#\s]*)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}")
_AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+(?:\s+[^\s,;]+)?)")
_COOKIE_RE = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)([^\n]+)")


@dataclass
class LatencySpan:
    stage: str
    ms: int
    detail: str = ""


class LatencyTracker:
    """Collects per-stage and end-to-end latency (milliseconds)."""

    def __init__(self) -> None:
        self.spans: list[LatencySpan] = []
        self._started = time.monotonic()

    def record(self, stage: str, ms: int, detail: str = "") -> None:
        self.spans.append(LatencySpan(stage=stage, ms=max(0, int(ms)), detail=detail))

    def __enter__(self) -> "LatencyTracker":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.record("goal_end_to_end", self.end_to_end_ms())

    def end_to_end_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)

    def summary(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for span in self.spans:
            result[span.stage] = result.get(span.stage, 0) + span.ms
        result["end_to_end"] = self.end_to_end_ms()
        return result

    def to_list(self) -> list[dict[str, Any]]:
        return [{"stage": s.stage, "ms": s.ms, "detail": s.detail} for s in self.spans]


class DiagnosticRecorder:
    """Bounded, minimized operational record for one Goal."""

    def __init__(self, path: str | Path | None, *, enabled: bool = True, max_text: int = 4000,
                 research_content: bool = False) -> None:
        self.path = Path(path) if path else None
        # Legacy opt-in selects a restricted metadata directory; body capture is disabled.
        self.research_content = research_content
        if self.path and research_content:
            self.path = self.path.parent / "restricted-research" / self.path.name
        self.enabled = enabled and self.path is not None
        self.max_text = max_text
        self.records: list[dict[str, Any]] = []

    def record(self, stage: str, **data: Any) -> None:
        if not self.enabled:
            return
        try:
            sanitized = _sanitize(data, self.max_text, _research=self.research_content)
        except Exception:
            sanitized = {"code": "DIAGNOSTIC_SANITIZATION_FAILURE"}
        safe_stage = stage if re.fullmatch(r"[a-z0-9_:-]{1,80}", stage) else "omitted"
        self.records.append({"stage": safe_stage, "at": _now(),
                             "diagnostic_policy": "minimized-v1",
                             "expires_at_epoch": time.time() + (86400 if self.research_content else 604800),
                             "data": sanitized})

    def write(self) -> str | None:
        if not self.enabled or self.path is None:
            return None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            _restrict_dir(self.path.parent)
            self._expire_owned_records()
            # Reapply policy at the persistence boundary, including caller-mutated records.
            safe_records = []
            for record in self.records:
                safe = _sanitize(record, self.max_text, _research=self.research_content)
                safe["data"] = _sanitize(record.get("data", {}), self.max_text,
                                         _research=self.research_content)
                safe_records.append(safe)
            payload = json.dumps(safe_records, ensure_ascii=False, indent=2)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
            _restrict_file(self.path)
            return str(self.path)
        except Exception:  # noqa: BLE001 - diagnostics must never break a turn
            return None

    def _expire_owned_records(self):
        """Lazy retention only for versioned recorder files; leave older diagnostics alone."""
        for candidate in self.path.parent.glob("*.json"):
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 2_000_000:
                continue
            try:
                records = json.loads(candidate.read_text(encoding="utf-8"))
                if (isinstance(records, list) and records and
                    all(isinstance(r, dict) and r.get("diagnostic_policy") == "minimized-v1" and
                        isinstance(r.get("expires_at_epoch"), (int, float)) and
                        r["expires_at_epoch"] < time.time() for r in records)):
                    candidate.unlink()
            except (OSError, ValueError):
                continue


def diagnostic_path(runtime_dir: Path, session_id: str, goal_id: str) -> Path:
    safe_session = _safe_name(session_id)
    safe_goal = _safe_name(goal_id)
    return Path(runtime_dir) / "diagnostics" / f"{safe_session}__{safe_goal}.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _is_secret_field(key: str) -> bool:
    lowered = str(key)
    for _ in range(6):
        decoded = unquote(lowered)
        if decoded == lowered:
            break
        lowered = decoded
    lowered = lowered.lower().strip()
    if lowered in _TELEMETRY_FIELDS:
        return False
    if lowered in _SECRET_FIELDS:
        return True
    return any(lowered.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def scrub_secret_values(text: str) -> str:
    """Remove credentials embedded in URLs, headers, bearer tokens and cookies."""
    if not text:
        return text
    scrubbed = text
    # Decode nested URL encoding before matching credential names and values.
    for _ in range(6):
        decoded = unquote(scrubbed)
        if decoded == scrubbed:
            break
        scrubbed = decoded
    # One field vocabulary for URL parameters, serialized JSON and headers.
    names = "|".join(re.escape(name) for name in sorted(_SECRET_FIELDS | {"key"}, key=len, reverse=True))
    suffixes = "|".join(re.escape(suffix) for suffix in _SECRET_SUFFIXES)
    assignment = re.compile(
        rf"(?i)(?<![\w-])((?:{names}|[\w-]+(?:{suffixes})))([\"']?\s*[:=]\s*[\"']?)([^\"'&\s,;}}]+)")
    scrubbed = assignment.sub(r"\1\2***", scrubbed)
    scrubbed = _BEARER_RE.sub(r"\1 ***", scrubbed)
    scrubbed = _AUTH_HEADER_RE.sub(r"\1***", scrubbed)
    scrubbed = _COOKIE_RE.sub(r"\1***", scrubbed)
    return scrubbed


def _sanitize(value: Any, max_text: int, *, _depth: int = 0, _field: str = "",
              _research: bool = False, _source_collection: bool = False) -> Any:
    if _field in {"body_text", "text_excerpt", "text"}:
        # Deny the entire raw field, including dictionary/list-shaped caller mutations.
        # This policy is applied again by write(); regex redaction cannot prove safety.
        return "<omitted>"
    if _depth > 6:
        return "<max-depth>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret_field(key):
                result[str(key)] = "***"
            else:
                safe_key = str(key) if re.fullmatch(r"[A-Za-z0-9_:.-]{1,100}", str(key)) else "<omitted-key>"
                result[safe_key] = _sanitize(item, max_text, _depth=_depth + 1, _field=str(key),
                    _research=_research, _source_collection=_source_collection or str(key) in {"documents", "evidence_spans"})
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, max_text, _depth=_depth + 1, _field=_field,
                          _research=_research, _source_collection=_source_collection) for item in value[:200]]
    if isinstance(value, str):
        scrubbed = scrub_secret_values(value)
        # Default-deny free text, even under unexpected keys, lists and errors. Operational
        # references remain usable; content stays in the runtime artifacts, not copied
        # to diagnostics. URLs are omitted because paths/queries may include private data.
        if _field in _SAFE_METADATA and re.fullmatch(r"[A-Za-z0-9_:./-]{1,160}", scrubbed):
            return scrubbed
        if (_field in {"id", "ref_id"} or _field.endswith(("_id", "_ids"))) and re.fullmatch(r"(?:goal|need|art|span|doc|ef|req|prop|claim|export|tool|n|r)[_-]?[A-Za-z0-9_-]{1,70}", scrubbed):
            return scrubbed
        if _field in {"content_hash", "span_hash", "proposition_signature", "hash"} and re.fullmatch(r"[a-fA-F0-9]{32,128}", scrubbed):
            return scrubbed
        return "<omitted>"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if _field in _NUMERIC_METADATA or _field.startswith(("web:", "tool:")) else "<omitted>"
    if hasattr(value, "model_dump"):
        return _sanitize(value.model_dump(), max_text, _depth=_depth + 1, _field=_field,
                         _research=_research, _source_collection=_source_collection)
    return "<omitted>"


_SAFE_METADATA = {
    "diagnostic_policy", "model", "provider", "category", "role", "format", "stage", "kind", "status", "code",
    "retrieval_status", "source_type", "verification", "deterministic_status", "llm_status",
    "coverage", "goal_status", "original_goal_status", "final_goal_status", "method",
    "time_state", "population_state", "attribution", "language", "turn_type", "source",
    "capability", "error_code", "retrieved_at", "at", "population", "game_population",
}

_NUMERIC_METADATA = {
    "expires_at_epoch", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
    "max_tokens", "token_count", "ms", "start", "end", "season", "row_count", "length",
    "judge_llm_calls", "composer_consistency_calls", "web_verification_calls", "web_query_generation_calls",
    "planner_rounds", "conversation", "semantic", "requirements", "planning_and_tools", "judge",
    "composer", "consistency", "goal_end_to_end", "end_to_end", "latency_ms", "fetch_count",
    # M1 research_metrics counters (ADR 0028 observability; no enforcement).
    "provider_calls", "calls", "count", "documents_fetched", "llm_query_calls", "llm_verify_calls",
    "research_input_tokens", "research_output_tokens", "research_total_tokens",
    "goal_provider_calls", "goal_documents_fetched", "goal_llm_query_calls", "goal_llm_verify_calls",
    "goal_search_rounds", "goal_soft_deadline_ms", "goal_hard_deadline_ms",
}


def _restrict_dir(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:  # pragma: no cover - best effort on exotic filesystems
        pass


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass


def _safe_name(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(text))
    return cleaned[:80] or "unknown"
