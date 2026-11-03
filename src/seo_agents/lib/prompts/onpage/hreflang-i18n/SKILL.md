---
name: hreflang-i18n
description: hreflang annotations (valid codes, self-reference, x-default, absolute hrefs, duplicates, canonical alignment, return tags) and the <html lang> value. Deterministic.
kind: deterministic
order: 17
---
## Checks (`checks.hreflang_i18n_check.check_hreflang_i18n`)
Valid hreflang codes: ISO 639-1 (2-letter) language, optional script, optional ISO 3166-1 region or UN M.49 number (`en`, `en-GB`, `zh-Hant-TW`, `es-419`) or `x-default`. `<html lang>` may also use a 2-3 letter language.
A missing `<html lang>` is owned by `technical` (`technical.lang`).

| Check | Pass condition | Output |
| --- | --- | --- |
| code | valid; not en_US (underscore), en-UK (-> en-GB), gb/us (-> en-GB/en-US), jp/cn/kr, 3-letter eng (-> en) | change `technical.hreflang` (`set_attr`) when the entry has an edit and a fix is known, else advice |
| `uk` | `uk` is Ukrainian | advice medium when the site is English (`<html lang="en*">`, or another entry is English) |
| duplicates | each code used once | advice |
| absolute hrefs | every href is an absolute URL | advice |
| self reference | the set includes this page | advice |
| x-default | present when more than one language | advice low |
| canonical | the self hreflang URL equals the canonical | advice |
| return tags | no site-level return-tag issue for this page | advice high |
| html lang value | valid BCP 47 (no underscore, no 3-letter code with a 2-letter equivalent, no country code as language) | change `technical.lang_value` (`set_attr` on `<html lang>`) |
| lang agreement | `<html lang>` matches Content-Language and the page's own hreflang | advice |
