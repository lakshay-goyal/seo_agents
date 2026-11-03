"""Tier -> model name, skill tier/ladder resolution and reasoning effort (values come from `lib/models.py`)."""

from __future__ import annotations

import os

from seo_agents.lib.models import (DEFAULT_MODEL, REASONING_EFFORTS, TIER_MODEL_ENV, TIERS,
                                   WEAK_REASONING_EFFORTS)


def model_for_tier(tier='default', *, allow_large=False):
    """Model name for a tier: its OPENROUTER_<TIER>_MODEL override, else the default in lib/models.py."""
    base = os.getenv('OPENROUTER_MODEL', DEFAULT_MODEL)
    if tier == 'large' and not (allow_large or os.getenv('SEO_ALLOW_LARGE') == '1'):
        raise ValueError('Tier large models are offline-only; not used in the runtime path (set SEO_ALLOW_LARGE=1 offline)')
    if tier in TIER_MODEL_ENV:
        env_var, default = TIER_MODEL_ENV[tier]
        return os.getenv(env_var, default or base)
    return base


def tier_for_skill(meta, default='small'):
    """Tier declared by a skill's `tier` setting, if it is a runtime tier; else `default`."""
    tier = str((meta or {}).get('tier') or '').strip().lower()
    return tier if tier in TIERS and tier != 'large' else default


def ladder_for_skill(meta, default=('small', 'small', 'mid')):
    """Escalation ladder from a `ladder` setting ('small,haiku,mid' or a tuple), else `default`.

    A skill that only declares `tier: X` starts at X and still escalates to mid after two failures.
    """
    raw = (meta or {}).get('ladder')
    if raw:
        items = raw if isinstance(raw, (list, tuple)) else str(raw).split(',')
        steps = [s.strip().lower() for s in items if s.strip().lower() in TIERS and s.strip().lower() != 'large']
        if steps:
            return steps
    tier = tier_for_skill(meta, default[0])
    if tier == 'mid':
        return ['mid', 'mid']
    return [tier, tier, 'mid']


def reasoning_effort_for(tier, model, *, explicit=None, escalation=False):
    """Explicit > OPENROUTER_<TIER>_REASONING_EFFORT > global (never weakening mid/large/escalation) > default."""
    if explicit:
        effort = explicit
    else:
        per_tier = os.getenv('OPENROUTER_' + (tier if tier in TIERS else 'default').upper() + '_REASONING_EFFORT')
        global_effort = os.getenv('OPENROUTER_REASONING_EFFORT')
        strong = tier in {'mid', 'large'} or escalation
        if per_tier:
            effort = per_tier
        elif global_effort and not (strong and global_effort in WEAK_REASONING_EFFORTS):
            effort = global_effort
        elif model == DEFAULT_MODEL or tier == 'small':
            # Flash can spend the whole cap on thinking; small-tier answers are short structured choices.
            effort = 'none'
        else:
            effort = 'default'
    if effort not in REASONING_EFFORTS:
        raise ValueError('Invalid OPENROUTER_REASONING_EFFORT')
    if escalation and (effort in WEAK_REASONING_EFFORTS or effort == 'default'):
        effort = 'low'  # an escalation must actually reason
    return effort
