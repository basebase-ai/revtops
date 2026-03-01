from config import settings
from models.database import _make_pgbouncer_safe_connect_args


def test_make_connect_args_includes_tcp_keepalives(monkeypatch):
    monkeypatch.setattr(settings, "DB_TCP_KEEPALIVES_IDLE_SECONDS", 75)
    monkeypatch.setattr(settings, "DB_TCP_KEEPALIVES_INTERVAL_SECONDS", 20)
    monkeypatch.setattr(settings, "DB_TCP_KEEPALIVES_COUNT", 4)

    connect_args = _make_pgbouncer_safe_connect_args()

    assert connect_args["server_settings"] == {
        "tcp_keepalives_idle": "75",
        "tcp_keepalives_interval": "20",
        "tcp_keepalives_count": "4",
    }
