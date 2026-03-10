"""
Pre-transpile JSX → JS using esbuild so the frontend can skip Babel Standalone.

Returns (compiled_code, component_name) on success, None on failure.
Failures are logged but never raised — the frontend falls back to runtime Babel.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Timeout for esbuild subprocess (seconds)
_ESBUILD_TIMEOUT = 10


def _find_esbuild() -> str | None:
    """Locate the esbuild binary."""
    path = shutil.which("esbuild")
    if path:
        return path
    # Common install locations
    for candidate in ["/usr/local/bin/esbuild", "/usr/bin/esbuild"]:
        if Path(candidate).is_file():
            return candidate
    return None


def _strip_module_syntax(code: str) -> str:
    """Strip import/export statements — mirrors SandpackAppRenderer.stripModuleSyntax()."""
    code = re.sub(r"^\s*import\s+.*?from\s+['\"].*?['\"];?\s*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"export\s+function\s+", "function ", code)
    code = re.sub(r"export\s+default\s+function\s+", "function ", code)
    code = re.sub(r"export\s+default\s+", "", code)
    code = re.sub(r"export\s+\{[^}]*\};?", "", code)
    return code


def _extract_component_name(code: str) -> str:
    """Extract the default-exported component name — mirrors transformAppCode()."""
    m = re.search(r"export\s+default\s+function\s+(\w+)", code)
    if m:
        return m.group(1)
    m = re.search(r"export\s+default\s+(\w+)\s*;?", code)
    if m:
        return m.group(1)
    return "App"


def transpile_jsx(source: str) -> tuple[str, str] | None:
    """
    Transpile Basebase app JSX source into plain JS.

    1. Extract the component name from the raw source.
    2. Strip imports/exports (same transforms as the frontend).
    3. Run esbuild to convert JSX → JS.

    Returns (compiled_js, component_name) on success, None on failure.
    """
    esbuild = _find_esbuild()
    if not esbuild:
        logger.warning("[transpile_jsx] esbuild binary not found, skipping transpilation")
        return None

    component_name = _extract_component_name(source)
    stripped = _strip_module_syntax(source)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsx", delete=False
        ) as tmp:
            tmp.write(stripped)
            tmp_path = tmp.name

        result = subprocess.run(
            [
                esbuild,
                tmp_path,
                "--bundle=false",
                "--loader=jsx",
                "--jsx=transform",
                "--target=es2020",
            ],
            capture_output=True,
            text=True,
            timeout=_ESBUILD_TIMEOUT,
        )

        if result.returncode != 0:
            logger.warning(
                "[transpile_jsx] esbuild failed",
                returncode=result.returncode,
                stderr=result.stderr[:500],
            )
            return None

        compiled = result.stdout
        return (compiled, component_name)

    except subprocess.TimeoutExpired:
        logger.warning("[transpile_jsx] esbuild timed out")
        return None
    except Exception:
        logger.exception("[transpile_jsx] unexpected error during transpilation")
        return None
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
