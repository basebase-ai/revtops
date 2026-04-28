import uuid

import pytest
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm.exc import DetachedInstanceError

from connectors import zoom


class _State:
    def __init__(self, identity):
        self.identity = identity


class _DetachedMeeting:
    @property
    def id(self):
        raise DetachedInstanceError("detached")


def test_safe_meeting_id_uses_sqlalchemy_identity(monkeypatch):
    meeting_id = uuid.uuid4()
    monkeypatch.setattr(zoom, "sa_inspect", lambda _obj: _State((meeting_id,)))

    result = zoom._safe_meeting_id(_DetachedMeeting())

    assert result == meeting_id


def test_safe_meeting_id_uses_dict_fallback(monkeypatch):
    meeting_id = uuid.uuid4()
    monkeypatch.setattr(
        zoom,
        "sa_inspect",
        lambda _obj: (_ for _ in ()).throw(NoInspectionAvailable("no inspection")),
    )
    obj = type("MeetingLike", (), {})()
    obj.id = meeting_id

    result = zoom._safe_meeting_id(obj)

    assert result == meeting_id


def test_safe_meeting_id_raises_for_detached_without_any_id(monkeypatch):
    monkeypatch.setattr(
        zoom,
        "sa_inspect",
        lambda _obj: (_ for _ in ()).throw(NoInspectionAvailable("no inspection")),
    )

    with pytest.raises(ValueError):
        zoom._safe_meeting_id(_DetachedMeeting())
