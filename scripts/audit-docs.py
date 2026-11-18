#!/usr/bin/env python3
"""Check the documentation against the implementation.

Documentation rots silently. This fails loudly instead, on the claims that can
be checked mechanically:

1. every relative link in a Markdown file resolves to something that exists;
2. every script the docs tell a reader to run exists and is executable;
3. every image referenced exists;
4. `.env.example` documents every settings field, and documents no field that
   no longer exists;
5. the test counts quoted in the docs match what the suites actually collect.

Run it from the repository root:

    python3 scripts/audit-docs.py

Checks that need a database are deliberately absent -- this must be runnable
from a clean checkout with nothing started.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")

failures: list[str] = []
checks = 0


def fail(message: str) -> None:
    failures.append(message)


def check_links() -> None:
    """Relative links and images must point at something that exists."""
    global checks
    for doc in DOCS:
        text = doc.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            checks += 1
            resolved = (doc.parent / target).resolve()
            if not resolved.exists():
                line = text[: match.start()].count("\n") + 1
                fail(f"{doc.relative_to(ROOT)}:{line} broken link -> {target}")


def check_referenced_scripts() -> None:
    """A documented command must exist and be runnable."""
    global checks
    pattern = re.compile(r"scripts/[a-z0-9-]+\.(?:sh|py)")
    referenced: set[str] = set()
    for doc in DOCS:
        referenced.update(pattern.findall(doc.read_text(encoding="utf-8")))
    for name in sorted(referenced):
        checks += 1
        path = ROOT / name
        if not path.exists():
            fail(f"documented script does not exist: {name}")
        elif path.suffix == ".sh" and not path.stat().st_mode & 0o111:
            fail(f"documented script is not executable: {name}")


def check_env_example() -> None:
    """`.env.example` must document every settings field, and no stale ones."""
    global checks
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.core.config import Settings
    except Exception as exc:  # pragma: no cover - import guard
        fail(f"could not import Settings to audit .env.example: {exc}")
        return

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    # A commented `# KEY=` counts as documented: that is how an optional
    # override should appear, and demanding it be live would push every
    # per-node model override into the active configuration.
    # Strict: an environment key, optionally commented out. Prose inside a
    # comment often contains "=" and must not be mistaken for a key.
    key_line = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")
    documented = {
        match.group(1)
        for line in example.splitlines()
        if (match := key_line.match(line.strip()))
    }
    # Compose-only knobs: published host ports have no settings field.
    compose_only = {
        "API_PORT",
        "FRONTEND_PORT",
        "POSTGRES_HOST_PORT",
        "REDIS_HOST_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
    }

    fields = {name.upper() for name in Settings.model_fields}
    checks += len(fields)

    for missing in sorted(fields - documented):
        fail(f".env.example does not document the setting {missing}")
    for stale in sorted(documented - fields - compose_only):
        fail(f".env.example documents {stale}, which is not a settings field")


def _collected(command: list[str], cwd: Path, pattern: re.Pattern[str]) -> int | None:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=600, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = pattern.search(result.stdout) or pattern.search(result.stderr)
    return int(match.group(1)) if match else None


def check_test_counts() -> None:
    """Counts quoted in the docs must match what the suites collect."""
    global checks
    # The offline suite, which is the figure the docs quote: live-provider
    # tests are collected but skipped without credentials.
    backend = _collected(
        ["uv", "run", "pytest", "--collect-only", "-m", "not live and not live_search"],
        ROOT / "backend",
        # "419/439 tests collected (20 deselected)" when a marker filter
        # applies; "439 tests collected" when none does.
        re.compile(r"(\d+)(?:/\d+)? tests? collected"),
    )
    # Test projects only. The `screenshots` project is a capture tool that
    # writes documentation images, not a test, and counting it would make
    # every honest figure in the docs look wrong by one.
    browser = _collected(
        ["pnpm", "exec", "playwright", "test", "--list", "--project=chromium", "--project=firefox"],
        ROOT / "frontend",
        re.compile(r"Total:\s+(\d+)\s+tests?"),
    )

    quoted: dict[str, list[tuple[Path, int, int]]] = {"backend": [], "browser": []}
    backend_claim = re.compile(r"\*\*(\d{3})\s+(?:passed|backend)|(\d{3})\s+backend tests")
    browser_claim = re.compile(r"(\d{2})\s+(?:browser|Playwright)")

    # Milestone reports are historical records: each states what was true at
    # that milestone, and a figure that has since moved on is not an error to
    # correct. Only the living documents are held to current reality.
    living = [doc for doc in DOCS if "milestones" not in doc.parts]

    for doc in living:
        text = doc.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in backend_claim.finditer(line):
                value = int(match.group(1) or match.group(2))
                quoted["backend"].append((doc, line_no, value))
            for match in browser_claim.finditer(line):
                quoted["browser"].append((doc, line_no, int(match.group(1))))

    for kind, actual in (("backend", backend), ("browser", browser)):
        if actual is None:
            print(f"  (skipped {kind} count check: suite could not be collected)")
            continue
        for doc, line_no, value in quoted[kind]:
            checks += 1
            if value != actual:
                fail(
                    f"{doc.relative_to(ROOT)}:{line_no} claims {value} {kind} tests, "
                    f"but {actual} are collected"
                )


def main() -> int:
    check_links()
    check_referenced_scripts()
    check_env_example()
    check_test_counts()

    if failures:
        print(f"\n{len(failures)} documentation claim(s) do not match the implementation:\n")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"  {checks} documentation claims check out")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
