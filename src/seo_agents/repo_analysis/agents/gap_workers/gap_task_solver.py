"""Gap-worker model calls: validation ladder (small -> small + feedback -> mid), self-consistency votes, transport\nbackoff, fail-fast on auth/credit errors, and a result cache keyed by packet + repo snapshot + code version."""

from __future__ import annotations

from collections import Counter
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import threading

from seo_agents.lib.models import gap_worker_max_tokens
from seo_agents.lib.prompts.system_prompts import gap_worker_retry_feedback
from seo_agents.utils.json_parsing import parse_json_object
from seo_agents.utils.step_logger import log
from seo_agents.repo_analysis.agents.gap_workers.gap_answer_validation import ENUM_KEYS, SCHEMAS, validate
from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import TASK_SKILL, load_skill

GAP_CODE_VERSION = '2.0.0'  # bump when packets, validators or apply() change: invalidates both caches


_AUTH_FAILED = threading.Event()
_AUTH_REASON = {'error': None}


def reset_auth_failure():
    _AUTH_FAILED.clear()
    _AUTH_REASON['error'] = None


# ---------------------------------------------------------------- model calls
def default_factory(tier, temperature=0, label=None, escalation=False):
    from seo_agents.llm.chat_model_factory import get_chat_model
    return get_chat_model(tier, max_tokens=gap_worker_max_tokens(tier), label=label, temperature=temperature,
                          escalation=escalation)


def make_model(factory, tier, temperature, label, escalation):
    try:
        accepts = 'escalation' in inspect.signature(factory).parameters
    except (TypeError, ValueError):
        accepts = False
    return factory(tier, temperature, label, escalation=escalation) if accepts else factory(tier, temperature, label)


def ask(model, system, packet, feedback=None, schema=None, name='answer'):
    """One validated-shape call. Transport errors back off; a provider that rejects response_format
    falls back to prompt-only JSON (brace scan)."""
    from langchain_core.messages import HumanMessage
    from seo_agents.llm.call_errors import call_with_backoff
    from seo_agents.llm.prompt_caching import cached_system_message, with_json_schema
    content = json.dumps(packet, ensure_ascii=False)
    if feedback:
        content += gap_worker_retry_feedback(feedback)
    messages = [cached_system_message(system, model), HumanMessage(content=content)]
    bound = with_json_schema(model, schema, name) if schema else model
    try:
        response = call_with_backoff(lambda: bound.invoke(messages))
    except Exception as error:  # noqa: BLE001 - only a response_format rejection is retried here
        if bound is model or not re.search(r'response_format|json_schema|structured', str(error), re.I):
            raise
        response = call_with_backoff(lambda: model.invoke(messages))
    return parse_json_object(response.content)


GAP_CACHE = Path(os.getenv('SEO_GAP_CACHE') if os.getenv('SEO_GAP_CACHE') not in {None, '', '0'} else Path.home() / '.cache' / 'seo_agents' / 'gap')


def cache_key(task, skill):
    from seo_agents.llm.model_tiers import model_for_tier
    payload = json.dumps([GAP_CODE_VERSION, task['task'], task['packet'], task.get('scope'), skill,
                          model_for_tier('small'), model_for_tier('haiku'), model_for_tier('mid')],
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def solve(task, family=None, *, model_factory=None):
    """Validated answer for one task; resolved answers are cached by packet + repo/snapshot + code version."""
    if _AUTH_FAILED.is_set():
        return {'status': 'failed', 'answer': None, 'tier': None, 'attempts': 0,
                'errors': ['auth: ' + str(_AUTH_REASON['error'] or 'provider rejected credentials')[:200]]}
    skill, _ = load_skill(TASK_SKILL[task['task']], family)
    use_cache = model_factory is None and os.getenv('SEO_GAP_CACHE', '') != '0'
    path = GAP_CACHE / (cache_key(task, skill) + '.json') if use_cache else None
    if path is not None and path.exists():
        try:
            cached = json.loads(path.read_text())
            return {**cached, 'cached': True}
        except ValueError:
            pass
    result = solve_uncached(task, family, model_factory=model_factory)
    if path is not None and result['status'] == 'resolved':
        try:
            GAP_CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result))
        except OSError:
            pass
    return result


def _provider_failure(error, errors, tier, attempt):
    from seo_agents.llm.call_errors import AuthError, TransportError, classify_error
    kind = 'auth' if isinstance(error, AuthError) else 'transport' if isinstance(error, TransportError) else classify_error(error)
    if kind is None:
        return None
    message = kind + ': ' + type(error).__name__ + ': ' + str(error)[:200]
    errors.append(message)
    if kind == 'auth':
        _AUTH_FAILED.set()
        _AUTH_REASON['error'] = str(error)[:200]
        log('gap workers: provider auth/credit failure; skipping remaining model calls: ' + str(error)[:160])
    return {'status': 'failed', 'answer': None, 'tier': tier, 'attempts': attempt, 'errors': errors, 'failure': kind}


def solve_uncached(task, family=None, *, model_factory=None):
    """Validation ladder (default small -> small+feedback -> mid). Provider failures stop the task."""
    from seo_agents.llm.model_tiers import ladder_for_skill
    factory = model_factory or default_factory
    skill, meta = load_skill(TASK_SKILL[task['task']], family)
    tiers = ladder_for_skill(meta)
    votes = int(meta.get('votes', '1'))
    schema = SCHEMAS.get(task['task'])
    errors = []
    if votes > 1:
        answers = []
        for index in range(votes):
            if index == 2 and len(answers) == 2 and all(answers[0].get(k) == answers[1].get(k) for k in ENUM_KEYS if k in answers[0]):
                break  # two agreeing votes: a third cannot change the majority
            try:
                model = make_model(factory, tiers[0], 0.5 if index else 0, task['task'], False)
                answers.append(validate(task, ask(model, skill, task['packet'], schema=schema, name=task['task'])))
            except Exception as error:  # noqa: BLE001 - each vote is independent; failures are counted
                failure = _provider_failure(error, errors, tiers[0], index + 1)
                if failure is not None:
                    return failure
                errors.append(type(error).__name__ + ': ' + str(error)[:160])
        if answers:
            merged, agreement = majority(answers)
            return finish(task, {'status': 'resolved', 'answer': merged, 'tier': tiers[0], 'votes': len(answers),
                                 'agreement': agreement, 'errors': errors})
        tiers = tiers[-1:]
    feedback = None
    for attempt, tier in enumerate(tiers, 1):
        try:
            model = make_model(factory, tier, 0, task['task'], attempt > 1 and tier != tiers[0])
            answer = validate(task, ask(model, skill, task['packet'], feedback, schema=schema, name=task['task']))
            return finish(task, {'status': 'resolved', 'answer': answer, 'tier': tier, 'attempts': attempt, 'errors': errors})
        except Exception as error:  # noqa: BLE001 - invalid answers are retried with the validator error, never applied
            failure = _provider_failure(error, errors, tier, attempt)
            if failure is not None:
                return failure
            feedback = type(error).__name__ + ': ' + str(error)[:200]
            errors.append(feedback)
    return {'status': 'unresolved', 'answer': None, 'tier': tiers[-1], 'attempts': len(tiers), 'errors': errors}


def finish(task, result):
    """Attach derived outputs (instances_suggested) so the orchestrator can feed them back to the manifest."""
    if task['task'] == 'enumerate_instances' and result['answer'].get('group') not in {None, 'none'}:
        from seo_agents.repo_analysis.site_model.url_manifest import substitute
        group = next((g for g in task.get('_groups') or [] if g['id'] == result['answer']['group']), None)
        if group:
            route = task['packet']['route']
            result['instances_suggested'] = [substitute(route, str(v)) for v in group['values'][:500]]
    return result


def majority(answers, enum_keys=None):
    """Majority per ENUM field; agreement = mean fraction of votes agreeing on each enum field.
    Free-text fields come from the first vote that matches the majority enums."""
    keys = [k for k in answers[0] if k in (enum_keys or ENUM_KEYS)]
    merged, agreement = {}, []
    for key in keys:
        value, count = Counter(json.dumps(a.get(key), sort_keys=True) for a in answers).most_common(1)[0]
        merged[key] = json.loads(value)
        agreement.append(count / len(answers))
    representative = max(answers, key=lambda a: sum(a.get(k) == merged[k] for k in keys))
    for key, value in representative.items():
        merged.setdefault(key, value)
    return merged, round(sum(agreement) / len(agreement), 2) if agreement else 1.0
