"""Gap workers: code-derived edit kinds, honest unknowns, grounded queries, retry/escalation policy, caches."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from seo_agents.repo_analysis.agents.gap_workers import (gap_answer_validation, gap_result_applier, gap_task_builder, gap_task_prompts, gap_task_solver, route_adapter_learner)


def scripted(answers, calls):
    """Factory returning fakes that replay `answers` (dict, str or Exception) in order."""
    def factory(tier, temperature=0, label=None):
        class Fake:
            def invoke(self, messages):
                calls.append((tier, messages))
                answer = answers[min(len(calls) - 1, len(answers) - 1)]
                if isinstance(answer, Exception):
                    raise answer
                return SimpleNamespace(content=answer if isinstance(answer, str) else json.dumps(answer))
        return Fake()
    return factory


def attr_task():
    return {'task': 'attribute_value', 'page_id': 'p1', 'field': '/links/out/0',
            'packet': {'field': '/links/out/0', 'observed': 'About',
                       'candidates': [{'id': 'c1', 'kind': 'literal', 'file': 'src/App.jsx', 'line': 280},
                                      {'id': 'c2', 'kind': 'json', 'file': 'nav.json', 'line': 3, 'pointer': '/1/label'}]},
            '_candidates': [{'id': 'c1', 'kind': 'literal', 'file': 'src/App.jsx', 'line': 280},
                            {'id': 'c2', 'kind': 'json', 'file': 'nav.json', 'line': 3, 'pointer': '/1/label', 'in_loop': True}]}


class GapPolicyTests(unittest.TestCase):
    def setUp(self):
        gap_task_solver.reset_auth_failure()

    def test_edit_kind_comes_from_candidate_not_model(self):
        calls = []
        task = attr_task()
        result = gap_task_solver.solve(task, 'react', model_factory=scripted([{'candidate': 'c2', 'edit_kind': 'literal', 'reason': 'x'}], calls))
        page = {'links': {'out': [{'anchor': 'About', 'edit': None}]}}
        self.assertTrue(gap_result_applier.apply(task, result, page, None))
        edit = page['links']['out'][0]['edit']
        self.assertEqual((edit['kind'], edit['pointer'], edit['in_loop'], edit['verify']), ('json', '/1/label', True, 'grounded'))
        system = calls[0][1][0].content
        self.assertNotIn('edit_kind', system if isinstance(system, str) else system[0]['text'])

    def test_escalates_to_mid_only_after_two_validation_failures(self):
        calls = []
        bad = {'candidate': 'c9', 'reason': ''}
        result = gap_task_solver.solve(attr_task(), None, model_factory=scripted([bad, bad, {'candidate': 'c1', 'reason': 'ok'}], calls))
        self.assertEqual([c[0] for c in calls], ['small', 'small', 'mid'])
        self.assertEqual(result['tier'], 'mid')
        self.assertIn('rejected by the validator', calls[1][1][1].content)

    def test_auth_failure_fails_fast_without_ladder_burn_and_stops_later_tasks(self):
        calls = []
        error = RuntimeError('401 Unauthorized: invalid api key')
        result = gap_task_solver.solve(attr_task(), None, model_factory=scripted([error], calls))
        self.assertEqual((result['status'], result['failure'], len(calls)), ('failed', 'auth', 1))
        later = gap_task_solver.solve(attr_task(), None, model_factory=scripted([{'candidate': 'c1'}], calls))
        self.assertEqual((later['status'], len(calls)), ('failed', 1))

    def test_explain_computed_none_keeps_real_location_and_flags_gap(self):
        task = {'task': 'explain_computed', 'page_id': 'p', 'field': '/head/title',
                'packet': {'lines': [{'id': 'L5', 'file': 'app/page.tsx', 'line': 5, 'code': 'title: post.title'}]}}
        page = {'head': {'title': {'value': 'Hi', 'edit': {'kind': 'computed', 'file': 'app/page.tsx', 'line': 5}}}}
        answer = gap_answer_validation.validate(task, {'line_id': 'none', 'origin': 'unknown', 'reason': 'not shown'})
        self.assertFalse(gap_result_applier.apply(task, {'answer': answer, 'tier': 'small'}, page, None))
        self.assertEqual(page['head']['title']['edit'], {'kind': 'computed', 'file': 'app/page.tsx', 'line': 5})
        self.assertEqual(page['head']['title']['gap'], 'computed-unexplained')

    def test_explain_computed_kind_is_derived_from_line(self):
        self.assertEqual(gap_result_applier.derive_kind('src/data/posts.json', '"title": "Hi"'), 'json')
        self.assertEqual(gap_result_applier.derive_kind('locales/en.json', '"t": "Hi"'), 'i18n')
        self.assertEqual(gap_result_applier.derive_kind('lib/posts.ts', 'const posts = [{ title: "Hello World" }]'), 'literal')
        self.assertEqual(gap_result_applier.derive_kind('lib/posts.ts', 'return data.title'), 'computed')

    def test_topic_queries_must_be_grounded_in_page_text(self):
        task = {'task': 'topic_intent', 'packet': {'url': '/', 'title': 'Lakshay Goyal — Software Engineer', 'headings': ['Projects'], 'copy': ''}}
        with self.assertRaisesRegex(ValueError, 'not found in the page text'):
            gap_answer_validation.validate(task, {'primary_topic': 'software engineer portfolio', 'intent': 'navigational',
                                        'candidate_queries': ['hire react developer']})
        answer = gap_answer_validation.validate(task, {'primary_topic': 'lakshay goyal engineer', 'intent': 'navigational',
                                             'candidate_queries': ['Lakshay Goyal', 'lakshay goyal projects']})
        self.assertEqual(answer['candidate_queries'], ['lakshay goyal', 'lakshay goyal projects'])
        self.assertEqual(gap_answer_validation.validate(task, {'primary_topic': '', 'intent': 'unknown', 'candidate_queries': []})['intent'], 'unknown')

    def test_votes_measure_enum_agreement_and_stop_early(self):
        calls = []
        task = {'task': 'topic_intent', 'page_id': 'p', 'packet': {'url': '/pricing', 'title': 'Acme pricing', 'headings': [], 'copy': ''}}
        vote = {'primary_topic': 'acme pricing', 'intent': 'commercial', 'candidate_queries': ['acme pricing'], 'reason': ''}
        result = gap_task_solver.solve(task, None, model_factory=scripted([vote, vote, vote], calls))
        self.assertEqual((result['votes'], result['agreement'], len(calls)), (2, 1.0, 2))
        merged, agreement = gap_task_solver.majority([{'intent': 'local', 'primary_topic': 'a b'}, {'intent': 'local', 'primary_topic': 'c d'},
                                                  {'intent': 'commercial', 'primary_topic': 'e f'}])
        self.assertEqual((merged['intent'], merged['primary_topic'], agreement), ('local', 'a b', 0.67))

    def test_skill_limits_come_from_validator_constants(self):
        body, meta = gap_task_prompts.load_skill('topic-intent', 'react')
        self.assertNotIn('{{', body)
        self.assertIn(f"{gap_task_prompts.LIMITS['QUERY_MIN_WORDS']}-{gap_task_prompts.LIMITS['QUERY_MAX_WORDS']} words", body)
        body, _ = gap_task_prompts.load_skill('attribute-value', 'react')
        self.assertIn('### jsx positive', body)
        self.assertIn('### jsx negative', body)
        self.assertNotIn('### php', body)

    def test_cache_key_changes_with_snapshot_and_code_version(self):
        task = attr_task()
        task['scope'] = {'repo': 'a/b', 'snapshot': '1'}
        key = gap_task_solver.cache_key(task, 'skill')
        task['scope'] = {'repo': 'a/b', 'snapshot': '2'}
        self.assertNotEqual(key, gap_task_solver.cache_key(task, 'skill'))
        with patch.object(gap_task_solver, 'GAP_CODE_VERSION', 'other'):
            self.assertNotEqual(gap_task_solver.cache_key(task, 'skill'), key)

    def test_enumerate_instances_returns_suggested_urls(self):
        calls = []
        groups = [{'id': 'g1', 'file': 'posts.json', 'pointer_pattern': '/*/slug', 'sample_values': ['a'], 'values': ['a', 'b']}]
        task = {'task': 'enumerate_instances', 'page_id': 'p', 'packet': {'route': '/blog/:slug', 'template': 't', 'imports': [],
                                                                         'groups': [{k: groups[0][k] for k in ('id', 'file', 'pointer_pattern', 'sample_values')}]},
                '_groups': groups}
        result = gap_task_solver.solve(task, None, model_factory=scripted([{'group': 'g1', 'reason': ''}], calls))
        self.assertEqual(result['instances_suggested'], ['/blog/a', '/blog/b'])


class RouteAdapterTests(unittest.TestCase):
    def setUp(self):
        gap_task_solver.reset_auth_failure()

    def test_adapter_ladder_heads_and_per_snapshot_cache(self):
        files = {'site/pages/home.tpl': '{extends "layout.tpl"}\n<h1>Home</h1>', 'site/partials/nav.tpl': '<nav></nav>'}
        rules = {'framework': 'tpl', 'rules': [{'path_regex': r'^site/pages/(?P<route>.+)\.tpl$', 'url_template': '/{route}',
                                                'index_names': ['home'], 'dynamic_syntax': 'none'}], 'reason': ''}
        calls = []
        with tempfile.TemporaryDirectory() as temp, patch.object(route_adapter_learner, 'CACHE_DIR', Path(temp)):
            result, origin = route_adapter_learner.learn_adapter({'files': files, 'sha256': 's1'}, {'candidates': []},
                                                       model_factory=scripted([{'rules': 'bad'}, rules], calls))
            self.assertEqual(origin, 'haiku')
            self.assertEqual([c[0] for c in calls], ['small', 'haiku'])
            packet = json.loads(calls[0][1][1].content)
            self.assertIn('{extends', packet['heads']['site/pages/home.tpl'])
            cached = list(Path(temp).glob('*.json'))
            self.assertEqual(len(cached), 1)
            _, origin = route_adapter_learner.learn_adapter({'files': files, 'sha256': 's2'}, {'candidates': []},
                                                  model_factory=scripted([rules], calls))
            self.assertNotEqual(origin, 'cache')  # another snapshot never reuses repo A's rules

    def test_honest_unknown_adapter(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp, patch.object(route_adapter_learner, 'CACHE_DIR', Path(temp)):
            rules, origin = route_adapter_learner.learn_adapter({'files': {'a/x.tpl': ''}, 'sha256': 's'}, {'candidates': []},
                                                      model_factory=scripted([{'framework': 'unknown', 'rules': [], 'reason': ''}], calls))
        self.assertEqual((rules, origin, len(calls)), (None, 'unknown', 1))


class HelperContextTests(unittest.TestCase):
    def test_explain_computed_packet_includes_imported_helper_body(self):
        files = {'app/page.tsx': 'import { getPost } from "../lib/posts";\nexport async function generateMetadata({params}) {\n  const post = getPost(params.slug);\n  return { title: post.title };\n}',
                 'lib/posts.ts': 'const posts = [{ slug: "hello", title: "Hello World" }];\nexport function getPost(slug) {\n  return posts.find(p => p.slug === slug);\n}'}
        ctx = SimpleNamespace(files=files, imports={'app/page.tsx': {'getPost': 'lib/posts.ts'}}, index={'refs': [], 'data': []})
        page = {'id': 'p', 'url': '/blog/hello', 'template': 'app/page.tsx', 'kind': 'page', 'instance_status': 'template', 'eeat': [],
                'head': {'title': {'value': 'Hello World', 'edit': {'kind': 'computed', 'file': 'app/page.tsx', 'line': 4, 'via': 'generateMetadata'}},
                         'description': {'value': None, 'edit': None}}, 'headings': [], 'content': {'blocks': []}}
        with patch.dict(os.environ, {'SEO_TOPIC_MAX_PAGES': '0', 'SEO_EEAT_MAX_PAGES': '0'}):
            tasks = gap_task_builder.build_tasks([], [page], ctx, {})
        lines = tasks[0]['packet']['lines']
        self.assertTrue(any(l['file'] == 'lib/posts.ts' and 'getPost' in l['code'] for l in lines))
        self.assertTrue(all(t['scope']['snapshot'] for t in tasks))


if __name__ == '__main__':
    unittest.main()
