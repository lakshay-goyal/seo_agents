"""Commit seo_reports/onpage/ in the checkout that holds it and push the current branch (updates the open PR)."""

from __future__ import annotations

from pathlib import Path
import subprocess

COMMIT_MESSAGE = 'chore(seo-agents): add on-page SEO proposals (current vs expected)'


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def publish(out_dir, message=COMMIT_MESSAGE):
    out = Path(out_dir).resolve()
    repo = Path(git(out, 'rev-parse', '--show-toplevel'))
    branch = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD')
    if branch in ('main', 'master', 'HEAD'):
        raise RuntimeError('refusing to push on ' + branch + '; check out the exploration PR branch first')
    relative = out.relative_to(repo).as_posix()
    git(repo, 'add', '--', relative)
    if not git(repo, 'status', '--porcelain', '--', relative):
        return 'nothing to commit'
    git(repo, 'commit', '-q', '-m', message)
    git(repo, 'push', 'origin', branch)
    return git(repo, 'rev-parse', 'HEAD') + ' on ' + branch
