"""Optional AI summary of crawl stats through OpenRouter.

The static crawl always runs. The summary is attached on top when
OPENROUTER_API_KEY is set. Anything fails -> return None, never crash.
"""

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
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=0, timeout=90, max_retries=2)


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
