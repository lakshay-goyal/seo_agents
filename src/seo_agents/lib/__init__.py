"""Configuration you tune by hand: system prompts, model choice per agent, and which tools each agent gets.

    lib/models.py         model tier per agent (small / haiku / mid / large), token caps, reasoning effort
    lib/tool_mapping.py   agent -> tools it is given (deterministic tools, LangChain tools, model calls)
    lib/prompts/          every system prompt and skill text sent to a model
    lib/signal_profiles/  regex signal profiles used by the deterministic agents (no model)
"""
