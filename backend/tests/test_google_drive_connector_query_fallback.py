import asyncio

from connectors.google_drive import GoogleDriveConnector


def test_get_file_content_falls_back_to_live_snapshot_when_metadata_missing(monkeypatch) -> None:
    connector = GoogleDriveConnector(
        organization_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
    )

    async def _fake_get_oauth_token(*args, **kwargs):
        return "token", ""

    async def _fake_shared_snapshot(external_id: str):
        assert external_id == "drive_file_123"
        return None

    async def _fake_live_snapshot(external_id: str):
        assert external_id == "drive_file_123"
        return {
            "name": "Live Doc",
            "mime_type": "text/plain",
            "folder_path": "/",
            "web_view_link": "https://drive.google.com/file/d/drive_file_123/view",
        }

    async def _fake_download_file(*args, **kwargs):
        return "hello from live fallback"

    monkeypatch.setattr(connector, "get_oauth_token", _fake_get_oauth_token)
    monkeypatch.setattr(connector, "_get_shared_file_snapshot", _fake_shared_snapshot)
    monkeypatch.setattr(connector, "_get_live_file_snapshot", _fake_live_snapshot)
    monkeypatch.setattr(connector, "_download_file", _fake_download_file)

    result = asyncio.run(connector.get_file_content("drive_file_123"))

    assert result["external_id"] == "drive_file_123"
    assert result["file_name"] == "Live Doc"
    assert result["content"] == "hello from live fallback"

