"""The orchestrator runs its six steps in order, carries state between them and survives optional-step failures."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from seo_agents import orchestrator
from seo_agents.orchestrator import PipelineState
from seo_agents.sandbox import report_bundle


def fake_steps(calls, *, keywords_error=None):
    def clone(state):
        calls.append("clone")
        return {"repo_path": "/tmp/repo", "commit_sha": "a" * 40}

    def install(state):
        calls.append("install")
        return {}

    def explore(state):
        calls.append("explore")
        assert state.repo_path == "/tmp/repo"
        return {"reports_dir": "/tmp/repo/seo_reports", "site_model_dir": "/tmp/repo/seo_reports/site_model"}

    def keywords(state):
        calls.append("keywords")
        if keywords_error:
            return {"errors": state.errors + ["keywords: " + keywords_error]}
        return {"keywords_dir": state.reports_dir + "/keywords"}

    def onpage(state):
        calls.append("onpage")
        return {"onpage_dir": state.reports_dir + "/onpage"}

    def bundle(state):
        calls.append("bundle")
        return {"bundle_path": "/tmp/bundle.json"}

    return {"clone_repository": clone, "install_dependencies": install, "explore_repository": explore,
            "research_keywords": keywords, "improve_onpage_and_technical_seo": onpage, "write_bundle": bundle}


class PipelineOrderTests(unittest.TestCase):
    def run_with(self, steps):
        with mock.patch.multiple(orchestrator, **steps):
            return orchestrator.run_pipeline("https://github.com/o/r", build=False, render=False)

    def test_steps_run_in_order_and_share_state(self):
        calls = []
        state = self.run_with(fake_steps(calls))
        self.assertEqual(calls, ["clone", "install", "explore", "keywords", "onpage", "bundle"])
        self.assertIsInstance(state, PipelineState)
        self.assertEqual(state.keywords_dir, "/tmp/repo/seo_reports/keywords")
        self.assertEqual(state.onpage_dir, "/tmp/repo/seo_reports/onpage")
        self.assertEqual(state.bundle_path, "/tmp/bundle.json")
        self.assertEqual(state.errors, [])

    def test_optional_step_failure_is_recorded_and_run_continues(self):
        calls = []
        state = self.run_with(fake_steps(calls, keywords_error="no API key"))
        self.assertEqual(calls[-2:], ["onpage", "bundle"])
        self.assertIsNone(state.keywords_dir)
        self.assertEqual(state.errors, ["keywords: no API key"])

    def test_required_step_failure_stops_the_run(self):
        calls = []
        steps = fake_steps(calls)

        def broken_clone(state):
            raise RuntimeError("clone failed")

        steps["clone_repository"] = broken_clone
        with self.assertRaises(RuntimeError):
            self.run_with(steps)
        self.assertEqual(calls, [])

    def test_real_keyword_step_records_exceptions(self):
        state = PipelineState(github_url="https://github.com/o/r", reports_dir="/nowhere")
        with mock.patch("seo_agents.keyword_research.keyword_research.KeywordResearchWorkflow.run",
                        side_effect=RuntimeError("boom")):
            changes = orchestrator.research_keywords(state)
        self.assertEqual(changes, {"errors": ["keywords: RuntimeError: boom"]})


class InstallStepTests(unittest.TestCase):
    def test_skipped_without_build(self):
        state = PipelineState(github_url="https://github.com/o/r", repo_path="/tmp/repo", build=False)
        with mock.patch("seo_agents.repo_analysis.measurement.site_server.SiteServer.install") as install:
            self.assertEqual(orchestrator.install_dependencies(state), {})
        install.assert_not_called()

    def test_skipped_with_site_url(self):
        state = PipelineState(github_url="https://github.com/o/r", repo_path="/tmp/repo", build=True,
                              site_url="https://example.com")
        with mock.patch("seo_agents.repo_analysis.measurement.site_server.SiteServer.install") as install:
            self.assertEqual(orchestrator.install_dependencies(state), {})
        install.assert_not_called()


class ReportBundleTests(unittest.TestCase):
    def test_collects_every_report_folder(self):
        with TemporaryDirectory() as tmp:
            reports = Path(tmp) / "seo_reports"
            for folder in ("site_model", "exploration", "keywords", "onpage"):
                (reports / folder).mkdir(parents=True)
                (reports / folder / "index.json").write_text("{}")
            (reports / "repo_context.json").write_text("{}")
            files = report_bundle.report_files(tmp, site_model_dir=reports / "site_model",
                                               keywords_dir=reports / "keywords", onpage_dir=reports / "onpage",
                                               repo_context_path=reports / "repo_context.json")
        self.assertEqual(sorted(files), ["seo_reports/exploration/index.json", "seo_reports/keywords/index.json",
                                         "seo_reports/onpage/index.json", "seo_reports/repo_context.json",
                                         "seo_reports/site_model/index.json"])

    def test_write_bundle_scrubs_local_paths(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "my-site"
            repo.mkdir()
            files = {"seo_reports/repo_context.json": json.dumps({"root": str(repo)})}
            path = report_bundle.write_bundle("https://github.com/o/my-site", "b" * 40, repo, files,
                                              bundle_dir=Path(tmp) / "out")
            bundle = json.loads(Path(path).read_text())
        self.assertTrue(path.endswith("my-site-bbbbbbbbbbbb.json"))
        self.assertEqual(bundle["sha"], "b" * 40)
        self.assertNotIn(str(repo), json.dumps(bundle["files"]))


if __name__ == "__main__":
    unittest.main()
