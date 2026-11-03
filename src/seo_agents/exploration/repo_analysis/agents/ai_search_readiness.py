"""llms.txt, AI crawler policy, FAQ/Q&A structure and extractable content (GEO/AEO).

AI-crawler policy comes from parsed robots groups (allow / disallow / partial per bot), not from any
mention of a bot name. Landmarks and lists/tables are per-file counts, not one record per element.
"""

import re

from seo_agents.exploration.repo_analysis.agents.sitemap_robots import parse_robots, robots_policy_for
from seo_agents.exploration.repo_analysis.tools.skill_runner import evaluate_gaps, run_skill

AI_CRAWLERS = ('GPTBot', 'ChatGPT-User', 'OAI-SearchBot', 'ClaudeBot', 'Claude-Web', 'anthropic-ai', 'PerplexityBot',
               'Google-Extended', 'CCBot', 'Bytespider', 'Applebot-Extended', 'Amazonbot', 'meta-externalagent')
LANDMARK = re.compile(r'<(main|article|section|aside|nav|header|footer)\b')
LIST_TABLE = re.compile(r'<(?:ol|ul|table|dl)\b|^\s*\|.+\|\s*$', re.M)
FAQ_TYPES = {'FAQPage', 'HowTo', 'QAPage', 'Question'}


def structure_counts(index):
    rows = {}
    for path in index.templates():
        text = index.files[path]
        landmarks = {}
        for match in LANDMARK.finditer(text):
            landmarks[match.group(1)] = landmarks.get(match.group(1), 0) + 1
        lists = len(LIST_TABLE.findall(text))
        if landmarks or lists:
            rows[path] = {**({'landmarks': landmarks} if landmarks else {}), **({'lists_tables': lists} if lists else {})}
    return rows


def ai_crawler_policy(index, sitemap_robots=None):
    robots = (sitemap_robots or {}).get('robots_files')
    if robots is None:
        robots = [{'file': p, **parse_robots(index.files[p])} for p in index.paths('robots.txt', '*/robots.txt')]
    policy = []
    for entry in robots:
        named = {u.lower() for g in entry['groups'] for u in g['user_agents']}
        for bot in AI_CRAWLERS:
            decision = robots_policy_for(entry, bot)
            if bot.lower() in named or decision is not None:
                policy.append({'bot': bot, 'policy': decision, 'explicit': bot.lower() in named, 'file': entry['file']})
    return policy


def analyze_ai_search_readiness(index, structured=None, sitemap_robots=None):
    result = run_skill(index, 'ai-search-readiness')
    result['structure_by_file'] = structure_counts(index)
    result['ai_crawler_policy'] = ai_crawler_policy(index, sitemap_robots)
    extra = []
    if any(p['explicit'] for p in result['ai_crawler_policy']):
        extra.append('ai-crawler-rule')
    else:
        # A bot name mentioned anywhere (UA lists, analytics) is not a crawler policy.
        result['found_signals'] = [s for s in result['found_signals'] if s != 'ai-crawler-rule']
    if structured is not None and set(structured.get('schema_types') or {}) & FAQ_TYPES:
        extra.append('faq-schema')
    return evaluate_gaps(result, extra)
