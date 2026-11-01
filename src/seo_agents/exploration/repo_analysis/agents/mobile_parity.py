"""Viewport, zoom restrictions, responsive images and mobile-hidden content."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def analyze_mobile_parity(index):
    return run_skill(index, 'mobile-parity')
