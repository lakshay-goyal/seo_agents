---
name: explore-hreflang-i18n
description: Find locale routing, translation libraries, hreflang alternates and document language.
---

Multilingual SEO needs a URL per language, `<html lang>`, reciprocal hreflang alternates (plus
`x-default`) and translated metadata. `html_lang` keeps literal values; `lang={expr}` and template
expressions are `value_origin: expression` (case-insensitive, so `<Html lang>` in `_document` counts).
`i18n_config` reads `{locales, default, strategy}` from `next.config` `i18n`, Nuxt/Astro `i18n`,
next-intl `defineRouting`, paraglide settings and Hugo `[languages]`. Locale route segments
(`[locale]`, `[lang]`) and translation files are recorded too.

Runtime profile: [`profiles/hreflang-i18n.json`](../../profiles/hreflang-i18n.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/hreflang_i18n.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
