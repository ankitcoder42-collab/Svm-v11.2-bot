#!/usr/bin/env python3
"""Safe, local-first AI gateway for the SVM bot.

This module only talks to a configured OpenAI-compatible LocalAI endpoint. It
does not execute model output, shell commands, or infrastructure operations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class AIError(RuntimeError):
    """A safe-to-display AI service error."""


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _env_int(
    env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class AISettings:
    enabled: bool
    agent_enabled: bool
    provider: str
    base_url: str
    api_key: str
    model: str
    timeout: int
    max_output: int
    history_messages: int
    max_context_chars: int
    user_rpm: int
    user_daily_limit: int
    max_concurrent_tasks: int
    memory_enabled: bool
    session_retention_days: int
    usage_retention_days: int
    unsupported_requested: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AISettings":
        values = os.environ if env is None else env
        provider = values.get("AI_PROVIDER", "localai").strip().lower()
        if provider != "localai":
            raise ValueError("AI_PROVIDER currently supports only localai")

        base_url = values.get(
            "LOCALAI_BASE_URL", "http://127.0.0.1:8080"
        ).strip().rstrip("/")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "LOCALAI_BASE_URL must be an http(s) URL without embedded credentials"
            )

        unsupported_flags = {
            "AI_STREAMING": "streaming",
            "AI_TOOLS": "tool calling",
            "AI_FILE_TOOLS": "file tools",
            "AI_CODE_TOOLS": "coding tools",
            "AI_VISION": "vision",
            "AI_IMAGE_ENABLED": "image generation",
        }
        unsupported_requested = tuple(
            feature
            for variable, feature in unsupported_flags.items()
            if _env_bool(values, variable, False)
        )
        if values.get("LOCALAI_IMAGE_MODEL", "").strip():
            unsupported_requested += ("image model",)

        return cls(
            enabled=_env_bool(values, "AI_ENABLED", True),
            agent_enabled=_env_bool(values, "AI_AGENT_ENABLED", True),
            provider=provider,
            base_url=base_url,
            api_key=values.get("LOCALAI_API_KEY", "").strip(),
            model=values.get("LOCALAI_MODEL", "").strip(),
            timeout=_env_int(values, "AI_TIMEOUT", 180, 1, 900),
            max_output=_env_int(values, "AI_MAX_OUTPUT", 4096, 64, 32768),
            history_messages=_env_int(
                values, "AI_MAX_HISTORY_MESSAGES", 12, 0, 100
            ),
            max_context_chars=_env_int(
                values, "AI_MAX_CONTEXT_CHARS", 24000, 2048, 500000
            ),
            user_rpm=_env_int(values, "AI_USER_RPM", 10, 1, 600),
            user_daily_limit=_env_int(
                values, "AI_USER_DAILY_LIMIT", 200, 1, 100000
            ),
            max_concurrent_tasks=_env_int(
                values, "AI_MAX_CONCURRENT_TASKS", 3, 1, 100
            ),
            memory_enabled=_env_bool(values, "AI_MEMORY", True),
            session_retention_days=_env_int(
                values, "AI_SESSION_RETENTION_DAYS", 90, 1, 3650
            ),
            usage_retention_days=_env_int(
                values, "AI_USAGE_RETENTION_DAYS", 365, 1, 3650
            ),
            unsupported_requested=unsupported_requested,
        )


class LocalAIClient:
    """Small OpenAI-compatible LocalAI client using only the Python standard library."""

    def __init__(self, settings: AISettings):
        self.settings = settings

    def _request_json(
        self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.settings.base_url}/v1/{path.lstrip('/')}"
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        request = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.settings.timeout) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise AIError("LocalAI returned a response larger than 2 MB")
        except HTTPError as exc:
            raise AIError(f"LocalAI returned HTTP {exc.code}") from None
        except (TimeoutError, URLError, OSError):
            raise AIError("LocalAI could not be reached or timed out") from None

        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AIError("LocalAI returned an invalid JSON response") from None
        if not isinstance(value, dict):
            raise AIError("LocalAI returned an unexpected response")
        return value

    def list_models(self) -> list[str]:
        response = self._request_json("models")
        data = response.get("data")
        if not isinstance(data, list):
            raise AIError("LocalAI model response did not include a model list")
        model_ids: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id.strip())
        return model_ids

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.settings.model:
            raise AIError("No model is configured. Set LOCALAI_MODEL in .env.")
        response = self._request_json(
            "chat/completions",
            method="POST",
            payload={
                "model": self.settings.model,
                "messages": messages,
                "max_tokens": self.settings.max_output,
                "stream": False,
            },
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIError("LocalAI returned no completion")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            result = "\n".join(part for part in parts if part).strip()
            if result:
                return result
        raise AIError("LocalAI returned an empty completion")

    def status(self) -> dict[str, Any]:
        started = time.monotonic()
        try:
            models = self.list_models()
        except AIError as exc:
            return {
                "connected": False,
                "model_ready": False,
                "models": [],
                "latency_ms": None,
                "error": str(exc),
            }

        selected_ready = bool(self.settings.model and self.settings.model in models)
        return {
            "connected": True,
            "model_ready": selected_ready,
            "models": models,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error": None if selected_ready else (
                "No model is configured."
                if not self.settings.model
                else "Configured model is not in the LocalAI model list."
            ),
        }


class AIAgentManager:
    """Owns AI runtime state, per-user limits, and isolated chat sessions."""

    SYSTEM_PROMPT = (
        "You are the SVM+ assistant. You can answer general questions, explain "
        "code, and help with technical topics. You do not have access to live "
        "VPS, node, payment, IPAM, port, file, or host-shell tools in this chat. "
        "Never claim to have inspected or changed infrastructure. Never request "
        "tokens, passwords, private keys, or payment secrets."
    )

    def __init__(
        self,
        database_path: str | Path,
        disabled_marker: str | Path,
        settings: AISettings | None = None,
    ):
        self.database_path = Path(database_path)
        self.disabled_marker = Path(disabled_marker)
        self._lock = threading.RLock()
        self._started_at = time.monotonic()
        self._last_request_at: datetime | None = None
        self._last_latency_ms: int | None = None
        self._request_times: dict[str, deque[float]] = defaultdict(deque)
        self._storage_error: str | None = None
        self._config_error: str | None = None
        try:
            self.settings = settings or AISettings.from_env()
            self.client = LocalAIClient(self.settings)
            self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_tasks)
        except ValueError as exc:
            self.settings = None
            self.client = None
            self._semaphore = asyncio.Semaphore(1)
            self._config_error = str(exc)
        try:
            self._initialize_storage()
        except (OSError, sqlite3.Error) as exc:
            self._storage_error = f"AI storage is unavailable ({type(exc).__name__})"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize_storage(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            connection = self._connect()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS ai_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        guild_id TEXT NOT NULL,
                        active INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        last_activity TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_ai_sessions_owner
                        ON ai_sessions(guild_id, user_id, active, last_activity);
                    CREATE TABLE IF NOT EXISTS ai_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES ai_sessions(session_id)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS idx_ai_messages_session
                        ON ai_messages(session_id, id);
                    CREATE TABLE IF NOT EXISTS ai_usage (
                        usage_date TEXT NOT NULL,
                        guild_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        request_count INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (usage_date, guild_id, user_id)
                    );
                    """
                )
                if self.settings:
                    now = datetime.now(timezone.utc)
                    session_cutoff = (
                        now - timedelta(days=self.settings.session_retention_days)
                    ).isoformat()
                    usage_cutoff = (
                        now.date() - timedelta(days=self.settings.usage_retention_days)
                    ).isoformat()
                    connection.execute(
                        """
                        DELETE FROM ai_messages
                        WHERE session_id IN (
                            SELECT session_id FROM ai_sessions WHERE last_activity < ?
                        )
                        """,
                        (session_cutoff,),
                    )
                    connection.execute(
                        "DELETE FROM ai_sessions WHERE last_activity < ?",
                        (session_cutoff,),
                    )
                    connection.execute(
                        "DELETE FROM ai_usage WHERE usage_date < ?",
                        (usage_cutoff,),
                    )
                connection.commit()
            finally:
                connection.close()

    @property
    def available(self) -> bool:
        return bool(
            self.settings
            and self.client
            and self.settings.enabled
            and self.settings.agent_enabled
            and not self._storage_error
            and not self.disabled_marker.exists()
        )

    def start(self) -> tuple[bool, str]:
        if self._storage_error:
            return False, self._storage_error
        if not self.settings or not self.settings.enabled or not self.settings.agent_enabled:
            return False, "AI is disabled by configuration. Check AI_ENABLED and AI_AGENT_ENABLED."
        try:
            self.disabled_marker.unlink(missing_ok=True)
        except OSError:
            return False, "Could not resume AI processing; check the bot directory permissions."
        return True, "AI processing is enabled. The SVM bot and infrastructure were not restarted."

    def stop(self) -> tuple[bool, str]:
        try:
            self.disabled_marker.parent.mkdir(parents=True, exist_ok=True)
            self.disabled_marker.write_text("AI processing paused\n", encoding="utf-8")
        except OSError:
            return False, "Could not pause AI processing; check the bot directory permissions."
        return True, "AI processing is paused. The SVM bot and infrastructure remain online."

    def current_session(
        self, guild_id: str, user_id: str, *, create: bool = True
    ) -> str | None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT session_id FROM ai_sessions
                    WHERE guild_id = ? AND user_id = ? AND active = 1
                    ORDER BY last_activity DESC LIMIT 1
                    """,
                    (guild_id, user_id),
                ).fetchone()
                if row:
                    connection.execute(
                        "UPDATE ai_sessions SET last_activity = ? WHERE session_id = ?",
                        (now, row["session_id"]),
                    )
                    connection.commit()
                    return str(row["session_id"])
                if not create:
                    return None
                session_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO ai_sessions
                        (session_id, user_id, guild_id, active, created_at, last_activity)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (session_id, user_id, guild_id, now, now),
                )
                connection.commit()
                return session_id
            finally:
                connection.close()

    def new_session(self, guild_id: str, user_id: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        session_id = uuid.uuid4().hex
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "UPDATE ai_sessions SET active = 0 WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
                connection.execute(
                    """
                    INSERT INTO ai_sessions
                        (session_id, user_id, guild_id, active, created_at, last_activity)
                    VALUES (?, ?, ?, 1, ?, ?)
                    """,
                    (session_id, user_id, guild_id, now, now),
                )
                connection.commit()
            finally:
                connection.close()
        return session_id

    def get_history(
        self, guild_id: str, user_id: str, limit: int | None = None
    ) -> list[dict[str, str]]:
        if not self.settings or not self.settings.memory_enabled:
            return []
        session_id = self.current_session(guild_id, user_id, create=False)
        if not session_id:
            return []
        message_limit = limit or self.settings.history_messages
        if message_limit <= 0:
            return []
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT role, content FROM ai_messages
                    WHERE session_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (session_id, message_limit),
                ).fetchall()
                return [
                    {"role": str(row["role"]), "content": str(row["content"])}
                    for row in reversed(rows)
                ]
            finally:
                connection.close()

    def clear_session(self, guild_id: str, user_id: str) -> int:
        session_id = self.current_session(guild_id, user_id, create=False)
        if not session_id:
            return 0
        with self._lock:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    "DELETE FROM ai_messages WHERE session_id = ?", (session_id,)
                )
                connection.commit()
                return cursor.rowcount
            finally:
                connection.close()

    def session_info(self, guild_id: str, user_id: str) -> dict[str, Any] | None:
        session_id = self.current_session(guild_id, user_id, create=False)
        if not session_id:
            return None
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT created_at, last_activity FROM ai_sessions
                    WHERE session_id = ? AND guild_id = ? AND user_id = ?
                    """,
                    (session_id, guild_id, user_id),
                ).fetchone()
                count = connection.execute(
                    "SELECT COUNT(*) FROM ai_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                if not row:
                    return None
                return {
                    "session_id": session_id,
                    "created_at": str(row["created_at"]),
                    "last_activity": str(row["last_activity"]),
                    "message_count": int(count),
                }
            finally:
                connection.close()

    def _record_message(
        self, session_id: str, user_message: str, assistant_message: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO ai_messages(session_id, role, content, created_at)
                    VALUES (?, 'user', ?, ?), (?, 'assistant', ?, ?)
                    """,
                    (session_id, user_message, now, session_id, assistant_message, now),
                )
                connection.execute(
                    "UPDATE ai_sessions SET last_activity = ? WHERE session_id = ?",
                    (now, session_id),
                )
                if self.settings and self.settings.history_messages > 0:
                    keep = self.settings.history_messages
                    connection.execute(
                        """
                        DELETE FROM ai_messages
                        WHERE session_id = ? AND id NOT IN (
                            SELECT id FROM ai_messages
                            WHERE session_id = ? ORDER BY id DESC LIMIT ?
                        )
                        """,
                        (session_id, session_id, keep),
                    )
                connection.commit()
            finally:
                connection.close()

    def _check_and_record_limit(self, guild_id: str, user_id: str) -> None:
        if not self.settings:
            raise AIError(self._config_error or "AI configuration is invalid.")
        now = time.monotonic()
        user_key = f"{guild_id}:{user_id}"
        with self._lock:
            timestamps = self._request_times[user_key]
            while timestamps and now - timestamps[0] >= 60:
                timestamps.popleft()
            if len(timestamps) >= self.settings.user_rpm:
                raise AIError("AI rate limit reached. Please wait a minute and try again.")

            usage_date = datetime.now(timezone.utc).date().isoformat()
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT request_count FROM ai_usage
                    WHERE usage_date = ? AND guild_id = ? AND user_id = ?
                    """,
                    (usage_date, guild_id, user_id),
                ).fetchone()
                current_count = int(row["request_count"]) if row else 0
                if current_count >= self.settings.user_daily_limit:
                    raise AIError("Your daily AI request limit has been reached.")
                connection.execute(
                    """
                    INSERT INTO ai_usage(usage_date, guild_id, user_id, request_count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(usage_date, guild_id, user_id)
                    DO UPDATE SET request_count = request_count + 1
                    """,
                    (usage_date, guild_id, user_id),
                )
                connection.commit()
                timestamps.append(now)
            finally:
                connection.close()

    async def ask(
        self, guild_id: str, user_id: str, prompt: str
    ) -> tuple[str, int]:
        if self._storage_error:
            raise AIError(self._storage_error)
        if not self.available:
            if self._config_error:
                raise AIError(f"AI configuration error: {self._config_error}")
            if self._storage_error:
                raise AIError(self._storage_error)
            raise AIError("AI processing is paused or disabled.")
        if not self.settings or not self.client:
            raise AIError("AI is not configured.")
        if not prompt.strip():
            raise AIError("Enter a message for the assistant.")
        if len(prompt) > 12000:
            raise AIError("Please keep each message under 12,000 characters.")
        if len(prompt) + len(self.SYSTEM_PROMPT) > self.settings.max_context_chars:
            raise AIError("Your message is larger than the configured AI context limit.")

        self._check_and_record_limit(guild_id, user_id)
        async with self._semaphore:
            history = self.get_history(guild_id, user_id)
            session_id = self.current_session(guild_id, user_id, create=True)
            if not session_id:
                raise AIError("Could not create an AI session.")
            remaining_chars = (
                self.settings.max_context_chars
                - len(self.SYSTEM_PROMPT)
                - len(prompt)
            )
            selected_history = []
            for item in reversed(history):
                if remaining_chars <= 0:
                    break
                content = item["content"]
                if len(content) > remaining_chars:
                    marker = "[Earlier message content omitted]\n"
                    if remaining_chars > len(marker):
                        keep_chars = remaining_chars - len(marker)
                        content = marker + content[-keep_chars:]
                    else:
                        continue
                selected_history.append(
                    {"role": item["role"], "content": content}
                )
                remaining_chars -= len(content)
            messages = [{"role": "system", "content": self.SYSTEM_PROMPT}]
            messages.extend(reversed(selected_history))
            messages.append({"role": "user", "content": prompt})
            started = time.monotonic()
            answer = await asyncio.to_thread(self.client.chat, messages)
            if len(answer) > 16000:
                answer = answer[:16000].rstrip() + "\n\n[Response truncated at 16,000 characters.]"
            elapsed = round((time.monotonic() - started) * 1000)
            if self.settings.memory_enabled:
                await asyncio.to_thread(
                    self._record_message, session_id, prompt, answer
                )
            self._last_request_at = datetime.now(timezone.utc)
            self._last_latency_ms = elapsed
            return answer, elapsed

    def usage_summary(self) -> dict[str, int]:
        if self._storage_error:
            raise AIError(self._storage_error)
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            connection = self._connect()
            try:
                total = connection.execute(
                    "SELECT COALESCE(SUM(request_count), 0) FROM ai_usage"
                ).fetchone()[0]
                today_total = connection.execute(
                    "SELECT COALESCE(SUM(request_count), 0) FROM ai_usage WHERE usage_date = ?",
                    (today,),
                ).fetchone()[0]
                active_users = connection.execute(
                    "SELECT COUNT(DISTINCT user_id) FROM ai_usage WHERE usage_date = ? AND request_count > 0",
                    (today,),
                ).fetchone()[0]
                return {
                    "total_requests": int(total),
                    "today_requests": int(today_total),
                    "today_users": int(active_users),
                }
            finally:
                connection.close()

    def status(self) -> dict[str, Any]:
        if self._config_error:
            return {
                "enabled": False,
                "connected": False,
                "model_ready": False,
                "error": f"Configuration error: {self._config_error}",
                "uptime_seconds": round(time.monotonic() - self._started_at),
            }
        if not self.client or not self.settings:
            return {
                "enabled": False,
                "connected": False,
                "model_ready": False,
                "error": "AI is not configured.",
            }
        service = self.client.status()
        return {
            "enabled": self.available,
            "configured": self.settings.enabled and self.settings.agent_enabled,
            "provider": self.settings.provider,
            "model": self.settings.model or "Not configured",
            "connected": service["connected"],
            "model_ready": service["model_ready"],
            "models": service["models"],
            "latency_ms": service["latency_ms"],
            "last_latency_ms": self._last_latency_ms,
            "last_request_at": self._last_request_at,
            "memory_enabled": self.settings.memory_enabled,
            "unsupported_requested": list(self.settings.unsupported_requested),
            "error": service["error"],
            "uptime_seconds": round(time.monotonic() - self._started_at),
            "storage_error": self._storage_error,
        }


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SVM+ LocalAI status utility")
    parser.add_argument(
        "command",
        choices=("status", "models", "doctor", "start", "stop", "restart"),
        help="inspect LocalAI or control in-process AI request handling",
    )
    parser.add_argument(
        "--env-file",
        default=str(Path(__file__).with_name(".env")),
        help="configuration file to read",
    )
    parser.add_argument(
        "--state-file",
        default=str(Path(__file__).with_name("ai.disabled")),
        help="runtime pause marker used by the SVM bot",
    )
    args = parser.parse_args(argv)
    _load_env_file(Path(args.env_file))

    try:
        settings = AISettings.from_env()
    except ValueError as exc:
        print(f"AI configuration error: {exc}")
        return 2

    state_file = Path(args.state_file)
    if args.command == "stop":
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("AI processing paused\n", encoding="utf-8")
        except OSError:
            print("Could not pause AI processing; check directory permissions.")
            return 1
        print("AI requests paused. The SVM bot and infrastructure remain online.")
        return 0
    if args.command in {"start", "restart"}:
        if not settings.enabled or not settings.agent_enabled:
            print("AI is disabled by AI_ENABLED or AI_AGENT_ENABLED in the environment.")
            return 2
        try:
            state_file.unlink(missing_ok=True)
        except OSError:
            print("Could not resume AI processing; check directory permissions.")
            return 1
        print("AI request handling is enabled. LocalAI is managed separately.")
        return 0

    client = LocalAIClient(settings)
    if args.command == "status":
        status = client.status()
        runtime_enabled = (
            settings.enabled and settings.agent_enabled and not state_file.exists()
        )
        print(f"AI request handling: {'enabled' if runtime_enabled else 'paused/disabled'}")
        print(f"Provider: {settings.provider}")
        print(f"Endpoint: {settings.base_url}")
        print(f"Connected: {'yes' if status['connected'] else 'no'}")
        print(f"Selected model: {settings.model or 'not configured'}")
        print(f"Model ready: {'yes' if status['model_ready'] else 'no'}")
        if settings.unsupported_requested:
            print(
                "Requested but not implemented: "
                + ", ".join(settings.unsupported_requested)
            )
        if status["latency_ms"] is not None:
            print(f"Latency: {status['latency_ms']} ms")
        if status["error"]:
            print(f"Details: {status['error']}")
        return 0 if status["connected"] else 1

    if args.command == "models":
        try:
            models = client.list_models()
        except AIError as exc:
            print(f"LocalAI unavailable: {exc}")
            return 1
        if not models:
            print("LocalAI is reachable, but it reports no models.")
            return 2
        for model in models:
            print(model)
        return 0

    print("Python standard-library AI client: ready")
    status = client.status()
    if not status["connected"]:
        print(f"LocalAI: unavailable ({status['error']})")
        return 1
    if not settings.model:
        print("LocalAI: connected; configure LOCALAI_MODEL before chat.")
        return 2
    if not status["model_ready"]:
        print(f"LocalAI: connected; {status['error']}")
        return 2
    print(f"LocalAI: connected; model {settings.model} is available.")
    return 0


if __name__ == "__main__":
    sys.exit(main())