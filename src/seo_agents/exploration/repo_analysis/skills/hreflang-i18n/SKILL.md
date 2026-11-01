---
name: explore-hreflang-i18n
description: Find locale routing, translation libraries, hreflang alternates and document language.
---

Multilingual SEO needs a URL per language, `<html lang>`, reciprocal hreflang
alternates (plus `x-default`) and translated metadata. Locale configuration
appears in framework config (`i18n:`, `locales`, `[languages]`), i18n libraries,
locale route segments (`[locale]`, `[lang]`, `/en/`) and translation files.

```json profile
{
  "name": "hreflang-i18n",
  "signals": [
    {"id": "i18n-config", "label": "Locale configuration", "regex": "\\bi18n\\s*:\\s*\\{|\\blocales\\s*:\\s*\\[|defaultLocale\\s*:|^\\[languages\\]|LANGUAGES\\s*=|config\\.i18n\\.|available_locales", "block": true},
    {"id": "i18n-library", "label": "i18n library usage", "regex": "from\\s+['\"](?:next-intl|next-i18next|react-i18next|i18next|vue-i18n|@nuxtjs/i18n|svelte-i18n|@angular/localize|@ngx-translate/core|astro-i18next|paraglide[\\w/-]*|@lingui/[\\w-]+|react-intl)['\"]|\\bgettext\\s*\\(|\\b_\\(\\s*['\"]|\\bI18n\\.t\\s*\\(|__\\(\\s*['\"]"},
    {"id": "hreflang", "label": "hreflang alternate", "regex": "hreflang|hrefLang|alternates\\s*:\\s*\\{[^}]*languages|x-default", "block": false},
    {"id": "html-lang", "label": "Document language attribute", "regex": "<html[^>]*\\blang\\s*=\\s*['\"{]?([\\w-]*)", "value_group": 1},
    {"id": "locale-segment", "label": "Locale route segment", "regex": "(?:^|/)(?:\\[(?:locale|lang|lng)\\]|\\[\\[\\.\\.\\.(?:locale|lang)\\]\\]|:(?:locale|lang))(?:/|$)", "files_only": true},
    {"id": "translation-files", "label": "Translation resource files", "regex": "(?:^|/)(?:locales?|i18n|lang|translations|messages)/[\\w-]+(?:/[\\w.-]+)?\\.(?:json|ya?ml|po|ts|js)$", "files_only": true}
  ],
  "gaps": [
    {"id": "no-html-lang", "label": "No <html lang> found", "when_none_of": ["html-lang"]},
    {"id": "no-hreflang", "label": "Locales configured but no hreflang alternates found", "when_none_of": ["hreflang"], "only_if_any": ["i18n-config", "locale-segment", "translation-files"]}
  ],
  "edit_guidance": "Set <html lang> from the active locale in the root layout; emit reciprocal hreflang links (and x-default) in the shared head or sitemap from the locale config; translate titles/descriptions per locale."
}
```
