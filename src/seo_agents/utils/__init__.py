"""Small helpers shared by several agents, tools and workflows (one copy instead of one per module).

    step_logger.py     timestamped START/DONE lines and a heartbeat for long steps
    state_reducers.py  LangGraph state reducers (merge dicts, keep the smallest crawl depth, merge per-page results)
    json_parsing.py    turn a model reply (string, content parts or dict) into one JSON object
    prompt_files.py    read `---` frontmatter + body from a SKILL.md / prompt file
    env_settings.py    typed environment readers (flags, bounded integers)
"""
