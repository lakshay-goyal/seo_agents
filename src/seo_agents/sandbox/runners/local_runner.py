"""Local runner: same run/write interface, but executes directly.

Used by code running INSIDE the container, so clone and PR steps
work unchanged whether driven from the host or from in_container.py.
"""

import os
import subprocess
from pathlib import Path


class LocalResult:
    def __init__(self, stdout, stderr, exit_code):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def success(self):
        return self.exit_code == 0


class LocalRunner:
    def run(self, command, env=None):
        merged = dict(os.environ)
        for key, value in (env or {}).items():
            merged[key] = value
        proc = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env=merged)
        return LocalResult(proc.stdout, proc.stderr, proc.returncode)

    def write(self, path, content):
        if isinstance(content, str):
            content = content.encode()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
