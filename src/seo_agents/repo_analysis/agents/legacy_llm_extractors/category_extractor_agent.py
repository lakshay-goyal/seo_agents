"""One fresh agent and domain skill per on-page exploration category.

Harness design (see docs/seo-agents-analysis.html §8):
- prompt order is cache-friendly: static role → common contract → category skill/schema →
  allowed methods; the per-run manifest and budgets go in the user message;
- the script is built in a virtual file (`write_file` / `edit_file`) and checked with
  `test_script` during discovery, so a draft is tested before an attempt is spent;
- each repair attempt starts a FRESH, compact conversation (task, current draft, exact
  validator errors, pinned evidence) with its own small tool budget, instead of resending
  the whole transcript with no tools.
"""

from dataclasses import dataclass
import hashlib
import json
import os
import time

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import AIMessage

from seo_agents.repo_analysis.tools.repository.repository_snapshot import snapshot_repository
from seo_agents.repo_analysis.tools.parsing.source_context import build_context
from seo_agents.repo_analysis.agents.legacy_llm_extractors.prior_report_handoff import handoff_summary
from seo_agents.repo_analysis.agents.legacy_llm_extractors.cross_report_references import validate_content_references
from seo_agents.repo_analysis.agents.legacy_llm_extractors.sandboxed_script.script_policy import METHODS, entry_arity, validate_script
from seo_agents.repo_analysis.agents.legacy_llm_extractors.repository_retrieval_tools import repository_tools, repository_summary
from seo_agents.repo_analysis.agents.legacy_llm_extractors.observation_verification import (compact_index, execute_script,
                                                                          validate_observations, verify_and_extract)
from seo_agents.repo_analysis.agents.legacy_llm_extractors.report_contracts import PAGE_FIELDS
from seo_agents.repo_analysis.tools.repository.repository_constants import REPORT_FILES
from seo_agents.llm.chat_model_factory import get_chat_model
from seo_agents.lib.models import agent_model
from seo_agents.lib.prompts import LEGACY_EXTRACTOR_PROMPTS_DIR
from seo_agents.lib.tool_mapping import LEGACY_EXTRACTOR_TOOLS
from seo_agents.lib.prompts.system_prompts import (legacy_extractor_common_contract, legacy_extractor_missing_plan_error,
                                                   legacy_extractor_no_plan_nudge, legacy_extractor_repair_brief,
                                                   legacy_extractor_system_prompt, legacy_extractor_task)
from seo_agents.utils.env_settings import bounded_int
from seo_agents.utils.step_logger import log, progress

SKILLS = LEGACY_EXTRACTOR_PROMPTS_DIR
DRAFT = 'extractor.py'


@dataclass
class ExtractionPlan:
    """Final answer. `source` may be empty: the draft extractor.py written with write_file/edit_file is then used."""

    framework: str
    notes: str
    source: str = ''


class ExtractionFailed(RuntimeError):
    """Retain the rejected program and repair history for the partial checkpoint."""
    def __init__(self, category, run):
        self.category, self.run = category, run
        super().__init__(category + ' extraction failed verification after 3 attempts: ' + run['previous_errors'][-1])


def system_prompt(category):
    """Static per category, so provider prefix caching applies across runs and repairs."""
    skill = (SKILLS / category / 'SKILL.md').read_text()
    common = (SKILLS / 'contract-common.md').read_text()
    common = legacy_extractor_common_contract(common, METHODS)
    prompt = legacy_extractor_system_prompt(category, common, skill, PAGE_FIELDS[category])
    return prompt, skill, common


def route_mismatch(report, routes):
    actual = {(p['url'], p['template']) for p in report['pages']}
    expected = {(p['url'], p['template']) for p in routes}
    if actual == expected:
        return None
    return ('Report routes must match the verified_route_catalog from list_routes; missing pairs: ' +
            json.dumps(sorted(expected - actual)[:6]) + '; unexpected pairs: ' + json.dumps(sorted(actual - expected)[:6]) +
            '. Iterate the `routes` argument instead of rediscovering pages; keep extra route candidates in '
            'extraction limitations, not the page catalog.')


def run_exploration_agent(category, repo_path=None, *, snapshot=None, routes=None, context=None, element_index=None,
                          route_candidates=None, model=None, agent_factory=None, max_discovery_rounds=None,
                          repair_rounds=None, handoff=None):
    if category not in REPORT_FILES:
        raise ValueError('Unknown exploration category: ' + category)
    if os.getenv('SEO_NO_LLM') == '1':
        raise RuntimeError('Agent exploration requires a model; unset SEO_NO_LLM or explicitly use SEO_EXPLORATION_MODE=static')
    max_discovery_rounds = bounded_int('SEO_MAX_DISCOVERY_ROUNDS', max_discovery_rounds, 12, 1, 100)
    repair_rounds = bounded_int('SEO_REPAIR_ROUNDS', repair_rounds, 4, 0, 50)
    max_tool_calls = bounded_int('SEO_MAX_TOOL_CALLS', None, 60, 1, 1000)
    snapshot = snapshot if snapshot is not None else snapshot_repository(repo_path)
    if not snapshot['files']:
        raise ValueError('No captured textual source; inspect inventory before generating an extractor')
    context = context if context is not None else build_context(snapshot)
    handoff = handoff or {}
    prompt, skill, common = system_prompt(category)
    script_routes = routes if routes is not None else list(route_candidates or [])
    lazy = {'index': element_index, 'compact': None}

    def get_index():
        if lazy['index'] is None:
            from seo_agents.repo_analysis.tools.parsing.element_index import build_element_index
            lazy['index'] = build_element_index(snapshot)
        return lazy['index']

    def get_compact():
        if lazy['compact'] is None:
            lazy['compact'] = compact_index(get_index())
        return lazy['compact']

    def uses_index(source):
        try:
            return entry_arity(validate_script(source)) == 4
        except (ValueError, SyntaxError, StopIteration):
            return False

    def check(report):
        if routes is not None:
            problem = route_mismatch(report, routes)
            if problem:
                raise ValueError(problem)
        if category == 'content':
            validate_content_references(report, {key: entry['report'] for key, entry in handoff.items()})

    def tester(source):
        """One bounded trial run for the draft REPL; never consumes an attempt."""
        try:
            index_arg = get_compact() if uses_index(source) else None
            report = execute_script(source, snapshot, routes=script_routes, index=index_arg)
            validate_observations(category, report, snapshot, index=lazy['index'])
            check(report)
        except (ValueError, SyntaxError, TypeError, KeyError, RecursionError) as error:
            metrics['test_script_failures'] = metrics.get('test_script_failures', 0) + 1
            return {'status': 'failed', 'error': str(error)[:2000]}
        metrics['test_script_passes'] = metrics.get('test_script_passes', 0) + 1
        counts = {field: sum(len(p[field]) for p in report['pages'] if isinstance(p.get(field), list))
                  for field in PAGE_FIELDS[category] if field not in {'word_count', 'depth', 'interpretation', 'inheritance_note'}}
        return {'status': 'passed', 'pages': len(report['pages']), 'observations': counts,
                'next': 'Submit ExtractionPlan (source may be empty to use extractor.py).'}

    inspected = set()
    draft = {DRAFT: ''}
    metrics = {'max_discovery_rounds': max_discovery_rounds, 'repair_rounds_per_attempt': repair_rounds,
               'max_tool_calls_per_attempt': max_tool_calls, 'discovery_model_calls': 0, 'repair_model_calls': 0,
               'finalization_model_calls': 0, 'discovery_budget_reached': False, 'tool_budget_reached': False}
    phase = {'attempt': 1, 'rounds': 0, 'limit': max_discovery_rounds, 'tool_start': 0}

    @wrap_model_call
    def bounded(request, handler):
        repairing = phase['attempt'] > 1
        tools_used = metrics.get('tool_calls', 0) - phase['tool_start']
        if phase['rounds'] < phase['limit'] and tools_used < max_tool_calls:
            phase['rounds'] += 1
            key = 'repair_model_calls' if repairing else 'discovery_model_calls'
            metrics[key] += 1
            label = ('repair ' if repairing else 'discovery ') + str(phase['rounds']) + '/' + str(phase['limit'])
        else:
            if tools_used >= max_tool_calls:
                metrics['tool_budget_reached'] = True
            if not repairing and not metrics['discovery_budget_reached']:
                metrics['discovery_budget_reached'] = True
                log('agent ' + category + ': discovery budget reached; requesting final ExtractionPlan')
            metrics['finalization_model_calls'] += 1
            # The structured-response tool remains available independently.
            request = request.override(tools=[])
            label = 'finalization ' + str(metrics['finalization_model_calls'])
        with progress('agent ' + category + ': model ' + label):
            return handler(request)

    def tool_progress(name, details):
        log('agent ' + category + ': completed ' + name + ' ' + json.dumps(details, ensure_ascii=True))

    tools = repository_tools(snapshot, inspected, context=context, routes=script_routes, metrics=metrics,
                             on_progress=tool_progress, handoff=handoff, element_index=element_index,
                             draft=draft, tester=tester)
    agent = (agent_factory or create_agent)(
        model=model if model is not None else get_chat_model(agent_model('legacy_extractor').tier),
        tools=tools, system_prompt=prompt, response_format=ToolStrategy(ExtractionPlan),
        middleware=[bounded], name='explore_' + category,
    )
    manifest = repository_summary(snapshot, context)
    manifest['verified_route_count'] = len(routes) if routes is not None else None
    if routes is None and route_candidates:
        manifest['route_candidates'] = {'count': len(route_candidates), 'preview': route_candidates[:40],
            'note': 'Deterministic file-convention/router candidates; verify against source. Your script receives them as `routes`.'}
    manifest['prior_work'] = handoff_summary(handoff)
    if handoff:
        manifest['tools'] += ', query_prior_report, read_prior_report'
        log('agent ' + category + ': relevant prior context available from ' + ', '.join(handoff))
    task = legacy_extractor_task(max_discovery_rounds, max_tool_calls, repair_rounds)
    messages = [{'role': 'user', 'content': task + json.dumps(manifest)}]
    errors, seen_messages, rejected = [], set(), {}
    last_plan, last_source = None, None
    metrics.update(model_calls=0, input_tokens=None, output_tokens=None, token_usage_available=False,
                   peak_recorded_conversation_bytes=0, per_call_tokens=[])
    begun = time.perf_counter()
    for attempt in range(1, 4):
        phase.update(attempt=attempt, rounds=0, limit=max_discovery_rounds if attempt == 1 else repair_rounds,
                     tool_start=metrics.get('tool_calls', 0))
        log('agent ' + category + ': generate/verify attempt ' + str(attempt))
        response = agent.invoke({'messages': messages}, config={'recursion_limit': 2 * (phase['limit'] + 4) + 8,
                                                               'max_concurrency': 1})
        response_messages = response.get('messages') or []
        for message in response_messages:
            if not isinstance(message, AIMessage):
                continue
            identifier = message.id or id(message)
            if identifier in seen_messages:
                continue
            seen_messages.add(identifier)
            metrics['model_calls'] += 1
            usage = message.usage_metadata or {}
            for key in ('input_tokens', 'output_tokens'):
                if key in usage:
                    metrics[key] = (metrics[key] or 0) + usage[key]
                    metrics['token_usage_available'] = True
            if usage:
                call = {'attempt': attempt, 'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
                        'cached_input_tokens': (usage.get('input_token_details') or {}).get('cache_read')}
                metrics['per_call_tokens'].append(call)
                log('agent ' + category + ': model call ' + str(metrics['model_calls']) + ' tokens ' + json.dumps(call))
        conversation = [m.model_dump() if hasattr(m, 'model_dump') else m for m in response_messages or messages]
        metrics['peak_recorded_conversation_bytes'] = max(metrics['peak_recorded_conversation_bytes'],
            len(prompt.encode()) + len(json.dumps(conversation, default=str, ensure_ascii=False).encode()))
        plan = response.get('structured_response')
        if not isinstance(plan, ExtractionPlan):
            final_message = next((m for m in reversed(response_messages) if isinstance(m, AIMessage)), None)
            diagnostic = {'structured_response_type': type(plan).__name__,
                'finish_reason': (final_message.response_metadata or {}).get('finish_reason') if final_message else None,
                'invalid_tool_calls': len(final_message.invalid_tool_calls) if final_message else 0,
                'output_tokens': (final_message.usage_metadata or {}).get('output_tokens') if final_message else None,
                'reasoning_tokens': (final_message.usage_metadata or {}).get('output_token_details', {}).get('reasoning') if final_message else None}
            metrics['last_response_diagnostic'] = diagnostic
            errors.append(legacy_extractor_missing_plan_error(diagnostic))
            log('agent ' + category + ': response rejected attempt ' + str(attempt) + ': ' + errors[-1])
            # No program to repair: continue the same conversation so gathered evidence is not lost.
            messages = list(response_messages or messages)
            messages.append({'role': 'user', 'content': legacy_extractor_no_plan_nudge(errors[-1])})
            continue
        last_plan = plan
        source = plan.source or draft[DRAFT]
        last_source = source or None
        try:
            if not inspected:
                raise ValueError('Read relevant repository source with read (or read_source) before submitting a script')
            if not source:
                raise ValueError('ExtractionPlan had no source and extractor.py is empty; write_file extractor.py first')
            if source in rejected:
                raise ValueError('Unchanged rejected script; change the code producing the invalid observations. ' + rejected[source])
            with progress('agent ' + category + ': verify and extract'):
                report = verify_and_extract(category, source, snapshot, routes=script_routes,
                                            index=get_index() if uses_index(source) else lazy['index'],
                                            compact=get_compact() if uses_index(source) else None)
                check(report)
        except (ValueError, SyntaxError, TypeError, KeyError, RecursionError) as error:
            detail = str(error)
            if isinstance(error, SyntaxError) and error.lineno:
                lines = source.splitlines()
                detail += '\nGenerated-source context: ' + json.dumps([
                    {'line': index + 1, 'code': lines[index][:200]}
                    for index in range(max(0, error.lineno - 2), min(len(lines), error.lineno + 1))])
            errors.append(detail[:2000])
            if inspected and source:
                rejected.setdefault(source, errors[-1])
            log('agent ' + category + ': verification rejected attempt ' + str(attempt) + ': ' + json.dumps(errors[-1]))
            draft[DRAFT] = source
            messages = [{'role': 'user', 'content': legacy_extractor_repair_brief(category, errors[-1], source, inspected, script_routes,
                                                                 handoff, repair_rounds)}]
            continue
        report['extraction'].update(framework=plan.framework,
                                    skill_sha256=hashlib.sha256((skill + common).encode()).hexdigest(),
                                    discovery_budget={'max_model_rounds': max_discovery_rounds,
                                                      'reached': metrics['discovery_budget_reached']})
        if metrics['discovery_budget_reached']:
            for page in report['pages']:
                page['coverage']['limitations'].append('Model discovery budget reached; remaining semantic investigation is unresolved. The extractor still receives the full captured snapshot.')
        metrics['wall_seconds'] = round(time.perf_counter() - begun, 4)
        return {'report': report, 'run': {'category': category, 'framework': plan.framework,
                'notes': plan.notes, 'script': source, 'script_from_draft': not plan.source, 'attempts': attempt,
                'verification': 'passed', 'inspected_files': sorted(inspected),
                'metrics': metrics, 'previous_errors': errors}}
    metrics['wall_seconds'] = round(time.perf_counter() - begun, 4)
    raise ExtractionFailed(category, {'category': category, 'framework': last_plan.framework if last_plan else None,
        'notes': last_plan.notes if last_plan else None, 'script': last_source,
        'attempts': 3, 'verification': 'failed',
        'inspected_files': sorted(inspected), 'metrics': metrics, 'previous_errors': errors})


def extract_category(category, repo_path=None):
    return run_exploration_agent(category, repo_path)['report']


class CategoryExtractorAgent:
    """Agent (LLM, legacy): writes and verifies a repository-specific extractor script for one SEO category.

    category        one of urls, page_structure, internal_links, media, content, metadata, eeat
    model_settings  lib/models.py AGENT_MODELS['legacy_extractor']
    tools           lib/tool_mapping.py LEGACY_EXTRACTOR_TOOLS (LangChain tools, in prompt order)
    prompt          lib/prompts/legacy_extractors/<category>/SKILL.md + contract-common.md
    """

    def __init__(self, category):
        self.category = category

    @property
    def model_settings(self):
        return agent_model('legacy_extractor')

    @property
    def tools(self):
        return LEGACY_EXTRACTOR_TOOLS

    def system_prompt(self):
        return system_prompt(self.category)[0]

    def run(self, **options):
        """{'report': ..., 'run': ...}; raises ExtractionFailed after three rejected attempts.

        Options are those of `run_exploration_agent` (repo_path, snapshot, routes, context, element_index, ...)."""
        return run_exploration_agent(self.category, **options)
