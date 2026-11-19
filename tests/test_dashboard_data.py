"""PR 4a tests: the strategy menu in the daily output, entry-day conditions, evidence labels, published backtest."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import backtest  # noqa: E402
import golden_tools  # noqa: E402
import main  # noqa: E402
import strategy_menu  # noqa: E402
import vol_gauge  # noqa: E402

SAMPLE = json.loads((ROOT / "data" / "sample" / "snapshot.json").read_text(encoding="utf-8"))


def sample_result(menu_loader=None) -> dict:
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "data" / "sample" / "iv_history_seed.csv", tmp / "h.csv")
    old_h, vol_gauge.HISTORY_CSV = vol_gauge.HISTORY_CSV, tmp / "h.csv"
    old_l = strategy_menu.load
    if menu_loader:
        strategy_menu.load = menu_loader
    try:
        return main.analyse(main.load_config(), SAMPLE)["result"]
    finally:
        vol_gauge.HISTORY_CSV, strategy_menu.load = old_h, old_l
        shutil.rmtree(tmp, ignore_errors=True)


class MenuInDailyOutput(unittest.TestCase):
    def test_every_menu_entry_in_strategies(self):                                       # (a)
        menu = strategy_menu.load()
        r = sample_result()
        self.assertEqual([s["id"] for s in r["strategies"]], [e["id"] for e in menu])
        self.assertEqual(len(menu), 9)
        for s in r["strategies"]:
            self.assertIn(s["status"], {"suggested", "no_trade", "not_eligible", "not_built"})
            self.assertTrue(s["reason"])

    def test_written_to_latest_json(self):
        tmp = Path(tempfile.mkdtemp())
        saved = (main.SITE_DATA, main.SUGGESTIONS_CSV, main.TERM_HISTORY, main.CHAINS_DIR, vol_gauge.HISTORY_CSV)
        try:
            main.SITE_DATA, main.SUGGESTIONS_CSV, main.TERM_HISTORY = tmp / "site", tmp / "s.csv", tmp / "t.json"
            main.CHAINS_DIR, vol_gauge.HISTORY_CSV = None, tmp / "h.csv"
            shutil.copy(ROOT / "data" / "sample" / "iv_history_seed.csv", tmp / "h.csv")
            cfg = main.load_config()
            main.save_outputs(cfg, SAMPLE, main.analyse(cfg, SAMPLE), sample=False)
            latest = json.loads((tmp / "site" / "latest.json").read_text(encoding="utf-8"))["latest"]
        finally:
            main.SITE_DATA, main.SUGGESTIONS_CSV, main.TERM_HISTORY, main.CHAINS_DIR, vol_gauge.HISTORY_CSV = saved
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual({s["id"] for s in latest["strategies"]}, {e["id"] for e in strategy_menu.load()})
        self.assertIsNotNone(latest["suggestion"])                         # the old field is still there

    def test_live_entry_carries_todays_suggestion_and_stamp(self):
        r = sample_result()
        bps = r["strategies"][0]
        self.assertEqual(bps["status"], "suggested")
        self.assertEqual(bps["suggestion"], r["suggestion"])
        self.assertEqual(bps["strategy"]["fingerprint"], r["strategy"]["fingerprint"])

    def test_menu_never_changes_the_decision(self):                                     # (b)
        one = lambda path=None: [{"id": "x", "name": "X", "market": "any", "purpose": "p", "budget": "none",
                                  "status": "not_built", "roadmap_step": "9"}]
        with_menu, other_menu = sample_result(), sample_result(one)
        self.assertEqual(with_menu["suggestion"], other_menu["suggestion"])
        self.assertEqual(with_menu["alternates"], other_menu["alternates"])
        self.assertEqual(golden_tools.sample_decision(),
                         json.loads(golden_tools.GOLDEN.read_text(encoding="utf-8")))


class MenuStatuses(unittest.TestCase):
    def setUp(self):
        self.menu = strategy_menu.load()
        self.ran = {"bull_put_spread.toml": {"stamp": {"fingerprint": "abc"}, "suggestion": None,
                                             "no_trade_reason": "no spread passed the filters"}}

    def by_id(self, label):
        return {s["id"]: s for s in strategy_menu.statuses(self.menu, label, self.ran)}

    def test_fair_day(self):
        s = self.by_id("FAIR")
        self.assertEqual(s["bull_put_spread"]["status"], "not_eligible")
        self.assertEqual(s["bull_put_spread"]["reason"], "IV is FAIR; this strategy is for RICH days.")
        self.assertEqual(s["fair_no_trade"]["status"], "suggested")
        self.assertEqual(s["call_debit_spread"]["reason"], "Not built yet: step 5.2 on the roadmap.")

    def test_rich_day_without_a_trade(self):
        s = self.by_id("RICH")
        self.assertEqual(s["bull_put_spread"]["status"], "no_trade")
        self.assertEqual(s["bull_put_spread"]["reason"], "no spread passed the filters")
        self.assertEqual(s["fair_no_trade"]["status"], "not_eligible")

    def test_no_verdict(self):
        s = self.by_id(None)
        self.assertEqual(s["bull_put_spread"]["status"], "not_eligible")
        self.assertEqual(s["accumulation"]["status"], "not_built")

    def test_typos_refused(self):
        good = (ROOT / "strategies" / "menu.toml").read_text(encoding="utf-8")
        d = Path(tempfile.mkdtemp())
        try:
            for bad in (good.replace('market = "cheap"', 'market = "cheep"', 1),
                        good.replace('status = "live"', 'status = "running"', 1),
                        good.replace('file = "bull_put_spread.toml"', 'file = "missing.toml"'),
                        good.replace('id = "iron_condor"', 'id = "bull_put_spread"')):
                (d / "menu.toml").write_text(bad, encoding="utf-8")
                shutil.copy(ROOT / "strategies" / "bull_put_spread.toml", d)
                with self.assertRaises(ValueError):
                    strategy_menu.load(d / "menu.toml")
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EntryConditions(unittest.TestCase):
    def setUp(self):
        self.cfg = main.load_config(local=False)
        self.days = backtest.load_days(self.cfg, golden_tools.BACKTEST_HISTORY)
        self.trades = backtest.run(self.cfg, self.cfg["_strategy"], self.days, True, golden_tools.NO_CHAINS)

    def test_no_data_after_entry(self):                                                 # (c)
        for t in self.trades:
            i = next(k for k, d in enumerate(self.days) if d["date"] == t["date"])
            changed = [dict(d) for d in self.days]
            for d in changed[i + 1:]:                                    # rewrite the whole future
                d["S"] *= 3
                d["iv_percentile"] = 0.0
                d["verdict"] = {"label": "CHEAP", "reason": ""}
            self.assertEqual(backtest.entry_conditions(changed, i), t["entry_conditions"])
            self.assertEqual(backtest.entry_conditions(self.days[:i + 1], i), t["entry_conditions"])

    def test_values(self):
        days = [{"date": f"2025-01-{d:02d}", "S": s, "iv_percentile": 70.0, "verdict": {"label": "RICH"}}
                for d, s in ((1, 100.0), (10, 120.0), (31, 90.0))]
        c = backtest.entry_conditions(days, 2)
        self.assertEqual(c, {"sol_30d_change_pct": -10.0, "below_30d_high_pct": 25.0, "iv_percentile": 70.0,
                             "verdict": "RICH"})

    def test_hindsight_split_is_labelled(self):
        sc = backtest.scorecard(self.trades, self.days, "2024-06-01", "2025-06-30")
        self.assertIn("during the trade", sc["by_condition_label"])
        self.assertIn("never 'past days like today'", sc["by_condition_label"])


class EvidenceLabels(unittest.TestCase):
    REPORT = {"held_back_thin_below": 20}

    def sc(self, a, a_n, b, b_n):
        return {"in_sample": {"total_per_1000": a, "trades": a_n}, "held_back": {"total_per_1000": b, "trades": b_n}}

    def test_labels(self):
        e = backtest.evidence
        self.assertEqual(e(self.sc(10, 5, 20, 25), self.REPORT)["label"], "consistent")
        self.assertFalse(e(self.sc(10, 5, 20, 25), self.REPORT)["thin"])
        self.assertEqual(e(self.sc(-10, 5, 20, 25), self.REPORT)["label"], "mixed")
        self.assertEqual(e(self.sc(-10, 5, -20, 25), self.REPORT)["label"], "losing")
        self.assertEqual(e(self.sc(0, 0, 0, 0), self.REPORT)["label"], "losing")
        thin = e(self.sc(-1268, 14, 288, 11), self.REPORT)
        self.assertEqual((thin["label"], thin["thin"]), ("mixed", True))
        self.assertIn("only 11 unseen-period trades (fewer than 20)", thin["text"])

    def test_threshold_comes_from_config(self):
        self.assertEqual(main.load_config()["report"]["held_back_thin_below"], 20)
        self.assertTrue(backtest.evidence(self.sc(1, 1, 1, 15), {"held_back_thin_below": 10})["thin"] is False)

    def test_window_labels(self):
        cfg = main.load_config(local=False)
        days = backtest.load_days(cfg, golden_tools.BACKTEST_HISTORY)
        st = cfg["_strategy"]
        labels = backtest.window_labels(st, backtest.windows(st, days))
        self.assertTrue(labels["in_sample"].startswith("Practice period (Jun 2024 - Jun 2025): the rules were chosen"))
        self.assertTrue(labels["held_back"].startswith("Unseen period (Jul 2025 - today): kept aside"))


class Published(unittest.TestCase):
    def setUp(self):
        self.res = backtest.run_all(quiet=True, history_path=golden_tools.BACKTEST_HISTORY,
                                    chains_dir=golden_tools.NO_CHAINS)
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def latest(self, fp):
        (self.dir / "latest.json").write_text(json.dumps({"latest": {"strategy": {"fingerprint": fp}}}))

    def test_fingerprint_check(self):
        self.latest(self.res["strategy"]["fingerprint"])
        self.assertTrue(backtest.publish(self.res, self.dir)["rules_check"]["matches"])
        self.latest("someoldprint")
        chk = backtest.publish(self.res, self.dir)["rules_check"]
        self.assertFalse(chk["matches"])
        self.assertIn("older version of the rules", chk["note"])
        on_disk = json.loads((self.dir / "backtest.json").read_text(encoding="utf-8"))
        self.assertFalse(on_disk["rules_check"]["matches"])
        self.assertTrue((self.dir / "backtest.js").read_text(encoding="utf-8").startswith("window.SCANNER_BACKTEST = "))

    def test_byte_identical_including_labels(self):                                      # (d)
        again = backtest.run_all(quiet=True, history_path=golden_tools.BACKTEST_HISTORY,
                                 chains_dir=golden_tools.NO_CHAINS)
        self.assertEqual(backtest.dumps(self.res), backtest.dumps(again))
        self.latest("x")
        a = (backtest.publish(self.res, self.dir), (self.dir / "backtest.json").read_bytes())
        b = (backtest.publish(again, self.dir), (self.dir / "backtest.json").read_bytes())
        self.assertEqual(a[1], b[1])
        for v in self.res["variants"]:
            self.assertIn(v["evidence"]["label"], {"consistent", "mixed", "losing"})


if __name__ == "__main__":
    unittest.main()
