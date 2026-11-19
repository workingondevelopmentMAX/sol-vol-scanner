"""Engine tests: the daily scan's decision is locked to a golden file, and the backtest uses the same engine."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import engine  # noqa: E402
import golden_tools  # noqa: E402


class Golden(unittest.TestCase):
    def test_sample_decision_unchanged(self):
        """Fails if any code or rule change alters what the scanner suggests on the stored sample day.
        After a deliberate change: python tests/golden_tools.py --update, and commit the new golden file."""
        want = json.loads(golden_tools.GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(golden_tools.sample_decision(), want)


class Strategy(unittest.TestCase):
    def test_stamp_and_fingerprint(self):
        st = engine.load_strategy()
        self.assertEqual(st["_stamp"]["name"], "bull_put_spread")
        self.assertEqual(len(st["_stamp"]["fingerprint"]), 12)
        self.assertEqual(st["_stamp"]["fingerprint"], engine.load_strategy()["_stamp"]["fingerprint"])

    def test_rules_reach_the_settings_view(self):
        st = engine.load_strategy()
        view = engine.with_strategy({"risk": {}}, st)
        self.assertEqual(view["spread"]["dte_min"], st["strikes"]["dte_min"])
        self.assertEqual(view["exits"]["stop_multiple"], st["exits"]["stop_multiple"])


class SyntheticChain(unittest.TestCase):
    def test_model_reproduces_a_live_spread(self):
        # Live Deribit 2026-11-17: 25 Dec 64/58 put spread, conservative credit 2.53 - 1.28 = 1.25 (SOL 72.28 fwd, ATM IV 65.6%)
        st = engine.load_strategy()
        bt = st["backtest"]
        T = 37.6 / 365
        s, l = engine.model_put(bt, 72.28, 64, T, 0.656), engine.model_put(bt, 72.28, 58, T, 0.656)
        self.assertAlmostEqual(s["bid"] - l["ask"], 1.25, delta=0.08)

    def test_decide_on_a_rebuilt_day(self):
        st = engine.load_strategy()
        chain = engine.synthetic_chain(st, "2026-11-17", 72.1, 0.656)
        dec = engine.decide({"risk": {}}, st, {"label": "RICH"}, chain)
        self.assertIsNotNone(dec["trade"])
        self.assertTrue(-0.25 <= dec["trade"]["short_delta"] <= -0.15)
        self.assertTrue(14 <= dec["trade"]["dte"] <= 42)

    def test_not_rich_means_no_trade(self):
        st = engine.load_strategy()
        dec = engine.decide({"risk": {}}, st, {"label": "FAIR", "reason": "x"}, [])
        self.assertIsNone(dec["trade"])


class Deterministic(unittest.TestCase):
    def test_backtest_twice_identical(self):
        import backtest
        import main
        cfg = main.load_config()
        days = backtest.load_days(cfg)[-200:]
        a = backtest.run(cfg, cfg["_strategy"], days)
        b = backtest.run(cfg, cfg["_strategy"], days)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
