"""Model tiering, reasoning effort, prompt caching, structured outputs, error classes and cost accounting."""

import os
import unittest
from unittest.mock import patch

from seo_agents import llm


class TierTests(unittest.TestCase):
    def test_tier_models_come_from_env_with_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm.model_for_tier('haiku'), 'anthropic/claude-haiku-5.5')
            self.assertEqual(llm.model_for_tier('mid'), 'anthropic/claude-sonnet-5.5')
            with self.assertRaises(ValueError):
                llm.model_for_tier('large')
            self.assertEqual(llm.model_for_tier('large', allow_large=True), 'anthropic/claude-opus-5.5')
        with patch.dict(os.environ, {'OPENROUTER_HAIKU_MODEL': 'x/h', 'OPENROUTER_MID_MODEL': 'x/m'}, clear=True):
            self.assertEqual((llm.model_for_tier('haiku'), llm.model_for_tier('mid')), ('x/h', 'x/m'))

    def test_skill_tier_and_ladder(self):
        self.assertEqual(llm.tier_for_skill({'tier': 'haiku'}), 'haiku')
        self.assertEqual(llm.tier_for_skill({'tier': 'large'}, 'small'), 'small')
        self.assertEqual(llm.tier_for_skill({}, 'mid'), 'mid')
        self.assertEqual(llm.ladder_for_skill({'tier': 'small'}), ['small', 'small', 'mid'])
        self.assertEqual(llm.ladder_for_skill({'ladder': 'small, haiku, mid'}), ['small', 'haiku', 'mid'])

    def test_global_none_effort_never_weakens_mid_or_escalation(self):
        with patch.dict(os.environ, {'OPENROUTER_REASONING_EFFORT': 'none'}, clear=True):
            self.assertEqual(llm.reasoning_effort_for('mid', 'anthropic/claude-sonnet-5.5'), 'default')
            self.assertEqual(llm.reasoning_effort_for('small', llm.DEFAULT_MODEL), 'none')
            self.assertEqual(llm.reasoning_effort_for('haiku', 'h', escalation=True), 'low')
        with patch.dict(os.environ, {'OPENROUTER_MID_REASONING_EFFORT': 'high'}, clear=True):
            self.assertEqual(llm.reasoning_effort_for('mid', 'm'), 'high')

    def test_mid_model_gets_reasoning_despite_global_none(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k', 'OPENROUTER_REASONING_EFFORT': 'none'}, clear=True), \
                patch('langchain_openai.ChatOpenAI') as constructor:
            llm.get_chat_model('mid')
        self.assertNotIn('extra_body', constructor.call_args.kwargs)
        self.assertEqual(constructor.call_args.kwargs['model'], 'anthropic/claude-sonnet-5.5')


class CachingAndSchemaTests(unittest.TestCase):
    def test_cache_control_only_for_providers_that_need_it(self):
        cached = llm.cached_system_message('rules', 'anthropic/claude-haiku-5.5')
        self.assertEqual(cached.content[0]['cache_control'], {'type': 'ephemeral'})
        plain = llm.cached_system_message('rules', 'deepseek/deepseek-v4.1-flash')
        self.assertEqual(plain.content, 'rules')

    def test_with_json_schema_binds_response_format_and_tolerates_fakes(self):
        class Bindable:
            def bind(self, **kwargs):
                self.kwargs = kwargs
                return 'bound'
        model = Bindable()
        self.assertEqual(llm.with_json_schema(model, {'type': 'object'}, 'answer'), 'bound')
        self.assertEqual(model.kwargs['response_format']['type'], 'json_schema')
        fake = object()
        self.assertIs(llm.with_json_schema(fake, {}, 'x'), fake)


class ErrorTests(unittest.TestCase):
    def test_classification(self):
        class Status(Exception):
            def __init__(self, code):
                super().__init__('boom')
                self.status_code = code
        self.assertEqual(llm.classify_error(Status(401)), 'auth')
        self.assertEqual(llm.classify_error(Status(402)), 'auth')
        self.assertEqual(llm.classify_error(Status(429)), 'transport')
        self.assertEqual(llm.classify_error(Status(503)), 'transport')
        self.assertEqual(llm.classify_error(TimeoutError('read timed out')), 'transport')
        self.assertEqual(llm.classify_error(RuntimeError('Insufficient credits')), 'auth')
        self.assertIsNone(llm.classify_error(ValueError('candidate must be one of c1 (timeout)')))

    def test_backoff_retries_transport_and_fails_fast_on_auth(self):
        calls, sleeps = [], []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError('connection reset')
            return 'ok'
        self.assertEqual(llm.call_with_backoff(flaky, sleep=sleeps.append), 'ok')
        self.assertEqual(sleeps, [1.0, 2.0])

        def denied():
            calls.append(1)
            raise RuntimeError('OPENROUTER_API_KEY not set')
        before = len(calls)
        with self.assertRaises(llm.AuthError):
            llm.call_with_backoff(denied, sleep=sleeps.append)
        self.assertEqual(len(calls) - before, 1)


class CostTests(unittest.TestCase):
    def setUp(self):
        llm.reset_usage()

    def test_anthropic_cache_read_and_write_multipliers(self):
        cost = llm.call_cost('anthropic/claude-sonnet-5.5', 1_000_000, 0, cached=500_000, written=100_000)
        self.assertAlmostEqual(cost, 400_000 * 3 / 1e6 + 500_000 * 0.3 / 1e6 + 100_000 * 3.75 / 1e6)

    def test_usage_scope_and_reset(self):
        with patch.dict(os.environ, {}, clear=True):
            llm.record_usage('anthropic/claude-haiku-5.5', 'haiku', 't', {'input_tokens': 100, 'output_tokens': 10})
            with llm.usage_scope() as scope:
                llm.record_usage('anthropic/claude-haiku-5.5', 'haiku', 't', {'input_tokens': 50, 'output_tokens': 5,
                                                                              'input_token_details': {'cache_read': 40}})
            self.assertEqual(scope['usage']['calls'], 1)
            self.assertEqual(scope['usage']['cached_input_tokens'], 40)
            self.assertEqual(llm.usage_summary()['calls'], 2)
            llm.reset_usage()
            self.assertEqual(llm.usage_summary()['calls'], 0)


if __name__ == '__main__':
    unittest.main()
