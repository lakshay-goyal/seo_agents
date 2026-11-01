---
name: explore-ai-search-readiness
description: Find signals that help AI search/answer engines (GEO/AEO) - llms.txt, AI crawler rules, FAQ/Q&A, extractable semantic structure.
---

AI answer engines favour pages they may crawl and can quote: allow/deny rules for
AI user agents in robots, an `llms.txt` summary, FAQ/HowTo/QAPage content and
schema, question-style headings, concise definitions/TL;DR blocks, lists and
tables, semantic landmarks (`<main>`, `<article>`), and dated, attributed content.

```json profile
{
  "name": "ai-search-readiness",
  "signals": [
    {"id": "llms-txt", "label": "llms.txt / llms-full.txt", "regex": "(?:^|/)llms(?:-full)?\\.txt$", "files_only": true},
    {"id": "ai-crawler-rule", "label": "AI crawler rule", "regex": "GPTBot|ChatGPT-User|OAI-SearchBot|ClaudeBot|Claude-Web|anthropic-ai|PerplexityBot|Google-Extended|CCBot|Bytespider|Applebot-Extended|Amazonbot|meta-externalagent", "ignore_case": true},
    {"id": "faq-schema", "label": "FAQ/HowTo/QA schema", "regex": "['\"]@type['\"]\\s*:\\s*['\"](FAQPage|HowTo|QAPage|Question)['\"]", "value_group": 1},
    {"id": "faq-section", "label": "FAQ section", "regex": "\\bFAQs?\\b|frequently\\s+asked|<details\\b|accordion", "ignore_case": true},
    {"id": "question-heading", "label": "Question-style heading", "regex": "<h[2-4][^>]*>\\s*(?:what|how|why|when|where|who|which|can|does|is|are|should)\\b[^<]{3,}\\?\\s*</h[2-4]>|^#{2,4}\\s+(?:What|How|Why|When|Where|Who|Which|Can|Does|Is|Are|Should)\\b.*\\?\\s*$", "ignore_case": true},
    {"id": "summary-block", "label": "Summary/TL;DR/key takeaways block", "regex": "TL;?DR|key\\s+takeaways|in\\s+summary|at\\s+a\\s+glance", "ignore_case": true},
    {"id": "semantic-landmark", "label": "Semantic landmark element", "regex": "<(?:main|article|section|aside|nav|header|footer)\\b"},
    {"id": "list-table", "label": "Lists/tables (extractable structure)", "regex": "<(?:ol|ul|table|dl)\\b|^\\s*\\|.+\\|\\s*$"},
    {"id": "dates", "label": "Published/updated dates", "regex": "datePublished|dateModified|article:published_time|article:modified_time|<time\\b|^(?:date|updated|lastmod|published):", "ignore_case": true}
  ],
  "gaps": [
    {"id": "no-llms-txt", "label": "No llms.txt found", "when_none_of": ["llms-txt"]},
    {"id": "no-ai-crawler-policy", "label": "No explicit AI crawler policy in robots", "when_none_of": ["ai-crawler-rule"]},
    {"id": "no-faq-schema", "label": "FAQ content found but no FAQPage schema", "when_none_of": ["faq-schema"], "only_if_any": ["faq-section", "question-heading"]}
  ],
  "edit_guidance": "Add /llms.txt summarising key pages; decide AI crawler access explicitly in robots; phrase key H2s as questions with a direct 1-2 sentence answer below; add FAQPage schema where FAQ content exists; wrap primary content in <main>/<article>; show published/updated dates."
}
```
