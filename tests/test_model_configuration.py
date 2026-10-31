"""Request budgets must reach the model and container without a provider call."""

import os
import unittest
from unittest.mock import patch

from seo_agents.exploration.checks.graph import extraction_identity
from seo_agents.exploration.checks.tools.llm import get_chat_model
from seo_agents.main import forward_env


class ModelConfigurationTests(unittest.TestCase):
    def test_flash_defaults_disable_reasoning_to_leave_room_for_structured_output(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}, clear=True), patch('langchain_openai.ChatOpenAI') as constructor:
            get_chat_model()
        self.assertEqual(constructor.call_args.kwargs['max_tokens'], 32768)
        self.assertEqual(constructor.call_args.kwargs['extra_body'], {'reasoning': {'effort': 'none'}})

    def test_explicit_settings_override_defaults(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', 'OPENROUTER_MAX_TOKENS': '24000',
                                    'OPENROUTER_REASONING_EFFORT': 'medium'}, clear=True), patch('langchain_openai.ChatOpenAI') as constructor:
            get_chat_model()
        self.assertEqual(constructor.call_args.kwargs['max_tokens'], 24000)
        self.assertEqual(constructor.call_args.kwargs['extra_body'], {'reasoning': {'effort': 'medium'}})

    def test_other_models_and_explicit_default_keep_provider_reasoning_defaults(self):
        for settings in [{'OPENROUTER_MODEL': 'other/model'}, {'OPENROUTER_REASONING_EFFORT': 'default'}]:
            with self.subTest(settings=settings), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', **settings}, clear=True), patch('langchain_openai.ChatOpenAI') as constructor:
                get_chat_model()
                self.assertNotIn('extra_body', constructor.call_args.kwargs)

    def test_invalid_budgets_are_rejected_before_client_creation(self):
        for settings in [{'OPENROUTER_MAX_TOKENS': value} for value in ('0', '-1', 'lots')] + [{'OPENROUTER_REASONING_EFFORT': 'unlimited'}]:
            with self.subTest(settings=settings), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test', **settings}, clear=True), patch('langchain_openai.ChatOpenAI') as constructor:
                with self.assertRaises(ValueError):
                    get_chat_model()
                constructor.assert_not_called()

    def test_settings_forward_to_container_and_invalidate_resume_identity(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'test', 'OPENROUTER_MAX_TOKENS': '16384',
                                    'OPENROUTER_REASONING_EFFORT': 'low', 'SEO_MAX_DISCOVERY_ROUNDS': '12'}, clear=True):
            self.assertEqual(forward_env()['OPENROUTER_MAX_TOKENS'], '16384')
            self.assertEqual(forward_env()['OPENROUTER_REASONING_EFFORT'], 'low')
            self.assertEqual(forward_env()['SEO_MAX_DISCOVERY_ROUNDS'], '12')
            original = extraction_identity('agent')
            with patch.dict(os.environ, {'OPENROUTER_REASONING_EFFORT': 'medium'}):
                self.assertNotEqual(extraction_identity('agent'), original)
            with patch.dict(os.environ, {'OPENROUTER_MAX_TOKENS': '24000'}):
                self.assertNotEqual(extraction_identity('agent'), original)
            with patch.dict(os.environ, {'SEO_MAX_DISCOVERY_ROUNDS': '20'}):
                self.assertNotEqual(extraction_identity('agent'), original)


if __name__ == '__main__':
    unittest.main()
