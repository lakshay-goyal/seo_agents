"""Run the 7 on-page checks in order using LangGraph.

The graph is a straight chain: content -> structure -> links ->
metadata -> media -> urls -> eeat. Each step runs one check and
saves its result in the shared state under "artifacts".
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.log import done, start

from seo_agents.exploration.checks.onpage.content_arrangement import analyze_content_arrangement
from seo_agents.exploration.checks.onpage.eeat_trust_signals import analyze_eeat_trust_signals
from seo_agents.exploration.checks.onpage.internal_links import analyze_internal_links
from seo_agents.exploration.checks.onpage.media_alt_text import analyze_media_alt_text
from seo_agents.exploration.checks.onpage.metadata_social_cards import analyze_metadata_social_cards
from seo_agents.exploration.checks.onpage.page_structure import analyze_page_structure
from seo_agents.exploration.checks.onpage.url_information_architecture import analyze_url_information_architecture


class OnpageState(TypedDict, total=False):
    repo_path: str
    artifacts: dict


CHECKS = [
    ("content", analyze_content_arrangement),
    ("page_structure", analyze_page_structure),
    ("internal_links", analyze_internal_links),
    ("metadata", analyze_metadata_social_cards),
    ("media", analyze_media_alt_text),
    ("urls", analyze_url_information_architecture),
    ("eeat", analyze_eeat_trust_signals),
]


def make_node(key, func):
    def node(state):
        begun = start("check: " + key)
        saved = dict(state.get("artifacts") or {})
        saved[key] = func(state.get("repo_path"))
        result = {"artifacts": saved}
        done("check: " + key, begun)
        return result
    return node


def build_onpage_graph():
    graph = StateGraph(OnpageState)
    for key, func in CHECKS:
        graph.add_node(key, make_node(key, func))
    graph.add_edge(START, CHECKS[0][0])
    for i in range(len(CHECKS) - 1):
        graph.add_edge(CHECKS[i][0], CHECKS[i + 1][0])
    graph.add_edge(CHECKS[-1][0], END)
    return graph.compile()


def run_onpage_graph(repo_path=None):
    begun = start("on-page graph (7 checks)")
    graph = build_onpage_graph()
    start_state = {"artifacts": {}}
    if repo_path:
        start_state["repo_path"] = repo_path
    result = graph.invoke(start_state)
    done("on-page graph (7 checks)", begun)
    return result
