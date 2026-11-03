"""LangGraph state reducers (the `Annotated[..., reducer]` functions) shared by every workflow."""


def merge_dicts(left, right):
    """Shallow merge; the right-hand (newer) update wins per key."""
    return {**(left or {}), **(right or {})}


def merge_min_depth(left, right):
    """Merge {path: click depth}, keeping the smallest known depth per path (None = unknown)."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if value is not None and (merged.get(key) is None or value < merged[key]):
            merged[key] = value
    return merged


def merge_page_results(left, right):
    """Merge {page_id: {check_name: result}} without dropping earlier checks of the same page."""
    merged = {page: dict(checks) for page, checks in (left or {}).items()}
    for page, checks in (right or {}).items():
        merged.setdefault(page, {}).update(checks)
    return merged
