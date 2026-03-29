from services.automated_agent_footer import ensure_automated_agent_footer


def test_ensure_automated_agent_footer_adds_footer_once() -> None:
    signed = ensure_automated_agent_footer("Hello there")
    assert "Done by an automated agent" in signed
    assert signed.startswith("Hello there")

    signed_again = ensure_automated_agent_footer(signed)
    assert signed_again == signed


def test_ensure_automated_agent_footer_handles_empty_text() -> None:
    signed = ensure_automated_agent_footer("")
    assert signed.startswith("— Done by an automated agent")
