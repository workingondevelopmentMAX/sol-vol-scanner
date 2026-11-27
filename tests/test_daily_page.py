"""Step 4b tests: the daily page's at a glance, evidence box, for and against, and past days like today.
All of it is display only and worked out in report.py; these tests pin the numbers and the rules."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import golden_tools  # noqa: E402
import main  # noqa: E402
import report  # noqa: E402
import strategy_menu  # noqa: E402
import vol_gauge  # noqa: E402

SAMPLE = json.loads((ROOT / "data" / "sample" / "snapshot.json").read_text(encoding="utf-8"))
BT = json.loads((ROOT / "data" / "backtest.json").read_text(encoding="utf-8"))
REP = main.load_config()["report"]


def sample_day():
    """The sample day's result, annotated history and settings, with no files written."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "data" / "sample" / "iv_history_seed.csv", tmp / "h.csv")
    old, vol_gauge.HISTORY_CSV = vol_gauge.HISTORY_CSV, tmp / "h.csv"
    try:
        cfg = main.load_config()
        out = main.analyse(cfg, SAMPLE)
        hist = vol_gauge.annotate_history(cfg, out["history"])
    finally:
        vol_gauge.HISTORY_CSV = old
        shutil.rmtree(tmp, ignore_errors=True)
    return cfg, out["result"], hist


def trade(date, move, verdict="RICH", pnl=100.0):
    return {"date": date, "S": 100.0, "pnl_per_1000": pnl, "exit": "take profit", "S_exit": 1e9,
            "entry_conditions": {"sol_30d_change_pct": move, "verdict": verdict, "iv_percentile": 60.0,
                                 "below_30d_high_pct": 5.0}}


class Settings(unittest.TestCase):
    def test_confirmed_values(self):
        self.assertEqual(REP, {"held_back_thin_below": 20, "caution_move_30d_pct": 15, "similar_move_band_pts": 10,
                               "similar_min_trades": 5})

    def test_golden_files_unchanged(self):
        self.assertEqual(golden_tools.sample_decision(), json.loads(golden_tools.GOLDEN.read_text(encoding="utf-8")))
        self.assertEqual(golden_tools.dump(golden_tools.backtest_slice()),
                         golden_tools.BACKTEST_GOLDEN.read_text(encoding="utf-8"))


class EvidenceBox(unittest.TestCase):                                                       # (a)
    def test_every_number_matches_backtest_json(self):
        v = next(x for x in BT["variants"] if x["name"] == "Strategy file as written")
        box = report.evidence_box(BT, v)
        self.assertEqual(box["variant"], "Strategy file as written")
        self.assertEqual(box["label"], v["evidence"]["label"])
        for w in box["windows"]:
            sc = v["scorecard"][w["key"]]
            self.assertEqual(w["label"], BT["window_labels"][w["key"]])
            got = {r["key"]: r["value"] for r in w["rows"]}
            for k in ("trades", "win_rate", "total_per_1000", "avg_per_1000", "worst_per_1000", "max_losing_streak",
                      "max_drawdown_per_1000"):
                self.assertEqual(got[k], sc[k], k)
            self.assertEqual(got["hold_sol"], sc["hold_sol"]["per_1000"])
            self.assertEqual(got["dca_sol"], sc["dca_sol"]["per_1000"])
        self.assertIn(f"{len(BT['variants'])} versions of these rules were tested", box["versions_note"])

    def test_title(self):
        v = copy.deepcopy(BT["variants"][0])
        v["evidence"].update(label="mixed", thin=True)
        self.assertEqual(report.evidence_box(BT, v)["title"], "Thin evidence, mixed results")
        v["evidence"].update(label="consistent", thin=False)
        self.assertEqual(report.evidence_box(BT, v)["title"], "Enough trades to judge, consistent results")


class SimilarDays(unittest.TestCase):                                                       # (b)
    TODAY = {"sol_30d_change_pct": -30.0, "verdict": "RICH"}

    def test_band_edges(self):
        ts = [trade("2025-01-01", -20.0), trade("2025-01-02", -40.0), trade("2025-01-03", -19.9),
              trade("2025-01-04", -40.1), trade("2025-01-05", -30.0, verdict="FAIR")]
        got = [t["date"] for t in report.similar_trades(ts, self.TODAY, 10)]
        self.assertEqual(got, ["2025-01-01", "2025-01-02"])            # exactly 10 away in; just over 10 out

    def test_only_entry_day_fields_are_used(self):
        a = trade("2025-01-01", -25.0)
        b = copy.deepcopy(a)
        b.update(S_exit=-1, pnl_per_1000=-999.0, exit="stop", exit_date="2030-01-01")   # change everything after entry
        self.assertEqual(len(report.similar_trades([a], self.TODAY, 10)), len(report.similar_trades([b], self.TODAY, 10)))
        c = copy.deepcopy(a)
        c["entry_conditions"]["sol_30d_change_pct"] = -45.0               # change the entry day -> excluded
        self.assertEqual(report.similar_trades([c], self.TODAY, 10), [])

    def test_too_few_at_4_not_at_5(self):
        bt = {"windows": {"in_sample": ["2024-06-01", "2025-06-30"], "held_back": ["2025-07-01", "2026-12-31"]}}
        rep = dict(REP)
        for n, enough in ((4, False), (5, True)):
            v = {"trades": [trade(f"2025-0{i + 1}-01", -30.0) for i in range(n)]}
            s = report.similar_days(bt, v, self.TODAY, rep)
            self.assertEqual((s["count"], s["enough"]), (n, enough))
            self.assertEqual(s["text"].startswith("Too few similar past days to tell"), not enough)
        self.assertEqual(report.similar_days(bt, {"trades": [trade("2025-01-01", -30.0)] * 4}, self.TODAY, rep)["text"],
                         "Too few similar past days to tell (4 found).")

    def test_hindsight_split_kept_apart(self):
        v = next(x for x in BT["variants"] if x["name"] == "Strategy file as written")
        d = report.during_trade(v)
        self.assertEqual(d["label"], "What happened when SOL fell during the trade")


class Cautions(unittest.TestCase):                                                          # (c)
    def setUp(self):
        self.cfg, self.res, self.hist = sample_day()
        self.s = self.res["suggestion"]
        self.v = copy.deepcopy(next(x for x in BT["variants"] if x["name"] == "Strategy file as written"))

    def keys(self, move=0.0, held=25, match=True, similar=None):
        self.v["scorecard"]["held_back"]["trades"] = held
        today = {"sol_30d_change_pct": move, "verdict": "RICH"}
        return [c["key"] for c in report.cautions(REP, self.s, float(self.res["index_price"]), today, self.v,
                                                  match, similar)]

    def test_big_move_threshold(self):
        for move, fires in ((15.0, False), (-15.0, False), (15.1, True), (-15.1, True), (3.0, False)):
            self.assertEqual("big_move" in self.keys(move=move), fires, move)

    def test_thin_threshold(self):
        self.assertIn("thin", self.keys(held=19))
        self.assertNotIn("thin", self.keys(held=20))

    def test_lost_older_rules_few_similar(self):
        k = self.keys(match=False, similar={"enough": False, "count": 3, "min_trades": 5})
        self.assertIn("older_rules", k)
        self.assertIn("few_similar", k)
        sc = self.v["scorecard"]
        lost = sum(sc[w]["trades"] > 0 and sc[w]["total_per_1000"] < 0 for w in ("in_sample", "held_back"))
        self.assertEqual(k.count("lost"), lost)                            # one caution per losing period

    def test_cushion(self):
        self.assertLess(report.cushion_ratio(self.s), 1.0)                 # sample day: 0.7x
        self.assertIn("cushion", self.keys())

    def test_cautions_never_change_the_decision(self):
        before = json.dumps(self.res["suggestion"], sort_keys=True, default=str)
        report.build(self.cfg, self.res, BT, self.hist, report.load_view(today="2026-11-15"))
        self.assertEqual(json.dumps(self.res["suggestion"], sort_keys=True, default=str), before)


class AtAGlance(unittest.TestCase):                                                         # (d)
    def setUp(self):
        self.cfg, self.res, self.hist = sample_day()
        self.view = report.load_view(today="2026-11-15")

    def build(self, res, bt=BT):
        return report.build(self.cfg, res, bt, self.hist, self.view)

    def test_sample_day(self):
        r = self.build(self.res)
        rows = r["glance"]["rows"]
        self.assertEqual([x["id"] for x in rows], [e["id"] for e in strategy_menu.load()])
        st = {x["id"]: x for x in rows}
        self.assertEqual(st["bull_put_spread"]["status"], "suggested")
        self.assertEqual(st["bull_put_spread"]["card"], "idea-bull_put_spread")
        self.assertEqual(st["fair_no_trade"]["text"], "Wrong conditions today: for FAIR IV days; IV is RICH")
        self.assertEqual(st["iron_condor"]["text"], "Not built yet (roadmap step 5.3)")
        self.assertIsNone(r["glance"]["footer"])
        self.assertEqual(len(r["cards"]), 1)
        self.assertEqual(r["banners"], [])

    def test_no_trade_day(self):
        res = copy.deepcopy(self.res)
        res["verdict"] = dict(res["verdict"], label="FAIR")
        res["suggestion"], res["no_trade_reason"] = None, "Volatility is FAIR"
        res["strategies"] = strategy_menu.statuses(strategy_menu.load(), "FAIR", {
            "bull_put_spread.toml": {"stamp": res["strategy"], "suggestion": None, "no_trade_reason": "x"}})
        r = self.build(res)
        st = {x["id"]: x for x in r["glance"]["rows"]}
        self.assertEqual(st["bull_put_spread"]["status"], "not_eligible")
        self.assertEqual(st["bull_put_spread"]["text"], "Wrong conditions today: for RICH IV days; IV is FAIR")
        self.assertEqual(st["fair_no_trade"]["status"], "suggested")
        self.assertIsNone(st["fair_no_trade"]["card"])
        self.assertEqual(r["glance"]["footer"], "No trade today. Sitting out is the plan working.")
        self.assertEqual(r["cards"], [])

    def test_no_trade_on_a_rich_day(self):
        res = copy.deepcopy(self.res)
        res["strategies"] = strategy_menu.statuses(strategy_menu.load(), "RICH", {
            "bull_put_spread.toml": {"stamp": res["strategy"], "suggestion": None,
                                     "no_trade_reason": "no put spread passed the filters"}})
        st = {x["id"]: x for x in self.build(res)["glance"]["rows"]}
        self.assertEqual(st["bull_put_spread"]["text"],
                         "Right conditions, but nothing passed the rules today: no put spread passed the filters")

    def test_mismatched_fingerprint(self):
        bt = copy.deepcopy(BT)
        bt["strategy"]["fingerprint"] = "000000000000"
        r = self.build(self.res, bt)
        self.assertEqual([b["kind"] for b in r["banners"]], ["older_rules"])
        self.assertFalse(r["rules_match"])
        self.assertIn("older_rules", [c["key"] for c in r["cards"][0]["cautions"]])
        self.assertEqual(len(r["glance"]["rows"]), len(strategy_menu.load()))

    def test_view_line(self):
        self.assertIn("Your view: neutral, strength 1 of 3, until 2026-12-31", self.build(self.res)["glance"]["line"])
        self.assertEqual(report.load_view(today="2027-01-05")["text"], "expired on 2026-12-31: counts as neutral")


class MissingBacktest(unittest.TestCase):                                                   # (e)
    def test_report_without_backtest(self):
        cfg, res, hist = sample_day()
        r = report.build(cfg, res, None, hist, report.load_view(today="2026-11-15"))
        self.assertEqual([b["kind"] for b in r["banners"]], ["missing"])
        self.assertIsNone(r["cards"][0]["evidence"])
        self.assertTrue(r["cards"][0]["evidence_missing"])
        self.assertIsNone(r["cards"][0]["similar"])
        self.assertEqual(len(r["glance"]["rows"]), 9)

    def test_page_renders_without_backtest(self):
        """Opens the real page in a headless browser. Skipped where no browser is installed (e.g. GitHub Actions)."""
        probe = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("no headless browser here")
        cfg, res, hist = sample_day()
        res["report"] = report.build(cfg, res, None, hist, report.load_view(today="2026-11-15"))
        site = Path(tempfile.mkdtemp()) / "site"
        shutil.copytree(ROOT / "site", site, ignore=shutil.ignore_patterns("data"))
        (site / "data").mkdir()
        dash = {"status": "ok", "last_good_at": res["generated_at"], "latest": res, "history": hist[-200:], "log": [],
                "premium": {"trades": 0},
                "config": {"risk": cfg["risk"], "vol": cfg["vol"], "conviction": cfg["conviction"]}}
        (site / "data" / "data.js").write_text("window.SCANNER_DATA = " + json.dumps(dash, default=str) + ";",
                                               encoding="utf-8")
        js = """const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage(); const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('file://' + process.argv[1]); await p.waitForTimeout(800);
  const banners = await p.$$eval('.banner.warn', n => n.map(x => x.textContent));
  const rows = await p.$$eval('.grow', n => n.length);
  console.log(JSON.stringify({errs, banners, rows})); await b.close(); })();"""
        out = subprocess.run(["node", "-e", js, str(site / "index.html")], capture_output=True, text=True, timeout=60)
        shutil.rmtree(site.parent, ignore_errors=True)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got["errs"], [])
        self.assertTrue(any("Evidence is missing" in b for b in got["banners"]))
        self.assertEqual(got["rows"], 9)


if __name__ == "__main__":
    unittest.main()
