"""Step 4c tests: the Strategy Lab data (lab.py) matches the backtest exactly and never ranks versions by results."""
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import lab  # noqa: E402
import main  # noqa: E402
import report  # noqa: E402
import strategy_menu  # noqa: E402
import vol_gauge  # noqa: E402

BT = json.loads((ROOT / "data" / "backtest.json").read_text(encoding="utf-8"))
CFG = main.load_config(local=False)
MENU = strategy_menu.load()
HIST = vol_gauge.load_history(ROOT / "data" / "iv_history.csv")


def build(bts=None, hist=HIST):
    return lab.build(CFG, MENU, {"bull_put_spread.toml": BT} if bts is None else bts, hist, None,
                     now="2026-11-19T12:00+00:00")


LAB = build()
SEC = LAB["strategies"][0]
WINS = ("in_sample", "held_back")


class Scorecards(unittest.TestCase):                                                           # (a)
    def test_every_number_matches_backtest(self):
        self.assertEqual(len(SEC["variants"]), len(BT["variants"]))
        for v, b in zip(SEC["variants"], BT["variants"]):
            self.assertEqual(v["name"], b["name"])
            self.assertEqual(v["evidence"], b["evidence"])
            for w in WINS:
                sc = b["scorecard"][w]
                got = {r["key"]: r["value"] for r in v["windows"][w]["rows"]}
                for k in ("trades", "win_rate", "total_per_1000", "avg_per_1000", "worst_per_1000",
                          "max_losing_streak", "max_drawdown_per_1000"):
                    self.assertEqual(got[k], sc.get(k) if sc["trades"] else (0 if k == "trades" else None), (v["name"], w, k))
                self.assertEqual(got["hold_sol"], sc["hold_sol"]["per_1000"])
                self.assertEqual(got["dca_sol"], sc["dca_sol"]["per_1000"])

    def test_benchmark_rows(self):
        for w in WINS:
            sc = BT["variants"][0]["scorecard"][w]
            self.assertEqual(SEC["benchmarks"][w]["hold"], sc["hold_sol"])
            self.assertEqual(SEC["benchmarks"][w]["dca"], sc["dca_sol"])

    def test_live_rule_marked_once(self):
        self.assertEqual([v["name"] for v in SEC["variants"] if v["live"]], ["Strategy file as written"])

    def test_quotes_line(self):
        live = BT["variants"][0]["trades"]
        real = sum(t["chain"] == "stored" for t in live)
        self.assertEqual(SEC["quotes_line"], f"{real} of {len(live)} trades used real quotes; the rest use model "
                                             "prices built from that day's IV.")


class Order(unittest.TestCase):                                                                # (b)
    def test_strategy_file_order(self):
        written = [v["name"] for v in tomllib.loads(engine.DEFAULT_STRATEGY.read_text(encoding="utf-8"))["variants"]]
        self.assertEqual([v["name"] for v in SEC["variants"]], written)

    def test_not_sorted_by_results(self):
        bt = copy.deepcopy(BT)
        bt["variants"].reverse()                       # whatever order the backtest gives is kept, never re-sorted
        names = [v["name"] for v in build({"bull_put_spread.toml": bt})["strategies"][0]["variants"]]
        self.assertEqual(names, [v["name"] for v in bt["variants"]])
        by_unseen = sorted(names, key=lambda n: -next(v for v in bt["variants"] if v["name"] == n)
                           ["scorecard"]["held_back"]["total_per_1000"])
        self.assertNotEqual([v["name"] for v in SEC["variants"]], by_unseen)


class Series(unittest.TestCase):                                                               # (c)
    def test_running_and_drawdown_end_at_scorecard(self):
        for v, b in zip(SEC["variants"], BT["variants"]):
            for w in WINS:
                s, sc = v["windows"][w]["series"], b["scorecard"][w]
                self.assertEqual(s["points"][0]["value"], 0.0)
                if sc["trades"]:
                    self.assertAlmostEqual(s["points"][-1]["value"], sc["total_per_1000"], places=6, msg=(v["name"], w))
                    self.assertAlmostEqual(min(p["value"] for p in s["drawdown"]), sc["max_drawdown_per_1000"],
                                           places=6, msg=(v["name"], w))
                    self.assertEqual(len(s["points"]), sc["trades"] + 1)
                else:
                    self.assertEqual(s["points"], [{"date": BT["windows"][w][0], "value": 0.0}])

    def test_benchmark_series_end_at_benchmarks(self):
        for w in WINS:
            b = SEC["benchmarks"][w]
            self.assertAlmostEqual(b["series"]["hold"][-1]["value"], b["hold"]["per_1000"], places=6)
            self.assertAlmostEqual(b["series"]["dca"][-1]["value"], b["dca"]["per_1000"], places=6)
            self.assertEqual(b["series"]["hold"][0]["value"], 0.0)


class EntryGroups(unittest.TestCase):                                                          # (d)
    REP = {"caution_move_30d_pct": 15, "similar_min_trades": 5}

    def t(self, d, move, verdict="RICH", pnl=50.0):
        return {"date": d, "pnl_per_1000": pnl, "S_exit": 1.0, "exit": "stop",
                "entry_conditions": {"sol_30d_change_pct": move, "verdict": verdict}}

    def test_boundaries(self):
        ts = [self.t("2025-01-01", -15.0), self.t("2025-01-02", 15.0), self.t("2025-01-03", -15.1),
              self.t("2025-01-04", 15.1)]
        g = {x["move"]: x["trades"] for x in lab.entry_groups(ts, "2025-01-01", "2025-12-31", self.REP)}
        self.assertEqual(g, {"SOL within ±15%": 2, "SOL down more than 15%": 1, "SOL up more than 15%": 1})

    def test_too_few_at_4_not_5(self):
        for n, few in ((4, True), (5, False)):
            g = lab.entry_groups([self.t(f"2025-02-0{i + 1}", 0.0) for i in range(n)], "2025-01-01", "2025-12-31",
                                 self.REP)[0]
            self.assertEqual((g["trades"], g["too_few"], g["note"]), (n, few, "too few to tell" if few else ""))

    def test_threshold_from_config(self):
        g = lab.entry_groups([self.t("2025-01-01", -12.0)], "2025-01-01", "2025-12-31",
                             {"caution_move_30d_pct": 10, "similar_min_trades": 1})
        self.assertEqual(g[0]["move"], "SOL down more than 10%")

    def test_only_entry_day_fields(self):
        a = self.t("2025-01-01", -20.0)
        b = copy.deepcopy(a)
        b.update(S_exit=999.0, exit="take profit", exit_date="2030-01-01")      # change what happened after entry
        f = lambda t: [(x["move"], x["verdict"]) for x in lab.entry_groups([t], "2025-01-01", "2025-12-31", self.REP)]
        self.assertEqual(f(a), f(b))

    def test_lab_uses_report_settings(self):
        self.assertEqual(LAB["settings"]["caution_move_30d_pct"], 15)
        self.assertEqual(LAB["settings"]["similar_min_trades"], 5)


class Menu(unittest.TestCase):                                                                 # (e)
    def test_one_row_per_menu_entry(self):
        self.assertEqual([r["id"] for r in LAB["menu"]], [e["id"] for e in MENU])

    def test_not_built_rows_show_future_scorecard(self):
        rows = {r["id"]: r for r in LAB["menu"]}
        for e in MENU:
            if e["status"] == "not_built":
                self.assertTrue(rows[e["id"]]["note"].startswith("No backtest yet."))
                self.assertIn(lab.JUDGED_ON_TEXT[e["judged_on"]], rows[e["id"]]["note"])
        self.assertIn("drawdowns versus what it cost", rows["protective_hedge"]["note"])
        self.assertIn("SOL acquired per dollar versus plain weekly buying", rows["accumulation"]["note"])
        self.assertIn("standard scorecard", rows["call_debit_spread"]["note"])

    def test_built_row_jumps_to_section(self):
        row = LAB["menu"][0]
        self.assertEqual(row["anchor"], SEC["anchor"])
        self.assertEqual(row["evidence_title"], report.evidence_box(BT, BT["variants"][0])["title"])

    def test_daily_page_links_here(self):
        box = report.evidence_box(BT, BT["variants"][0], "bull_put_spread")
        self.assertEqual(box["lab_link"], "lab.html#lab-bull_put_spread")
        self.assertEqual(box["lab_link"].split("#")[1], SEC["anchor"])


class Rendering(unittest.TestCase):                                                            # (f)
    def test_missing_backtest_data(self):
        out = build({})
        self.assertEqual([b["kind"] for b in out["banners"]], ["missing"])
        self.assertEqual(out["strategies"], [])
        self.assertEqual(len(out["menu"]), len(MENU))
        self.assertTrue(out["menu"][0]["note"].startswith("No backtest yet."))

    def render(self, data: dict) -> dict:
        probe = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
        if probe.returncode != 0:
            self.skipTest("no headless browser here")
        site = Path(tempfile.mkdtemp()) / "site"
        shutil.copytree(ROOT / "site", site, ignore=shutil.ignore_patterns("data"))
        (site / "data").mkdir()
        (site / "data" / "lab.js").write_text("window.SCANNER_LAB = " + json.dumps(data) + ";", encoding="utf-8")
        js = """const { chromium } = require('playwright');
(async () => { const b = await chromium.launch(); const p = await b.newPage({viewport:{width:390, height:800}}); const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('file://' + process.argv[1]); await p.waitForTimeout(800);
  const rows = await p.$$('tr.trow'); if (rows.length) { await rows[0].click(); await p.waitForTimeout(200); }
  const out = {errs, banners: await p.$$eval('.banner.warn', n => n.map(x => x.textContent)),
    menu: await p.$$eval('.mrow', n => n.length), trades: rows.length, charts: await p.$$eval('.tdetail svg', n => n.length),
    overflow: await p.evaluate(() => document.documentElement.scrollWidth > 390)};
  console.log(JSON.stringify(out)); await b.close(); })();"""
        res = subprocess.run(["node", "-e", js, str(site / "lab.html")], capture_output=True, text=True, timeout=60)
        shutil.rmtree(site.parent, ignore_errors=True)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_page_without_backtest(self):
        got = self.render(build({}))
        self.assertEqual(got["errs"], [])
        self.assertTrue(any("Evidence is missing" in b for b in got["banners"]))
        self.assertEqual(got["menu"], len(MENU))

    def test_page_with_sample_history(self):
        sample = vol_gauge.load_history(ROOT / "data" / "sample" / "iv_history_seed.csv")
        got = self.render(build(hist=sample))
        self.assertEqual(got["errs"], [])
        self.assertEqual(got["menu"], len(MENU))
        self.assertGreater(got["trades"], 0)
        self.assertFalse(got["overflow"])

    def test_page_with_real_data_opens_a_trade_chart(self):
        got = self.render(LAB)
        self.assertEqual((got["errs"], got["charts"], got["overflow"]), ([], 1, False))


if __name__ == "__main__":
    unittest.main()
