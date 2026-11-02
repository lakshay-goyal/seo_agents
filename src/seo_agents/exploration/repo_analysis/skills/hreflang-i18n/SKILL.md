---
name: explore-hreflang-i18n
description: Find locale routing, translation libraries, hreflang alternates and document language.
---

Multilingual SEO needs a URL per language, `<html lang>`, reciprocal hreflang
alternates (plus `x-default`) and translated metadata. Locale configuration
appears in framework config (`i18n:`, `locales`, `[languages]`), i18n libraries,
locale route segments (`[locale]`, `[lang]`, `/en/`) and translation files.

Runtime profile: [`profiles/hreflang-i18n.json`](../../profiles/hreflang-i18n.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
