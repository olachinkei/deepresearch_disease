from __future__ import annotations

import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path


HIGH_CONFIDENCE_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9 ]+)?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Slack token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    "live payment key": re.compile(r"sk_live_[0-9A-Za-z]{16,}"),
    "credentialed URL": re.compile(r"https?://[^/@\s]+:[^/@\s]+@"),
}
SENSITIVE_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|
    hmac[_-]?secret|private[_-]?key|password|session[_-]?secret)\b
    \s*[:=]\s*
    ["']?([^"'#,\s}\]]+)
    """
)
SAFE_VALUE_PARTS = {
    "${{",
    "changeme",
    "dummy",
    "example",
    "fixture",
    "get_secret_value",
    "local",
    "mock",
    "not-used",
    "placeholder",
    "playwright",
    "replace",
    "secretstr(",
    "secrets.",
    "settings.",
    "synthetic",
    "test",
    "your_",
}
SKIP_CONTENT_SCAN = {
    "pnpm-lock.yaml",
    "services/agent/uv.lock",
    "data/public_seed/public-seed-v1.json",
    "apps/web/drizzle/meta/0000_snapshot.json",
}


def shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [Path(raw.decode()) for raw in result.stdout.split(b"\0") if raw]


def is_safe_assignment(value: str) -> bool:
    lowered = value.lower()
    if lowered in {"", "false", "none", "null", "true"}:
        return True
    return any(part in lowered for part in SAFE_VALUE_PARTS)


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in tracked_files():
        if path.as_posix() in SKIP_CONTENT_SCAN:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in HIGH_CONFIDENCE_PATTERNS.items():
                if pattern.search(line):
                    findings.append((path, line_number, label))

            for match in SENSITIVE_ASSIGNMENT.finditer(line):
                value = match.group(1)
                if is_safe_assignment(value):
                    continue
                if len(value) >= 20 and shannon_entropy(value) >= 3.5:
                    findings.append((path, line_number, "high-entropy sensitive assignment"))

    if findings:
        print("secret scan failed; review these locations without printing their values:", file=sys.stderr)
        for path, line_number, label in sorted(set(findings)):
            print(f"{path}:{line_number}: {label}", file=sys.stderr)
        return 1

    print("local secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
