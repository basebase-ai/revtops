#!/usr/bin/env python3
"""Interactive Perplexity Sonar API tester — prints raw JSON responses.

Usage:
  python3 test_perplexity.py                         # interactive REPL
  python3 test_perplexity.py "some query"            # one-shot with defaults
  python3 test_perplexity.py --model sonar-pro --recency week --search-mode academic "some query"

Options (all optional, usable in CLI or REPL via /set):
  --model           sonar | sonar-pro | sonar-deep-research | sonar-reasoning-pro  (default: sonar)
  --search-mode     web | academic | sec                                           (default: web)
  --recency         hour | day | week | month | year
  --domains         comma-separated domain allow/block list (prefix with - to exclude)
  --lang            comma-separated search language codes (e.g. en,de)
  --return-images   include images in response
  --return-related  include related questions
  --max-tokens      max output tokens (up to 128000)
  --system          custom system prompt

REPL commands:
  /set <option> <value>   change an option   e.g. /set model sonar-pro
  /unset <option>         clear an option     e.g. /unset recency
  /options                show current options
"""

import argparse
import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

API_KEY: str | None = os.getenv("PERPLEXITY_API_KEY")
URL: str = "https://api.perplexity.ai/chat/completions"

DEFAULT_SYSTEM: str = (
    "You are a helpful research assistant. Provide concise, factual answers with relevant details."
)


def build_payload(query: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Assemble the full request body from query + options."""
    payload: dict[str, Any] = {
        "model": opts.get("model", "sonar"),
        "messages": [
            {"role": "system", "content": opts.get("system", DEFAULT_SYSTEM)},
            {"role": "user", "content": query},
        ],
    }

    if opts.get("search_mode"):
        payload["search_mode"] = opts["search_mode"]
    if opts.get("recency"):
        payload["search_recency_filter"] = opts["recency"]
    if opts.get("domains"):
        payload["search_domain_filter"] = [d.strip() for d in opts["domains"].split(",")]
    if opts.get("lang"):
        payload["search_language_filter"] = [l.strip() for l in opts["lang"].split(",")]
    if opts.get("return_images"):
        payload["return_images"] = True
    if opts.get("return_related"):
        payload["return_related_questions"] = True
    if opts.get("max_tokens"):
        payload["max_tokens"] = int(opts["max_tokens"])

    return payload


def search(query: str, opts: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = build_payload(query, opts)
    with httpx.Client(timeout=60.0) as client:
        resp: httpx.Response = client.post(
            URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    return {"status_code": resp.status_code, "body": resp.json()}


# ── REPL helpers ──────────────────────────────────────────────────────

OPTION_ALIASES: dict[str, str] = {
    "model": "model",
    "search-mode": "search_mode",
    "search_mode": "search_mode",
    "recency": "recency",
    "domains": "domains",
    "lang": "lang",
    "return-images": "return_images",
    "return_images": "return_images",
    "return-related": "return_related",
    "return_related": "return_related",
    "max-tokens": "max_tokens",
    "max_tokens": "max_tokens",
    "system": "system",
}

BOOL_KEYS: set[str] = {"return_images", "return_related"}


def handle_repl_command(line: str, opts: dict[str, Any]) -> bool:
    """Handle /commands. Returns True if the line was a command."""
    parts: list[str] = line.split(None, 2)
    cmd: str = parts[0].lower()

    if cmd == "/options":
        active: dict[str, Any] = {k: v for k, v in opts.items() if v is not None}
        print(json.dumps(active if active else {"(all defaults)": True}, indent=2))
        return True

    if cmd == "/set" and len(parts) >= 3:
        key: str | None = OPTION_ALIASES.get(parts[1])
        if key is None:
            print(f"Unknown option: {parts[1]}  (valid: {', '.join(sorted(OPTION_ALIASES))})")
        else:
            value: Any = parts[2]
            if key in BOOL_KEYS:
                value = value.lower() in ("true", "1", "yes")
            opts[key] = value
            print(f"  {key} = {value!r}")
        return True

    if cmd == "/unset" and len(parts) >= 2:
        key = OPTION_ALIASES.get(parts[1])
        if key is None:
            print(f"Unknown option: {parts[1]}")
        else:
            opts.pop(key, None)
            print(f"  {key} cleared")
        return True

    return False


# ── CLI ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Perplexity Sonar API tester")
    p.add_argument("query", nargs="*", help="Search query (omit for interactive REPL)")
    p.add_argument("--model", default="sonar")
    p.add_argument("--search-mode", dest="search_mode")
    p.add_argument("--recency")
    p.add_argument("--domains")
    p.add_argument("--lang")
    p.add_argument("--return-images", dest="return_images", action="store_true")
    p.add_argument("--return-related", dest="return_related", action="store_true")
    p.add_argument("--max-tokens", dest="max_tokens", type=int)
    p.add_argument("--system", default=DEFAULT_SYSTEM)
    return p.parse_args()


def main() -> None:
    if not API_KEY:
        print("ERROR: PERPLEXITY_API_KEY not found in .env", file=sys.stderr)
        sys.exit(1)

    args: argparse.Namespace = parse_args()
    opts: dict[str, Any] = {
        k: v for k, v in vars(args).items() if k != "query" and v is not None and v is not False
    }

    # One-shot mode
    if args.query:
        query: str = " ".join(args.query)
        print(json.dumps(search(query, opts), indent=2))
        return

    # Interactive REPL
    print("Perplexity Sonar API tester  (Ctrl-C to quit)")
    print("Type /options, /set <key> <val>, /unset <key> for config\n")
    while True:
        try:
            line: str = input("query> ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            handle_repl_command(line, opts)
            continue
        result: dict[str, Any] = search(line, opts)
        print(json.dumps(result, indent=2))
        print()


if __name__ == "__main__":
    main()
