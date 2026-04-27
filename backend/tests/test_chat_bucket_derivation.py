from api.routes import chat


def test_derive_bucket_returns_direct_for_private_scope() -> None:
    assert chat._derive_bucket(source="slack", scope="private", normalized_channel_id="C123") == (
        "direct",
        "direct",
    )


def test_derive_bucket_returns_direct_for_web_source() -> None:
    assert chat._derive_bucket(source="web", scope="shared", normalized_channel_id=None) == (
        "direct",
        "direct",
    )


def test_derive_bucket_returns_channel_for_non_dm_slack_channel() -> None:
    assert chat._derive_bucket(source="slack", scope="shared", normalized_channel_id="C123") == (
        "channel",
        "channel:C123",
    )


def test_derive_bucket_returns_direct_for_slack_dm_channel() -> None:
    assert chat._derive_bucket(source="slack", scope="shared", normalized_channel_id="D123") == (
        "direct",
        "direct",
    )


def test_derive_bucket_returns_uncategorized_for_other_sources() -> None:
    assert chat._derive_bucket(source="teams", scope="shared", normalized_channel_id=None) == (
        "uncategorized",
        "uncategorized",
    )
