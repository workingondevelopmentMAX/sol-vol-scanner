"""Step 5.1 tests: direction factors use no future data, the pass rule is exact, the view is recorded once a day,
the test is reproducible, and nothing here changes a trade suggestion."""
import copy
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import direction  # noqa: E402
import golden_tools  # noqa: E402
import main  # noqa: E402
import report  # noqa: E402
import vol_gauge  # noqa: E402

RULES = direction.load_rules()
DAYS = direction.days_from_history(vol_gauge.load_history(ROOT / "data" / "iv_history.csv"))


class NoLookAhead(unittest.TestCase):                                                          # (a)
    def test_future_never_changes_a_day(self):
        for i in range(0, len(DAYS), 37):
            changed = [dict(d) for d in DAYS]
            for d in changed[i + 1:]:
                d["S"] *= 5
                d["iv"] = 9.9 if d["iv"] is not None else None
            for name in direction.TESTED:
                self.assertEqual(direction.factor_state(RULES, name, changed, i),
                                 direction.factor_state(RULES, name, DAYS, i), (name, DAYS[i]["date"]))
                self.assertEqual(direction.factor_state(RULES, name, DAYS[:i + 1], i),
                                 direction.factor_state(RULES, name, DAYS, i), (name, DAYS[i]["date"]))

    def test_outcome_uses_exactly_30_days_later(self):
        i = 100
        end, mv = direction.outcome(DAYS, i, 30)
        self.assertEqual(end, DAYS[i + 30]["date"])
        self.assertAlmostEqual(mv, (DAYS[i + 30]["S"] / DAYS[i]["S"] - 1) * 100)


class PassRule(unittest.TestCase):                                                             # (b)
    def g(self, avg, periods):
        return {"days": 50, "avg_move_pct": avg, "pct_up": 50.0, "periods": periods}

    def windows(self, spread, periods, one_sided=False):
        if one_sided:
            w = {"fear": self.g(spread, periods), "other": self.g(0.0, 20)}
        else:
            w = {"bullish": self.g(spread, periods), "neutral": self.g(0, 9), "bearish": self.g(0.0, periods)}
        return {"in_sample": w, "held_back": copy.deepcopy(w)}

    def test_spread_threshold(self):
        self.assertTrue(direction.judge(RULES, False, self.windows(5.0, 4))["pass"])
        self.assertFalse(direction.judge(RULES, False, self.windows(4.99, 4))["pass"])

    def test_period_threshold(self):
        self.assertTrue(direction.judge(RULES, False, self.windows(8.0, 4))["pass"])
        self.assertFalse(direction.judge(RULES, False, self.windows(8.0, 3))["pass"])

    def test_both_windows_needed(self):
        w = self.windows(8.0, 6)
        w["held_back"]["bullish"]["avg_move_pct"] = 4.0
        self.assertFalse(direction.judge(RULES, False, w)["pass"])

    def test_one_sided(self):
        self.assertTrue(direction.judge(RULES, True, self.windows(5.0, 4, True))["pass"])
        self.assertFalse(direction.judge(RULES, True, self.windows(5.0, 3, True))["pass"])

    def test_non_overlapping_count(self):
        d = ["2025-01-01", "2025-01-02", "2025-01-30", "2025-01-31", "2025-03-01", "2025-03-03"]
        self.assertEqual(direction.non_overlapping(d, 30), 3)    # 01-01, 01-31, 03-03 (03-01 is within 30 of 01-31)

    def test_rules_file_thresholds(self):
        self.assertEqual((RULES["evaluation"]["min_spread_points"], RULES["evaluation"]["min_periods"]), (5, 4))


class View(unittest.TestCase):                                                                 # (c)
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def view_file(self, text):
        p = self.d / "view.toml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_expired_view_is_neutral_and_recorded_false(self):
        v = report.load_view(self.view_file('direction = "bullish"\nstrength = 3\nexpires = 2026-01-31\nnote = "x"\n'),
                             today="2026-02-01")
        self.assertTrue(v["expired"])
        self.assertEqual(v["in_force"], "neutral")
        self.assertEqual(direction.view_row("2026-02-01", v)["in_force"], "false")
        c = direction.combine(RULES, {}, [], v)
        self.assertEqual((c["view_tilt"], c["final_reading"]), (0.0, "neutral"))

    def test_view_in_force_tilts(self):
        v = report.load_view(self.view_file('direction = "bullish"\nstrength = 2\nexpires = 2026-12-31\n'),
                             today="2026-11-20")
        self.assertEqual(direction.view_row("2026-11-20", v)["in_force"], "true")
        c = direction.combine(RULES, {}, [], v)
        self.assertAlmostEqual(c["final_score"], 0.30)
        self.assertEqual(c["final_reading"], "bullish")
        v1 = report.load_view(self.view_file('direction = "bearish"\nstrength = 1\nexpires = 2026-12-31\n'),
                              today="2026-11-20")
        self.assertEqual(direction.combine(RULES, {}, [], v1)["final_reading"], "neutral")    # -0.15: stays neutral

    def test_tilt_is_clipped(self):
        c = direction.combine(RULES, {"trend": {"state": "bullish"}}, ["trend"],
                              {"in_force": "bullish", "strength": 3, "available": True})
        self.assertEqual(c["final_score"], 1.0)


class DailyRecord(unittest.TestCase):                                                          # (d)
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_first_run_of_the_day_wins(self):
        p = self.d / "view_history.csv"
        a = {"date": "2026-11-20", "direction": "bullish", "strength": 2, "expires": "2026-12-31", "in_force": "true",
             "note": "first"}
        self.assertTrue(direction.append_once(p, direction.VIEW_FIELDS, a))
        before = p.read_bytes()
        self.assertFalse(direction.append_once(p, direction.VIEW_FIELDS, dict(a, direction="bearish", note="later")))
        self.assertEqual(p.read_bytes(), before)                                   # never rewritten
        self.assertTrue(direction.append_once(p, direction.VIEW_FIELDS, dict(a, date="2026-11-21")))
        self.assertTrue(p.read_bytes().startswith(before))                         # only appended to
        with p.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r["date"], r["note"]) for r in rows], [("2026-11-20", "first"), ("2026-11-21", "first")])

    def test_sample_runs_record_nothing(self):
        saved = (main.VIEW_HISTORY, main.DIRECTION_HISTORY, main.CHAINS_DIR)
        try:
            main.use_sample_paths()
            self.assertIsNone(main.VIEW_HISTORY)
            self.assertIsNone(main.DIRECTION_HISTORY)
        finally:
            main.VIEW_HISTORY, main.DIRECTION_HISTORY, main.CHAINS_DIR = saved


class Reproducible(unittest.TestCase):                                                         # (e)
    def test_byte_identical(self):
        a = direction.dumps(direction.run_test(RULES, DAYS))
        b = direction.dumps(direction.run_test(direction.load_rules(), direction.days_from_history(
            vol_gauge.load_history(ROOT / "data" / "iv_history.csv"))))
        self.assertEqual(a, b)

    def test_factor_order_and_recording_only(self):
        res = direction.run_test(RULES, DAYS)
        self.assertEqual([f["name"] for f in res["factors"]], list(RULES["factors"]))
        self.assertEqual({f["name"]: f["result"] for f in res["factors"]}["skew"], "RECORDING ONLY")
        self.assertTrue(all(f["name"] in direction.TESTED for f in res["factors"] if f["result"] == "PASS"))


class NoEffectOnTrades(unittest.TestCase):                                                     # (f)
    def run_sample(self, patch=None):
        tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "data" / "sample" / "iv_history_seed.csv", tmp / "h.csv")
        old_h, vol_gauge.HISTORY_CSV = vol_gauge.HISTORY_CSV, tmp / "h.csv"
        old_t = direction.today
        if patch:
            direction.today = patch
        try:
            snap = json.loads((ROOT / "data" / "sample" / "snapshot.json").read_text(encoding="utf-8"))
            return main.analyse(main.load_config(), snap)["result"]
        finally:
            vol_gauge.HISTORY_CSV, direction.today = old_h, old_t
            shutil.rmtree(tmp, ignore_errors=True)

    def test_direction_never_changes_the_suggestion(self):
        normal = self.run_sample()
        self.assertIn("direction", normal)
        forced = self.run_sample(lambda *a, **k: {"final_reading": "bearish", "data_reading": "bearish", "passed": [],
                                                 "tested": 4, "factors": [], "view": {}, "note": "x",
                                                 "final_score": -1.0, "data_score": -1.0})
        for k in ("suggestion", "alternates", "no_trade_reason"):
            self.assertEqual(json.dumps(normal[k], sort_keys=True, default=str),
                             json.dumps(forced[k], sort_keys=True, default=str), k)

    def test_golden_files_unchanged(self):
        self.assertEqual(golden_tools.sample_decision(), json.loads(golden_tools.GOLDEN.read_text(encoding="utf-8")))
        self.assertEqual(golden_tools.dump(golden_tools.backtest_slice()),
                         golden_tools.BACKTEST_GOLDEN.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
