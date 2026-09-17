"""Show a review diff without reproducing historical credential values."""

import re
import subprocess
import sys

if __name__ == "__main__":
    diff = subprocess.check_output(["git", "diff", sys.argv[1]], text=True)
    sensitive = re.compile(r"(?i)password|api_key|secret|token|\w+://[^\s:/]+:[^\s@]+@|sk-[A-Za-z0-9_-]{16,}")
    for line in diff.splitlines():
        if line.startswith(("+", "-", " ")) and sensitive.search(line):
            print(line[:1] + " SECRET_REDACTED")
        else:
            print(line)
