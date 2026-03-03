from __future__ import annotations

from collections import Counter
from pathlib import Path


def _migration_numbers() -> list[int]:
    versions_dir = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
    migration_numbers: list[int] = []

    for path in versions_dir.glob("*.py"):
        name = path.stem
        prefix = name.split("_", 1)[0]
        if prefix.isdigit():
            migration_numbers.append(int(prefix))

    return migration_numbers


def test_no_duplicate_migration_numbers_above_84() -> None:
    """Prevent multiple same-number Alembic scripts once migration numbers exceed 084."""
    number_counts = Counter(_migration_numbers())
    duplicate_numbers_above_84 = sorted(
        number for number, count in number_counts.items() if number > 84 and count > 1
    )

    assert not duplicate_numbers_above_84, (
        "Found duplicate Alembic migration numbers above 084: "
        f"{duplicate_numbers_above_84}. Keep only one migration file per number > 084."
    )
