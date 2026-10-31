"""Shared OpenRouter configuration and optional summaries of crawl statistics."""

import json
import os

from seo_agents.log import log


def get_chat_model():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    from langchain_openai import ChatOpenAI
    model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    try:
        max_tokens = int(os.getenv('OPENROUTER_MAX_TOKENS', '32768'))
    except ValueError as error:
        raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer') from error
    if max_tokens < 1:
        raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer')
    # OpenRouter counts reasoning and visible output against the same budget.
    # Flash can spend the entire cap on thinking even at low effort, leaving no plan.
    effort = os.getenv('OPENROUTER_REASONING_EFFORT',
                       'none' if model == 'deepseek/deepseek-v4.1-flash' else 'default')
    if effort not in {'default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}:
        raise ValueError('Invalid OPENROUTER_REASONING_EFFORT')
    options = {} if effort == 'default' else {'extra_body': {'reasoning': {'effort': effort}}}
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0,
                      timeout=90, max_retries=2, max_tokens=max_tokens, **options)


def summarize(check_name, stats):
    if os.getenv("SEO_NO_LLM") == "1":
        return None
    try:
        model = get_chat_model()
    except Exception:
        log("summarize: no OPENROUTER_API_KEY, skipping AI summary for " + check_name)
        return None
    log("summarize: asking OpenRouter for " + check_name)
    prompt = (
        "You are an SEO analyst. Check: " + check_name + ". "
        "Given these crawl stats (JSON), return STRICT JSON "
        'with keys "summary" (2-3 sentences) and "issues" (list of short strings). '
        "Stats: " + json.dumps(stats)[:4000]
    )
    try:
        answer = model.invoke(prompt)
    except Exception:
        return None
    if isinstance(answer.content, str):
        text = answer.content
    else:
        text = str(answer.content)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return {"summary": text[:1000], "issues": []}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return {"summary": text[:1000], "issues": []}
