"""Parse a model reply into one JSON object, whatever shape the chat client returned."""

import json


def content_text(content):
    """Join LangChain content parts ([{'type': 'text', 'text': ...}, ...]) into one string; strings pass through."""
    if isinstance(content, list):
        return ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in content)
    return content


def parse_json_object(answer):
    """dict -> itself; text / content parts -> the outermost {...} object (code fences and prose are ignored).

    Raises ValueError('answer is not a JSON object') when the reply holds no object.
    """
    if isinstance(answer, dict):
        return answer
    text = str(content_text(answer))
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('answer is not a JSON object')
    return json.loads(text[start:end + 1])
