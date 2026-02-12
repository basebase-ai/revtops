from agents.registry import requires_approval


def test_save_memory_requires_approval_by_default() -> None:
    assert requires_approval("save_memory") is True
