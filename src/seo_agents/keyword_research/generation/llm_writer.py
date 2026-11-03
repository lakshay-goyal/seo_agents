"""Skill-guided keyword-research writer: selected principles + one skill -> JSON -> validator -> repair loop.

Mirrors onpage/writer/content_writer.py (same message layout, same escalation rule) but reads its prompts from
lib/prompts/keyword_research/, its model settings from AGENT_MODELS['keyword_research.<skill>'] and labels usage
'keyword_research:<skill>'. Numeric limits in the prompts are rendered from candidate_validation.LIMITS.

Environment:
  SEO_KEYWORD_TIER              force the starting tier for every keyword skill
  SEO_KEYWORD_ATTEMPTS          validation attempts per call (default 3; escalation to `mid` only after two failures)
  SEO_KEYWORD_REASONING_EFFORT  reasoning effort for mid-tier calls (default `low`; mid endpoints reject `none`)
  SEO_KEYWORD_MAX_TOKENS        reasoning + output cap per call (default 12000)
"""

from __future__ import annotations

from functools import lru_cache
import inspect
import json
import os
import re

from seo_agents.keyword_research.generation.candidate_validation import LIMITS
from seo_agents.lib.models import KEYWORD_MID_REASONING_EFFORT, KEYWORD_WRITER_MAX_TOKENS, skill_model_meta
from seo_agents.lib.prompts import KEYWORD_RESEARCH_PROMPTS_DIR
from seo_agents.lib.prompts.system_prompts import onpage_writer_retry_feedback
from seo_agents.utils.json_parsing import content_text, parse_json_object
from seo_agents.utils.prompt_files import split_frontmatter
from seo_agents.utils.step_logger import log

SKILLS_DIR = KEYWORD_RESEARCH_PROMPTS_DIR
AGENT_PREFIX = 'keyword_research'
TIERS = ('small', 'haiku', 'mid')


class KeywordWriterFailed(RuntimeError):
    def __init__(self, skill, errors):
        super().__init__(skill + ' failed validation: ' + ' | '.join(errors[-3:]))
        self.skill, self.errors = skill, errors


def render_limits(text):
    return re.sub(r'\{\{(\w+)\}\}', lambda m: str(LIMITS.get(m.group(1), m.group(0))), text)


@lru_cache(maxsize=None)
def principle_sections():
    """'## K1 ...' sections of _principles.md keyed by id (K1..Kn) plus 'intro' and 'output'."""
    text = render_limits((SKILLS_DIR / '_principles.md').read_text())
    parts = re.split(r'(?m)^## ', text)
    sections = {'intro': parts[0].strip()}
    for part in parts[1:]:
        title = part.split('\n', 1)[0]
        key = title.split()[0] if re.match(r'K\d+\b', title) else 'output'
        sections[key] = '## ' + part.strip()
    return sections


@lru_cache(maxsize=None)
def load_skill(name):
    """(system prompt, meta): selected principles + the skill body; model settings from lib/models.py."""
    meta, body = split_frontmatter((SKILLS_DIR / name / 'SKILL.md').read_text())
    meta = {**meta, **skill_model_meta(AGENT_PREFIX + '.' + name)}
    sections = principle_sections()
    wanted = [p.strip() for p in (meta.get('principles') or '').split(',') if p.strip()]
    parts = [sections['intro']] + [sections[p] for p in wanted if p in sections] + [sections.get('output', '')]
    return '\n\n'.join(p for p in parts if p) + '\n\n# Skill: ' + name + '\n' + render_limits(body.strip()), meta


def skill_ladder(name):
    meta = dict(load_skill(name)[1])
    forced = os.getenv('SEO_KEYWORD_TIER')
    if forced:
        meta = {'tier': forced}
    from seo_agents.llm.model_tiers import ladder_for_skill
    tier = meta.get('tier') if meta.get('tier') in TIERS else 'mid'
    return list(ladder_for_skill(meta, default=(tier, tier, 'mid')))


def default_factory(tier, label=None, temperature=0):
    from seo_agents.llm.chat_model_factory import get_chat_model
    # Mid-tier endpoints reject reasoning "none" (the .env global); small tier answers without reasoning.
    effort = (os.getenv('SEO_KEYWORD_REASONING_EFFORT') or KEYWORD_MID_REASONING_EFFORT) if tier == 'mid' else None
    return get_chat_model(tier, max_tokens=int(os.getenv('SEO_KEYWORD_MAX_TOKENS', str(KEYWORD_WRITER_MAX_TOKENS))),
                          label=label, temperature=temperature, reasoning_effort=effort)


def system_message(system, model=None):
    from langchain_core.messages import SystemMessage
    try:
        from seo_agents.llm.prompt_caching import cached_system_message
        return cached_system_message(system, model)
    except Exception:  # noqa: BLE001 - caching helper is optional
        return SystemMessage(content=system)


class KeywordWriter:
    """write(skill, packet, validate) -> (validated answer, meta).

    Prompt: lib/prompts/keyword_research/<skill>/SKILL.md; model: AGENT_MODELS['keyword_research.<skill>'].
    `model_factory(tier, label[, temperature])` returns a LangChain chat model (tests pass a fake)."""

    def __init__(self, model_factory=None, tier=None, attempts=None):
        self.factory = model_factory or default_factory
        self.tier = tier
        self.attempts = attempts or int(os.getenv('SEO_KEYWORD_ATTEMPTS', '3'))
        try:
            self._temperature_kw = 'temperature' in inspect.signature(self.factory).parameters
        except (TypeError, ValueError):
            self._temperature_kw = False

    def model(self, tier, label, temperature):
        if self._temperature_kw:
            return self.factory(tier, label, temperature=temperature)
        return self.factory(tier, label)

    def ask(self, model, system, packet, history):
        from langchain_core.messages import AIMessage, HumanMessage
        messages = [system_message(system, getattr(model, 'bound', model)),
                    HumanMessage(content=json.dumps(packet, ensure_ascii=False, default=str))]
        for raw, feedback in history:
            messages += [AIMessage(content=raw), HumanMessage(content=onpage_writer_retry_feedback(feedback))]
        response = model.invoke(messages)
        if isinstance(response, dict):
            return response
        return content_text(getattr(response, 'content', response))

    def write(self, skill, packet, validate):
        system, meta = load_skill(skill)
        ladder = [self.tier, self.tier, 'mid'] if self.tier else skill_ladder(skill)
        temperature = float(meta.get('temperature', 0))
        history, errors = [], []
        for attempt in range(1, self.attempts + 1):
            tier = ladder[min(attempt, len(ladder)) - 1]  # escalate only after two validation failures
            raw = ''
            try:
                model = self.model(tier, AGENT_PREFIX + ':' + skill, temperature)
                raw = self.ask(model, system, packet, history)
                answer = validate(parse_json_object(raw))
                return answer, {'skill': skill, 'tier': tier, 'attempts': attempt, 'errors': errors}
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                feedback = type(error).__name__ + ': ' + str(error)[:1200]
                errors.append(feedback)
                history.append((raw if isinstance(raw, str) and raw else json.dumps(raw) if isinstance(raw, dict) else '{}',
                                feedback))
                log(f'keyword_research {skill}: attempt {attempt} rejected: {feedback[:160]}')
        raise KeywordWriterFailed(skill, errors)
