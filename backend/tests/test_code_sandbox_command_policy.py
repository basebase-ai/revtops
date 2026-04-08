import pytest

from connectors.code_sandbox import CodeSandboxConnector, get_blocked_package_install_reason


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
