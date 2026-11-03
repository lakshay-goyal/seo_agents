"""Skill-guided LLM writer: relevant principles + one skill -> JSON answer -> validator -> repair loop.

The system prompt holds only the principle sections a skill lists in its `principles:` frontmatter (audit §8: the full
_principles.md was ~19% of input and mostly irrelevant), with numeric limits rendered from `policy.LIMITS` so the prompt
and the validator can never disagree. The system block is marked cacheable when the model layer supports it.

Environment:
  SEO_ONPAGE_TIER      force the starting tier for every skill (default: the skill's `tier:`/`ladder:` frontmatter)
  SEO_ONPAGE_ATTEMPTS  validation attempts per task (default 3; escalation to `mid` only after two failures)
  SEO_ONPAGE_REASONING_EFFORT  reasoning effort for mid-tier writer calls (default `low`)
  SEO_ONPAGE_MAX_TOKENS        reasoning + output cap per call (default 16000)
"""

from __future__ import annotations

from functools import lru_cache
import inspect
import json
import os
from pathlib import Path
import re

from seo_agents.log import log
from seo_agents.onpage.policy import render_limits

SKILLS_DIR = Path(__file__).parent / 'skills'
TIERS = ('small', 'haiku', 'mid')


class WriterFailed(RuntimeError):
    def __init__(self, skill, errors):
        super().__init__(skill + ' failed validation: ' + ' | '.join(errors[-3:]))
        self.skill, self.errors = skill, errors


@lru_cache(maxsize=None)
def principle_sections():
    """'## P1 ...' sections of _principles.md keyed by id (P1..P10) plus 'intro' and 'output'."""
    text = render_limits((SKILLS_DIR / '_principles.md').read_text())
    parts = re.split(r'(?m)^## ', text)
    sections = {'intro': parts[0].strip()}
    for part in parts[1:]:
        title = part.split('\n', 1)[0]
        key = title.split()[0] if re.match(r'P\d+\b', title) else 'output'
        sections[key] = '## ' + part.strip()
    return sections


def parse_frontmatter(text):
    meta, body = {}, text
    if text.startswith('---'):
        _, front, body = text.split('---', 2)
        meta = dict(re.findall(r'(?m)^(\w+):\s*(.+)$', front))
    return meta, body


@lru_cache(maxsize=None)
def load_skill(name):
    """(system prompt, frontmatter): selected principles + the skill body, limits rendered from policy."""
    meta, body = parse_frontmatter((SKILLS_DIR / name / 'SKILL.md').read_text())
    sections = principle_sections()
    wanted = [p.strip() for p in (meta.get('principles') or '').split(',') if p.strip()]
    parts = [sections['intro']] + [sections[p] for p in wanted if p in sections]
    if meta.get('kind') == 'llm':
        parts.append(sections.get('output', ''))
    return '\n\n'.join(p for p in parts if p) + '\n\n# Skill: ' + name + '\n' + render_limits(body.strip()), meta


def skill_catalog():
    rows = []
    for path in sorted(SKILLS_DIR.glob('*/SKILL.md')):
        _, meta = load_skill(path.parent.name)
        rows.append({'name': meta.get('name', path.parent.name), 'kind': meta.get('kind'), 'tier': meta.get('tier'),
                     'description': meta.get('description'), 'principles': meta.get('principles')})
    return rows


def skill_ladder(name):
    """Tier per attempt: the skill's `tier:`/`ladder:` frontmatter (tools/llm.py helpers), escalating to mid only
    after two validation failures. SEO_ONPAGE_TIER forces the starting tier for every skill."""
    meta = dict(load_skill(name)[1])
    forced = os.getenv('SEO_ONPAGE_TIER')
    if forced:
        meta = {'tier': forced}
    try:
        from seo_agents.exploration.repo_analysis.tools.llm import ladder_for_skill
        ladder = list(ladder_for_skill(meta, default=(meta.get('tier') or 'mid', meta.get('tier') or 'mid', 'mid')))
        if ladder:
            return ladder
    except Exception:  # noqa: BLE001 - optional helper; local fallback below
        pass
    tier = meta.get('tier') if meta.get('tier') in TIERS else 'mid'
    return [tier, tier, 'mid']


def parse_json(content):
    if isinstance(content, list):
        content = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    if isinstance(content, dict):
        return content
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content.strip())
    start, end = content.find('{'), content.rfind('}')
    if start < 0 or end < start:
        raise ValueError('answer is not a JSON object')
    return json.loads(content[start:end + 1])


def default_factory(tier, label=None, temperature=0):
    from seo_agents.exploration.repo_analysis.tools.llm import get_chat_model
    # Mid-tier endpoints reject reasoning "none"; small tier answers short structured questions without reasoning.
    effort = (os.getenv('SEO_ONPAGE_REASONING_EFFORT') or 'low') if tier == 'mid' else None
    return get_chat_model(tier, max_tokens=int(os.getenv('SEO_ONPAGE_MAX_TOKENS', '16000')), label=label,
                          temperature=temperature, reasoning_effort=effort)


def system_message(system, model=None):
    from langchain_core.messages import SystemMessage
    try:
        from seo_agents.exploration.repo_analysis.tools.llm import cached_system_message
        return cached_system_message(system, model)
    except Exception:  # noqa: BLE001 - caching helper is optional
        return SystemMessage(content=system)


def structured(model, schema, name):
    """Structured output (JSON schema) when the model layer supports it; otherwise the raw model + brace parsing."""
    if not schema:
        return model
    try:
        from seo_agents.exploration.repo_analysis.tools.llm import with_json_schema
        return with_json_schema(model, schema, name=name) or model
    except Exception:  # noqa: BLE001
        return model


class Writer:
    """write(skill, packet, validate, schema=None) -> validated answer.
    `model_factory(tier, label[, temperature])` returns a LangChain chat model."""

    def __init__(self, model_factory=None, tier=None, attempts=None):
        self.factory = model_factory or default_factory
        self.tier = tier
        self.attempts = attempts or int(os.getenv('SEO_ONPAGE_ATTEMPTS', '3'))
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
        messages = [system_message(system, getattr(model, 'bound', model)), HumanMessage(content=json.dumps(packet, ensure_ascii=False))]
        for raw, feedback in history:
            messages += [AIMessage(content=raw), HumanMessage(
                content='Your answer was rejected by the validator: ' + feedback + '\nReturn the corrected JSON object only.')]
        response = model.invoke(messages)
        if isinstance(response, dict):
            return response
        content = getattr(response, 'content', response)
        if isinstance(content, list):
            content = ''.join(p.get('text', '') if isinstance(p, dict) else str(p) for p in content)
        return content

    def write(self, skill, packet, validate, schema=None):
        system, meta = load_skill(skill)
        ladder = [self.tier, self.tier, 'mid'] if self.tier else skill_ladder(skill)
        temperature = float(meta.get('temperature', 0))
        history, errors = [], []
        for attempt in range(1, self.attempts + 1):
            # Escalate only after two validation failures, never on the first (audit §13).
            tier = ladder[min(attempt, len(ladder)) - 1]
            raw = ''
            try:
                model = structured(self.model(tier, 'onpage:' + skill, temperature), schema, skill)
                raw = self.ask(model, system, packet, history)
                answer = validate(parse_json(raw))
                return answer, {'skill': skill, 'tier': tier, 'attempts': attempt, 'errors': errors}
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                feedback = type(error).__name__ + ': ' + str(error)[:400]
                errors.append(feedback)
                history.append((raw if isinstance(raw, str) and raw else json.dumps(raw) if isinstance(raw, dict) else '{}', feedback))
                log(f'onpage {skill}: attempt {attempt} rejected: {feedback[:160]}')
        raise WriterFailed(skill, errors)
