"""Every system prompt / skill text sent to a model lives under this folder.

    legacy_extractors/  contract-common.md + one SKILL.md per legacy LLM category extractor
    gap_workers/        one SKILL.md per gap-worker task (attribute-value, explain-computed, ...)
    onpage/             _principles.md + one SKILL.md per on-page checklist item
    system_prompts.py   prompts that are built in code (role lines, repair / validator feedback turns)
"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent
LEGACY_EXTRACTOR_PROMPTS_DIR = PROMPTS_DIR / 'legacy_extractors'
GAP_WORKER_PROMPTS_DIR = PROMPTS_DIR / 'gap_workers'
ONPAGE_PROMPTS_DIR = PROMPTS_DIR / 'onpage'
