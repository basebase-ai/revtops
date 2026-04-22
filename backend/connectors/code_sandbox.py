"""
Code Sandbox connector – sandboxed shell execution via E2B.

Wraps E2B sandbox management so organizations can toggle code execution.
The sandbox persists across calls within a conversation.
"""

import asyncio
import logging
import re
from typing import Any

from config import settings
from connectors.base import BaseConnector
from connectors.registry import (
    AuthType,
    Capability,
    ConnectorAction,
    ConnectorMeta,
    ConnectorScope,
)

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT_SECONDS: int = 1800
_COMMAND_TIMEOUT_SECONDS: float = 120
_MAX_OUTPUT_LENGTH: int = 50_000

_PACKAGE_INSTALL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|[;&|()\s])npm\b", re.IGNORECASE), "npm"),
    (re.compile(r"(^|[;&|()\s])yarn\s+(?:global\s+add|add|install)\b", re.IGNORECASE), "yarn add/install"),
    (re.compile(r"(^|[;&|()\s])pnpm\s+(?:add|install)\b", re.IGNORECASE), "pnpm add/install"),
    (re.compile(r"(^|[;&|()\s])bun\s+(?:add|install)\b", re.IGNORECASE), "bun add/install"),
    (re.compile(r"(^|[;&|()\s])pip(?:3)?\b", re.IGNORECASE), "pip"),
    (re.compile(r"(^|[;&|()\s])python(?:3)?\s+-m\s+pip\s+install\b", re.IGNORECASE), "python -m pip install"),
    (re.compile(r"(^|[;&|()\s])uv\s+(?:pip\s+install|add)\b", re.IGNORECASE), "uv pip install/add"),
    (re.compile(r"(^|[;&|()\s])poetry\s+(?:add|install)\b", re.IGNORECASE), "poetry add/install"),
    (re.compile(r"(^|[;&|()\s])pipx\s+install\b", re.IGNORECASE), "pipx install"),
    (re.compile(r"(^|[;&|()\s])apt-get\b", re.IGNORECASE), "apt-get"),
    (re.compile(r"(^|[;&|()\s])yum\b", re.IGNORECASE), "yum"),
    (re.compile(r"(^|[;&|()\s])brew\b", re.IGNORECASE), "brew"),
    (re.compile(r"(^|[;&|()\s])apt\s+install\b", re.IGNORECASE), "apt install"),
    (re.compile(r"(^|[;&|()\s])apk\s+add\b", re.IGNORECASE), "apk add"),
    (re.compile(r"(^|[;&|()\s])dnf\s+install\b", re.IGNORECASE), "dnf install"),
    (re.compile(r"(^|[;&|()\s])pacman\s+-S\b", re.IGNORECASE), "pacman -S"),
)

_PACKAGE_INSTALL_BLOCK_MESSAGE: str = (
    "Installing packages inside the code sandbox is disabled. "
    "Use the preinstalled runtimes and libraries only."
)

_SUDO_BLOCK_PATTERN: re.Pattern[str] = re.compile(r"(^|[;&|()\s])sudo\b", re.IGNORECASE)
_SUDO_BLOCK_MESSAGE: str = (
    "Using sudo inside the code sandbox is disabled. "
    "Run commands without elevated privileges."
)

_MAX_EGRESS_BYTES: int = 1_000_000
_BASEBASE_USER_ID_ENV_KEY: str = "BASEBASE_USER_ID"


def _compile_command_invocation_pattern(command: str) -> re.Pattern[str]:
    """Match command invocations including absolute-path forms (e.g. /usr/bin/curl)."""
    return re.compile(
        rf"(^|[;&|()\s\"'`])(?:[^\s;&|()\"'`]+/)?{re.escape(command)}(?=$|[;&|()\s\"'`])",
        re.IGNORECASE,
    )


_NETWORK_EGRESS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_compile_command_invocation_pattern("curl"), "curl"),
    (_compile_command_invocation_pattern("wget"), "wget"),
    (_compile_command_invocation_pattern("ftp"), "ftp"),
    (_compile_command_invocation_pattern("sftp"), "sftp"),
    (_compile_command_invocation_pattern("scp"), "scp"),
    (_compile_command_invocation_pattern("rsync"), "rsync"),
    (_compile_command_invocation_pattern("nc"), "nc"),
    (_compile_command_invocation_pattern("ncat"), "ncat"),
    (_compile_command_invocation_pattern("netcat"), "netcat"),
    (_compile_command_invocation_pattern("socat"), "socat"),
    (_compile_command_invocation_pattern("ssh"), "ssh"),
    (re.compile(r"/dev/tcp/", re.IGNORECASE), "/dev/tcp"),
)
_NETWORK_EGRESS_BLOCK_MESSAGE: str = (
    "Outbound network transfer commands are disabled in the code sandbox to enforce "
    f"a strict <= {_MAX_EGRESS_BYTES:,} bytes external egress policy. "
    "FTP/tunneling and similar exfiltration channels are blocked."
)


def get_blocked_package_install_reason(command: str) -> str | None:
    """Return a user-facing reason when a sandbox command violates command policy."""
    normalized_command: str = command.strip()
    if not normalized_command:
        return None

    if _SUDO_BLOCK_PATTERN.search(normalized_command):
        logger.info("[Sandbox] Blocked sudo command attempt")
        return f"{_SUDO_BLOCK_MESSAGE} Blocked command pattern: sudo."

    for pattern, label in _PACKAGE_INSTALL_PATTERNS:
        if pattern.search(normalized_command):
            logger.info("[Sandbox] Blocked package installation attempt via %s", label)
            return f"{_PACKAGE_INSTALL_BLOCK_MESSAGE} Blocked command pattern: {label}."

    for pattern, label in _NETWORK_EGRESS_PATTERNS:
        if pattern.search(normalized_command):
            logger.info("[Sandbox] Blocked outbound network command attempt via %s", label)
            return f"{_NETWORK_EGRESS_BLOCK_MESSAGE} Blocked command pattern: {label}."

    return None

_SANDBOX_DB_HELPER_TEMPLATE: str = """
import os
import psycopg2

_DB_HOST: str = os.environ["DB_HOST"]
_DB_PORT: str = os.environ["DB_PORT"]
_DB_NAME: str = os.environ["DB_NAME"]
_DB_USER: str = os.environ["DB_USER"]
_DB_PASSWORD: str = os.environ["DB_PASSWORD"]
_DB_SSLMODE: str = os.environ.get("DB_SSLMODE", "prefer")
_ORG_ID: str = os.environ["ORG_ID"]
_USER_ID: str = os.environ["BASEBASE_USER_ID"]

def get_connection() -> psycopg2.extensions.connection:
    conn: psycopg2.extensions.connection = psycopg2.connect(
        host=_DB_HOST,
        port=_DB_PORT,
        dbname=_DB_NAME,
        user=_DB_USER,
        password=_DB_PASSWORD,
        sslmode=_DB_SSLMODE,
    )
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET ROLE revtops_app")
        cur.execute("SET app.current_org_id = %s", (_ORG_ID,))
        cur.execute("SET app.current_user_id = %s", (_USER_ID,))
        cur.execute("SET default_transaction_read_only = on")
    return conn
""".strip()


class CodeSandboxConnector(BaseConnector):
    """Sandboxed shell execution via E2B, togglable per organization."""

    source_system: str = "code_sandbox"
    meta = ConnectorMeta(
        name="Code Sandbox",
        slug="code_sandbox",
        auth_type=AuthType.CUSTOM,
        scope=ConnectorScope.ORGANIZATION,
        capabilities=[Capability.ACTION],
        actions=[
            ConnectorAction(
                name="execute_command",
                description=(
                    "Run a shell command in a persistent Linux sandbox (Debian, Python3, Node, preinstalled libraries). "
                    "Files in /home/user/output/ are returned as artifacts. "
                    "A read-only DB helper is available via `from db import get_connection`."
                ),
                parameters=[
                    {"name": "command", "type": "string", "required": True, "description": "Shell command to execute"},
                ],
            ),
        ],
        description="Sandboxed code execution via E2B (Python, Node, bash)",
    )

    # Stub abstract methods – no CRM entities
    async def sync_deals(self) -> int:
        return 0

    async def sync_accounts(self) -> int:
        return 0

    async def sync_contacts(self) -> int:
        return 0

    async def sync_activities(self) -> int:
        return 0

    async def fetch_deal(self, deal_id: str) -> dict[str, Any]:
        return {}

    # -----------------------------------------------------------------
    # ACTION – execute_command
    # -----------------------------------------------------------------

    async def execute_action(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        if action != "execute_command":
            raise ValueError(f"Unknown action: {action}")
        return await self._execute_command(params)

    async def _execute_command(self, params: dict[str, Any]) -> dict[str, Any]:
        command: str = (params.get("command") or "").strip()
        if not command:
            return {"error": "No command provided."}

        blocked_reason: str | None = get_blocked_package_install_reason(command)
        if blocked_reason:
            return {"error": blocked_reason}

        conversation_id: str | None = params.get("conversation_id")
        if not conversation_id:
            return {"error": "execute_command requires a conversation context."}

        basebase_user_id: str = str(params.get("basebase_user_id") or "").strip()
        if not basebase_user_id:
            return {
                "error": (
                    "Code sandbox execution requires an authenticated Basebase user. "
                    "Unable to resolve current user context."
                )
            }

        if not settings.E2B_API_KEY:
            return {"error": "E2B_API_KEY is not configured. Cannot run sandboxed commands."}

        sandbox_id: str | None = await _get_sandbox_id_from_db(conversation_id, self.organization_id)
        if sandbox_id is not None:
            alive: bool = await asyncio.to_thread(_is_sandbox_alive_sync, sandbox_id)
            if not alive:
                logger.info("[Sandbox] Sandbox %s expired or dead, creating new one", sandbox_id)
                sandbox_id = None

        if sandbox_id is None:
            try:
                sandbox_id = await asyncio.to_thread(
                    _create_sandbox_sync,
                    self.organization_id,
                    conversation_id,
                    basebase_user_id,
                )
                await _save_sandbox_id_to_db(conversation_id, self.organization_id, sandbox_id)
            except Exception as exc:
                logger.error("[Sandbox] Failed to create sandbox: %s", exc)
                return {"error": f"Failed to create sandbox: {exc}"}

        try:
            result: dict[str, Any] = await asyncio.to_thread(_run_command_sync, sandbox_id, command)
        except Exception as exc:
            error_str: str = str(exc)
            if "not found" in error_str.lower() or "not running" in error_str.lower():
                await _save_sandbox_id_to_db(conversation_id, self.organization_id, None)
            logger.error("[Sandbox] Command execution failed: %s", exc)
            return {"error": f"Command execution failed: {error_str}"}

        stdout: str = result["stdout"]
        stderr: str = result["stderr"]
        exit_code: int = result["exit_code"]

        combined_len: int = len(stdout) + len(stderr)
        if combined_len > _MAX_OUTPUT_LENGTH:
            half: int = _MAX_OUTPUT_LENGTH // 2
            if len(stdout) > half:
                stdout = stdout[:half] + f"\n\n... [truncated, {len(result['stdout'])} chars total]"
            if len(stderr) > half:
                stderr = stderr[:half] + f"\n\n... [truncated, {len(result['stderr'])} chars total]"

        tool_result: dict[str, Any] = {"exit_code": exit_code, "stdout": stdout, "stderr": stderr}

        try:
            output_files: list[dict[str, Any]] = await asyncio.to_thread(_list_output_files_sync, sandbox_id)
            if output_files:
                artifact_names: list[str] = [f["filename"] for f in output_files]
                tool_result["output_files"] = artifact_names
                tool_result["output_files_note"] = f"Files available in /home/user/output/: {', '.join(artifact_names)}"
        except Exception as exc:
            logger.warning("[Sandbox] Failed to list output files: %s", exc)

        return tool_result


# ---- Sandbox ID persistence (DB-backed) ------------------------------------

async def _get_sandbox_id_from_db(conversation_id: str, organization_id: str) -> str | None:
    from sqlalchemy import text as sa_text
    from models.database import get_session

    async with get_session(organization_id=organization_id) as session:
        row = await session.execute(
            sa_text("SELECT sandbox_id FROM conversations WHERE id = CAST(:cid AS uuid)").bindparams(cid=conversation_id)
        )
        return row.scalar_one_or_none()


async def _save_sandbox_id_to_db(conversation_id: str, organization_id: str, sandbox_id: str | None) -> None:
    from sqlalchemy import text as sa_text
    from models.database import get_session

    async with get_session(organization_id=organization_id) as session:
        await session.execute(
            sa_text("UPDATE conversations SET sandbox_id = :sid WHERE id = CAST(:cid AS uuid)").bindparams(
                sid=sandbox_id, cid=conversation_id
            )
        )
        await session.commit()


# ---- Synchronous E2B helpers (run via asyncio.to_thread) --------------------

def _create_sandbox_sync(organization_id: str, conversation_id: str, basebase_user_id: str) -> str:
    from e2b import Sandbox

    sandbox_db_env: dict[str, str] = settings.sandbox_database_connection_env
    sandbox: Sandbox = Sandbox.create(
        timeout=_SANDBOX_TIMEOUT_SECONDS,
        envs={
            **sandbox_db_env,
            "ORG_ID": organization_id,
            _BASEBASE_USER_ID_ENV_KEY: basebase_user_id,
        },
        metadata={
            "conversation_id": conversation_id,
            "organization_id": organization_id,
            "basebase_user_id": basebase_user_id,
        },
        api_key=settings.E2B_API_KEY,
    )
    sandbox.files.write("/home/user/db.py", _SANDBOX_DB_HELPER_TEMPLATE)
    sandbox.files.make_dir("/home/user/output")
    logger.info("[Sandbox] Created sandbox %s for conversation %s", sandbox.sandbox_id, conversation_id[:8])
    return sandbox.sandbox_id


def _is_sandbox_alive_sync(sandbox_id: str) -> bool:
    from e2b import Sandbox

    try:
        sbx: Sandbox = Sandbox.connect(sandbox_id, api_key=settings.E2B_API_KEY)
        return sbx.is_running()
    except Exception:
        return False


def _run_command_sync(sandbox_id: str, command: str) -> dict[str, Any]:
    from e2b import Sandbox

    sandbox: Sandbox = Sandbox.connect(sandbox_id, api_key=settings.E2B_API_KEY)
    result = sandbox.commands.run(command, timeout=_COMMAND_TIMEOUT_SECONDS, cwd="/home/user")
    return {"stdout": result.stdout or "", "stderr": result.stderr or "", "exit_code": result.exit_code}


def _list_output_files_sync(sandbox_id: str) -> list[dict[str, Any]]:
    from e2b import Sandbox

    sandbox: Sandbox = Sandbox.connect(sandbox_id, api_key=settings.E2B_API_KEY)
    try:
        entries = sandbox.files.list("/home/user/output")
    except Exception:
        return []
    output_files: list[dict[str, Any]] = []
    for entry in entries:
        if entry.type == "file":
            try:
                data: bytearray = sandbox.files.read(f"/home/user/output/{entry.name}", format="bytes")
                output_files.append({"filename": entry.name, "content_bytes": bytes(data)})
            except Exception as exc:
                logger.warning("[Sandbox] Failed to read output file %s: %s", entry.name, exc)
    return output_files


def _kill_sandbox_sync(sandbox_id: str) -> bool:
    from e2b import Sandbox

    try:
        return Sandbox.kill(sandbox_id, api_key=settings.E2B_API_KEY)
    except Exception as exc:
        logger.warning("[Sandbox] Failed to kill sandbox %s: %s", sandbox_id, exc)
        return False


# ---- Public cleanup helpers -------------------------------------------------

async def cleanup_sandbox(conversation_id: str, organization_id: str | None = None) -> None:
    sandbox_id: str | None = None
    if organization_id:
        sandbox_id = await _get_sandbox_id_from_db(conversation_id, organization_id)
    if sandbox_id is not None:
        logger.info("[Sandbox] Cleaning up sandbox %s for conversation %s", sandbox_id, conversation_id[:8])
        await asyncio.to_thread(_kill_sandbox_sync, sandbox_id)
        if organization_id:
            await _save_sandbox_id_to_db(conversation_id, organization_id, None)


async def cleanup_all_sandboxes() -> None:
    try:
        from e2b import Sandbox

        paginator = await asyncio.to_thread(lambda: Sandbox.list(api_key=settings.E2B_API_KEY))
        sandboxes = await asyncio.to_thread(paginator.next_items)
        if not sandboxes:
            return
        logger.info("[Sandbox] Shutting down %d active sandbox(es)", len(sandboxes))
        await asyncio.gather(
            *(asyncio.to_thread(_kill_sandbox_sync, s.sandbox_id) for s in sandboxes),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning("[Sandbox] Failed to list/kill sandboxes on shutdown: %s", exc)
