"""Deterministic signal profiles (configuration, not prompts) used by the repo-analysis agents.

    profiles/<name>.json    scoped regex signals with precision labels and executable examples
    profiles/schema.json    the profile schema (checked by tools/signal_profiles/signal_profile_runner.py)
    skills/<name>/SKILL.md  human documentation for each profile (never read at runtime)
"""

from pathlib import Path

SIGNAL_PROFILES_DIR = Path(__file__).parent / 'profiles'
SIGNAL_PROFILE_DOCS_DIR = Path(__file__).parent / 'skills'
