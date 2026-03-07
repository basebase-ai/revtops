from __future__ import annotations

from pathlib import Path
import re

PUBLIC_SOURCE_DIRS = (
    Path("frontend/src"),
    Path("backend/api"),
    Path("backend/services"),
)
ALLOWED_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".md"}
BETA_WORD_RE = re.compile(r"\bbeta\b", re.IGNORECASE)


def test_no_beta_word_in_public_source_strings() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []

    for relative_dir in PUBLIC_SOURCE_DIRS:
        source_dir = repo_root / relative_dir
        if not source_dir.exists():
            continue

        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.suffix not in ALLOWED_SUFFIXES:
                continue
            if path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
                continue

            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if BETA_WORD_RE.search(line):
                    # Skip comments (Python: #, JavaScript/TypeScript: //, /* */, /** */)
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/**"):
                        continue
                    # Skip type definitions (e.g., 'alpha' | 'beta' | 'ga')
                    if "|" in line and ("'beta'" in line or '"beta"' in line):
                        continue
                    rel_path = path.relative_to(repo_root)
                    violations.append(f"{rel_path}:{line_number}: {line.strip()}")

    assert not violations, "Found disallowed 'beta' usage in public source:\n" + "\n".join(violations)
