"""Small offline credential tripwire. Reports locations only, never matching values.

This is not an entropy scanner or proof of absence. --history audits existing
local exposure; --staged is suitable for pre-commit enforcement.
"""

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class Finding:
    line: int
    rule: str
    value: str = "SECRET_REDACTED"


SECRET_NAME = re.compile(r"(?i)(?:^|_)(?:api_?key|password|passwd|token|secret)$")
PATTERNS = {
    "provider-token": re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})"),
    "credential-url": re.compile(r"\w+://[^\s:/]+:[^\s@]+@"),
    "private-key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
}


def find_secrets(content: str, filename: str) -> tuple[Finding, ...]:
    findings = set()
    for number, line in enumerate(content.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.add(Finding(number, rule))
        # Environment/Compose assignments, including unquoted YAML values.
        match = re.match(r"\s*([A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(.*?)\s*$", line)
        if match and SECRET_NAME.search(match[1]) and match[2] and not match[2].startswith(("${", "#", "os.", "field(")):
            if match[2].strip("\"'"):
                if not filename.endswith(".py") or match[1].isupper():
                    findings.add(Finding(number, "literal-credential"))
    if filename.endswith(".py"):
        try:
            tree = ast.parse(content)
        except SyntaxError:
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key, val in zip(node.keys, node.values):
                        if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                                and SECRET_NAME.search(key.value) and isinstance(val, ast.Constant)
                                and isinstance(val.value, str) and val.value):
                            findings.add(Finding(key.lineno, "literal-credential"))
                value, names = None, []
                if isinstance(node, ast.keyword):
                    value, names = node.value, [node.arg or ""]
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    names = [x.id for x in targets if isinstance(x, ast.Name)]
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                    if any(SECRET_NAME.search(name) for name in names):
                        findings.add(Finding(node.lineno, "literal-credential"))
    if filename.endswith((".json", ".ipynb")):
        try:
            document = json.loads(content)
        except json.JSONDecodeError:
            document = None

        def inspect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if SECRET_NAME.search(key) and isinstance(item, str) and item:
                        findings.add(Finding(1, "structured-credential"))
                    inspect(item)
                if value.get("cell_type") == "code":
                    source = value.get("source", [])
                    source = source if isinstance(source, str) else "".join(source)
                    if find_secrets(source, "cell.py"):
                        findings.add(Finding(1, "notebook-credential"))
            elif isinstance(value, list):
                for item in value:
                    inspect(item)

        inspect(document)
    return tuple(sorted(findings, key=lambda f: (f.line, f.rule)))


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def scan_repository(*, staged: bool = False, history: bool = False) -> int:
    count = 0
    if history:
        objects = []
        seen = set()
        for commit in git("rev-list", "--all").decode().splitlines():
            for entry in git("ls-tree", "-rz", commit).split(b"\0"):
                if not entry:
                    continue
                meta, path = entry.split(b"\t", 1)
                oid = meta.split()[2].decode()
                if oid not in seen:
                    seen.add(oid)
                    objects.append((f"history/{commit[:8]}/{path.decode()}", oid))
    elif staged:
        objects = [(p.decode(), ":" + p.decode()) for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split(b"\0") if p]
    else:
        objects = [(p.decode(), None) for p in set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split(b"\0")) if p]
    for filename, ref in sorted(objects):
        if Path(filename).suffix in {".parquet", ".png", ".pyc"}:
            continue
        if ref:
            data = git("show", ref) if staged else git("cat-file", "blob", ref)
        else:
            path = Path(filename)
            if not path.is_file():
                continue
            data = path.read_bytes()
        for finding in find_secrets(data.decode("utf-8", errors="replace"), filename):
            count += 1
            print(f"{filename}:{finding.line}: {finding.rule}: SECRET_REDACTED")
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--history", action="store_true")
    args = parser.parse_args()
    raise SystemExit(bool(scan_repository(staged=args.staged, history=args.history)))
