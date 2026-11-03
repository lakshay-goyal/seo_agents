"""Keyword judge: deterministic gates + Jev (TypeSafe) atomic judgments + an explicit decision table in code."""

from .jev_client import JevClient, JevError, JevResult
from .keyword_judge import HEURISTIC_MODE, JevKeywordJudge

__all__ = ['HEURISTIC_MODE', 'JevClient', 'JevError', 'JevKeywordJudge', 'JevResult']
