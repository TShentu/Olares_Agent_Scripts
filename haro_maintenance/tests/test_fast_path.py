import sys, unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maintenance import summarize, Runner
from github_api import RemoteState, CheckpointError
from config import Error

class FastPathTests(unittest.TestCase):
    def test_summary_bounded_and_omits_large_payload(self):
        report = {"apps": [{"app": str(i), "status": "blocked", "diff": "x"*10000, "upgrade_description": "x"*10000} for i in range(100)]}
        result = summarize(report)
        self.assertEqual(result["counts"], {"blocked": 100})
        self.assertEqual(result["omitted"], 70)
        self.assertNotIn("diff", str(result))
        self.assertLess(len(str(result)), 2000)
    def test_uncertain_checkpoint_stops_close_writes(self):
        api = Mock(); api.request.side_effect = Error("GitHub HTTP 502")
        remote = RemoteState(api, "org/repo")
        with self.assertRaises(CheckpointError): remote.save({"tasks": {}})
        self.assertTrue(remote.uncertain)
        runner = Runner.__new__(Runner); runner.apply = True; runner.remote = remote
        api.reset_mock(); runner.close(); api.request.assert_not_called()
    def test_known_failed_write_is_fatal(self):
        remote = RemoteState(Mock(), "org/repo"); remote.sha = "old"
        remote.api.request.side_effect = Error("GitHub HTTP 502")
        remote.load = Mock(return_value={"different": True})
        with self.assertRaises(CheckpointError): remote.save({"tasks": {}})
        self.assertEqual(remote.sha, "old")
        self.assertTrue(remote.uncertain)
