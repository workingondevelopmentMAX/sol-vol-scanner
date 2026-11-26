"""Step 5.2 tests: debit spreads on cheap-IV days through the shared engine; selection and pass rules; paper log."""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import backtest  # noqa: E402
import engine  # noqa: E402
import golden_tools  # noqa: E402
import main  # noqa: E402
import paper  # noqa: E402

ST = engine.load_strategy(ROOT / "strategies" / "debit_spreads.toml")
CHAIN = engine.synthetic_chain(ST, "2025-05-01", 150.0, 0.60)
CFG = main.load_config(local=False)


class Golden(unittest.TestCase):
    def test_debit_sample_decision_unchanged(self):
        self.assertEqual(golden_tools.dump(golden_tools.debit_sample_decision()),
                         golden_tools.DEBIT_GOLDEN.read_text(encoding="utf-8"))

    def test_debit_backtest_slice_unchanged(self):
        self.assertEqual(golden_tools.dump(golden_tools.debit_backtest_slice()),
                         golden_tools.DEBIT_BACKTEST_GOLDEN.read_text(encoding="utf-8"))

    def test_put_spread_goldens_unchanged(self):
        self.assertEqual(golden_tools.sample_decision(), json.loads(golden_tools.GOLDEN.read_text(encoding="utf-8")))
        self.assertEqual(golden_tools.dump(golden_tools.backtest_slice()),
                         golden_tools.BACKTEST_GOLDEN.read_text(encoding="utf-8"))


class Direction(unittest.TestCase):                                                            # (a)
    def sides(self, reading, st=ST):
        t = engine.decide(CFG, st, {"label": "CHEAP"}, CHAIN, reading)["trade"]
        return [(s["kind"], s["share"]) for s in t["sides"]]

    def test_mapping(self):
        self.assertEqual(self.sides("bullish"), [("call", 1.0)])
        self.assertEqual(self.sides("bearish"), [("put", 1.0)])
        self.assertEqual(self.sides("neutral"), [("call", 0.5), ("put", 0.5)])

    def test_fixed_sides_ignore_the_reading(self):
        for mode, want in (("calls", [("call", 1.0)]), ("puts", [("put", 1.0)]),
                           ("both", [("call", 0.5), ("put", 0.5)])):
            st = copy.deepcopy(ST)
            st["direction"]["sides"] = mode
            for reading in ("bullish", "neutral", "bearish"):
                self.assertEqual(self.sides(reading, st), want, (mode, reading))

    def test_not_cheap_means_no_trade(self):
        self.assertIsNone(engine.decide(CFG, ST, {"label": "RICH", "reason": ""}, CHAIN, "bullish")["trade"])

    def test_strike_rules(self):
        t = engine.decide(CFG, ST, {"label": "CHEAP"}, CHAIN, "neutral")["trade"]
        for s in t["sides"]:
            self.assertTrue(45 <= s["dte"] <= 75)
            self.assertLessEqual(s["cost_pct_of_width"], 35)
            self.assertAlmostEqual(abs(s["long_delta"]), 0.40, delta=0.06)
            beyond = s["short"]["strike"] > s["long"]["strike"] if s["kind"] == "call" else \
                s["short"]["strike"] < s["long"]["strike"]
            self.assertTrue(beyond)


class MonthlyBudget(unittest.TestCase):                                                        # (b)
    def test_one_entry_per_month_and_none_open(self):
        ok, _ = engine.debit_entry_allowed(ST, "2025-05-03", None, {"2025-04"})
        self.assertTrue(ok)
        self.assertFalse(engine.debit_entry_allowed(ST, "2025-05-20", None, {"2025-05"})[0])
        self.assertFalse(engine.debit_entry_allowed(ST, "2025-06-01", "2025-06-10", set())[0])
        self.assertTrue(engine.debit_entry_allowed(ST, "2025-06-10", "2025-06-10", set())[0])

    def test_backtest_trades_respect_the_month_rule(self):
        res = json.loads((ROOT / "data" / "backtest_debit_spreads.json").read_text(encoding="utf-8"))
        for v in res["variants"]:
            months = [t["date"][:7] for t in v["trades"]]
            self.assertEqual(len(months), len(set(months)), v["name"])
            for a, b in zip(v["trades"], v["trades"][1:]):
                self.assertGreaterEqual(b["date"], a["exit_date"], v["name"])            # never two open at once

    def test_never_over_the_monthly_budget(self):
        t = engine.decide(CFG, ST, {"label": "CHEAP"}, CHAIN, "neutral")["trade"]
        for used in (0, 400, 999, 1000, 1500):
            cfg = copy.deepcopy(CFG)
            cfg["risk"]["budget_used_this_month_usd"] = used
            sz = engine.size_debit(cfg, ST, t)
            self.assertLessEqual(sz["cost_usd"], max(0, 1000 - used) + 1e-9, used)
            for side, z in zip(t["sides"], sz["sides"]):
                self.assertLessEqual(z["cost_usd"], max(0, 1000 - used) * side["share"] + 1e-9)


class NoStopTakeProfit(unittest.TestCase):                                                      # (c)
    def pos(self, kind="call", Kl=100.0, Ks=125.0, cost=4.0):
        exp = datetime(2025, 7, 25, 8, tzinfo=timezone.utc)
        return {"kind": kind, "Kl": Kl, "Ks": Ks, "cost": cost, "share": 1.0, "expiry": "2025-07-25",
                "expiry_ms": int(exp.timestamp() * 1000), "expiry_dt": exp}

    def leg(self, K, bid, ask, kind="call"):
        return {"type": kind, "strike": K, "expiry_ms": self.pos()["expiry_ms"], "bid": bid, "ask": ask,
                "mid": (bid + ask) / 2, "mark": (bid + ask) / 2}

    def test_max_loss_is_the_cost(self):
        why, value = engine.manage_debit(ST, self.pos(), "2025-07-26", 50.0, 0.6)       # expired far out of the money
        self.assertEqual((why, value), ("expiry", 0.0))
        self.assertEqual(value - self.pos()["cost"], -4.0)                               # lose exactly what was paid
        st = copy.deepcopy(ST)
        self.assertNotIn("stop_multiple", st["exits"])
        crash = [self.leg(100, 0.01, 0.02), self.leg(125, 0.0, 0.01)]
        self.assertIsNone(engine.manage_debit(ST, self.pos(), "2025-06-10", 60.0, 0.6, crash))   # no stop: holds

    def test_take_profit_at_2x(self):
        at_2x = [self.leg(100, 10.6, 11.0), self.leg(125, 2.0, 2.5)]   # value = 10.6 - 2.5 - fees, just above 8
        below = [self.leg(100, 9.0, 9.5), self.leg(125, 2.0, 2.5)]
        r = engine.manage_debit(ST, self.pos(), "2025-06-10", 108.0, 0.6, at_2x)
        self.assertEqual(r, ("take profit", 8.0))                    # a resting limit fills at exactly 2x the cost
        self.assertIsNone(engine.manage_debit(ST, self.pos(), "2025-06-10", 108.0, 0.6, below))

    def test_time_exit_10_days_before_expiry(self):
        q = [self.leg(100, 3.0, 3.2), self.leg(125, 0.1, 0.2)]
        self.assertIsNone(engine.manage_debit(ST, self.pos(), "2025-07-15", 100.0, 0.6, q))   # 10.3 days left
        self.assertEqual(engine.manage_debit(ST, self.pos(), "2025-07-16", 100.0, 0.6, q)[0], "time exit")
        t = engine.decide(CFG, ST, {"label": "CHEAP"}, CHAIN, "bullish")["trade"]
        plan = engine.exit_plan_debit(ST, t)                       # the printed date is the day the engine closes
        exp = datetime.fromtimestamp(t["sides"][0]["expiry_ms"] / 1000, timezone.utc)
        self.assertEqual(plan["sides"][0]["time_exit_date"], "2025-06-18")
        self.assertEqual(exp.date().isoformat(), "2025-06-27")


class Selection(unittest.TestCase):                                                            # (d)
    def var(self, name, is_n, is_total, hb_n=10, hb_total=-9999.0):
        sc = lambda n, t: {"trades": n, "total_per_1000": t, "dca_sol": {"per_1000": -300.0}}
        return {"name": name, "scorecard": {"in_sample": sc(is_n, is_total), "held_back": sc(hb_n, hb_total)}}

    def names(self):
        return ST["selection"]["candidates"]

    def test_uses_practice_period_only(self):
        a, b, c, d = self.names()
        vs = [self.var(a, 6, 100), self.var(b, 6, 500, hb_total=-5000), self.var(c, 6, 200, hb_total=9000),
              self.var(d, 6, 50)]
        self.assertEqual(backtest.select_version(ST, vs)["chosen"], b)        # best practice total, unseen ignored
        vs[2]["scorecard"]["held_back"]["total_per_1000"] = 1e9
        self.assertEqual(backtest.select_version(ST, vs)["chosen"], b)

    def test_five_trade_fallback(self):
        a, b, c, d = self.names()
        vs = [self.var(a, 6, 100), self.var(b, 4, 500), self.var(c, 6, 200), self.var(d, 6, 50)]
        sel = backtest.select_version(ST, vs)
        self.assertEqual(sel["chosen"], ST["selection"]["fallback"])
        self.assertIn("fewer than 5", sel["reason"])
        vs[1]["scorecard"]["in_sample"]["trades"] = 5
        self.assertEqual(backtest.select_version(ST, vs)["chosen"], b)

    def test_ties_go_to_the_earlier_candidate(self):
        a, b, c, d = self.names()
        vs = [self.var(a, 6, 100), self.var(b, 6, 300), self.var(c, 6, 300), self.var(d, 6, 50)]
        self.assertEqual(backtest.select_version(ST, vs)["chosen"], b)


class PassRule(unittest.TestCase):                                                             # (e)
    def check(self, n, total, dca=-300.0):
        v = {"name": "x", "scorecard": {"held_back": {"trades": n, "total_per_1000": total,
                                                      "dca_sol": {"per_1000": dca}}}}
        return backtest.pass_check(ST, [v], "x")

    def test_thresholds(self):
        self.assertTrue(self.check(8, 1.0)["passed"])
        self.assertFalse(self.check(7, 1.0)["passed"])                      # 8 trades needed
        self.assertFalse(self.check(8, 0.0)["passed"])                      # total must be above 0
        self.assertEqual(self.check(8, 0.0)["status"], "in_testing")

    def test_beats_weekly_buying_with_the_same_money(self):
        self.assertFalse(self.check(8, 100.0, dca=20.0)["passed"])          # weekly buying: 8 x +20 = +160
        self.assertTrue(self.check(8, 161.0, dca=20.0)["passed"])
        self.assertEqual(self.check(8, 161.0, dca=20.0)["weekly_same_money"], 160.0)

    def test_real_result_matches_rule(self):
        res = json.loads((ROOT / "data" / "backtest_debit_spreads.json").read_text(encoding="utf-8"))
        p, sel = res["pass"], res["selection"]
        v = next(x for x in res["variants"] if x["name"] == sel["chosen"])["scorecard"]["held_back"]
        self.assertEqual(p["trades"], v["trades"])
        self.assertEqual(p["passed"], v["trades"] >= 8 and v["total_per_1000"] > 0 and
                         v["total_per_1000"] > v["trades"] * v["dca_sol"]["per_1000"])


class PaperLog(unittest.TestCase):                                                             # (f)
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.log = self.d / "paper_trades.csv"

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def debit(self, reading="neutral"):
        t = engine.decide(CFG, ST, {"label": "CHEAP"}, CHAIN, reading)["trade"]
        sz, plan = engine.size_debit(CFG, ST, t), engine.exit_plan_debit(ST, t)
        return {"status": "in_testing", "trade": True, "reason": None,
                "view": engine.debit_view(ST, t, sz, plan, 150.0), "positions": engine.open_debit(ST, t, 150.0)}

    def test_once_per_day_never_rewritten(self):
        self.assertEqual(len(paper.record_day("2025-05-01", self.debit(), ST, 150.0, 0.6, CHAIN, self.log)), 1)
        before = self.log.read_bytes()
        self.assertEqual(paper.record_day("2025-05-01", self.debit("bullish"), ST, 150.0, 0.6, CHAIN, self.log), [])
        self.assertEqual(self.log.read_bytes(), before)
        rows = paper.read(self.log)
        self.assertEqual([r["event"] for r in rows], ["open"])
        self.assertEqual(paper.state(rows)[0], "9999-12-31")                          # a paper trade is open

    def test_closes_are_appended(self):
        paper.record_day("2025-05-01", self.debit(), ST, 150.0, 0.6, CHAIN, self.log)
        before = self.log.read_bytes()
        no = {"status": "in_testing", "trade": False, "reason": "IV is RICH; this strategy is for CHEAP days."}
        new = paper.record_day("2025-07-01", no, ST, 230.0, 0.6, None, self.log)         # big rally: call side doubles
        self.assertTrue(self.log.read_bytes().startswith(before))
        self.assertIn("close", [r["event"] for r in new])
        self.assertEqual(new[-1]["event"], "no_entry")

    def test_sample_runs_record_nothing(self):
        saved = (main.PAPER_LOG, main.VIEW_HISTORY, main.DIRECTION_HISTORY, main.CHAINS_DIR)
        try:
            main.use_sample_paths()
            self.assertIsNone(main.PAPER_LOG)
        finally:
            main.PAPER_LOG, main.VIEW_HISTORY, main.DIRECTION_HISTORY, main.CHAINS_DIR = saved


class Reproducible(unittest.TestCase):                                                         # (g)
    def test_byte_identical(self):
        hist = golden_tools.BACKTEST_HISTORY
        a = backtest.dumps(backtest.run_all(ROOT / "strategies" / "debit_spreads.toml", quiet=True, history_path=hist,
                                            chains_dir=golden_tools.NO_CHAINS))
        b = backtest.dumps(backtest.run_all(ROOT / "strategies" / "debit_spreads.toml", quiet=True, history_path=hist,
                                            chains_dir=golden_tools.NO_CHAINS))
        self.assertEqual(a, b)
        out = json.loads(a)
        self.assertIn("strategies/direction.toml", out["inputs"])
        self.assertEqual([v["name"] for v in out["variants"]], [v["name"] for v in ST["variants"]])

    def test_output_files(self):
        self.assertEqual(backtest.out_path(ROOT / "strategies" / "debit_spreads.toml").name,
                         "backtest_debit_spreads.json")
        self.assertEqual(backtest.out_path(None), backtest.OUT)


if __name__ == "__main__":
    unittest.main()


class ExpiryCalendar(unittest.TestCase):
    """v2: the model chain lists only the expiries Deribit really lists for SOL."""
    CAL = ST["backtest"]["calendar"]

    def test_matches_every_stored_chain(self):
        """Month-end and quarter-end expiries (the ones 23-113 day trades use) must match Deribit exactly on every
        stored day. Weeklies: Deribit adds them irregularly (from 2026-11-21 to 11-26 it listed only two Fridays,
        never the one 15 days out), so the model's weeklies (up to 15 days) must contain every real weekly but may
        include one Deribit has not listed yet. Debit spreads never use an expiry under 23 days, so that cannot
        change their results."""
        import chain_store
        import vol_gauge
        from datetime import date, timedelta
        for p in sorted((ROOT / "data" / "chains").glob("*.json.gz")):
            day = p.name[:10]
            d0 = date.fromisoformat(day)
            real = {o["expiry"] for o in vol_gauge.build_chain(chain_store.load(day))
                    if date.fromisoformat(o["expiry"]).weekday() == 4 and o["expiry"] > day}
            model = {d.isoformat() for d in engine.listed_expiries(d0, self.CAL)}
            far = (d0 + timedelta(days=int(self.CAL["weekly_ahead_days"]))).isoformat()
            self.assertEqual({e for e in model if e > far}, {e for e in real if e > far}, day)   # month/quarter ends
            self.assertTrue({e for e in real if e <= far} <= model, day)                          # every real weekly
            self.assertLessEqual(len({e for e in model if e <= far} - real), 1, day)              # at most one extra

    def test_month_and_quarter_ends(self):
        from datetime import date
        got = [d.isoformat() for d in engine.listed_expiries(date(2025, 5, 1), self.CAL)]
        self.assertEqual(got, ["2025-05-02", "2025-05-09", "2025-05-16", "2025-05-30", "2025-06-27", "2025-09-26"])

    def test_model_chain_uses_only_listed_expiries(self):
        from datetime import date
        listed = {d.isoformat() for d in engine.listed_expiries(date(2025, 5, 1), self.CAL)}
        self.assertEqual({o["expiry"] for o in CHAIN}, listed)

    def test_put_spread_keeps_every_friday(self):
        put = engine.load_strategy()
        self.assertNotIn("calendar", put["backtest"])
        exps = sorted({o["expiry"] for o in engine.synthetic_chain(put, "2025-05-01", 150.0, 0.6)})
        self.assertEqual(len(exps), 10)                                   # every Friday out to 70 days, as before


class HeldInTesting(unittest.TestCase):
    def test_go_live_false_holds_a_pass(self):
        st = copy.deepcopy(ST)
        v = {"name": "x", "scorecard": {"held_back": {"trades": 9, "total_per_1000": 500.0,
                                                      "dca_sol": {"per_1000": -300.0}}}}
        p = backtest.pass_check(st, [v], "x")
        self.assertTrue(p["passed"])
        self.assertEqual(p["status"], "in_testing")                       # go_live = false (owner, 2026-11-21)
        self.assertIn("held \"in testing\"", p["text"])
        st["pass_rule"]["go_live"] = True
        self.assertEqual(backtest.pass_check(st, [v], "x")["status"], "live")

    def test_daily_status_is_in_testing(self):
        cfg = main.load_config()
        d, _ = main.debit_today(cfg, {"label": "CHEAP"}, CHAIN, 150.0, "2025-05-01", "neutral")
        self.assertEqual(d["status"], "in_testing")
