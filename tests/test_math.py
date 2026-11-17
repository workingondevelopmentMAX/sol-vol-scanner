"""Unit tests on fixed numbers. Run from the project folder:  python -m unittest discover tests"""
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bsmodel  # noqa: E402
import suggest  # noqa: E402
import vol_gauge  # noqa: E402

CFG = {
    "vol": {"iv_percentile_min": 50, "iv_percentile_cheap": 20, "iv_rv_ratio_min": 1.2,
            "iv_rv_ratio_cheap": 0.9, "min_history_days": 60, "target_dte": 30},
    "risk": {"max_loss_per_trade_usd": 200, "max_total_spread_risk_usd": 600, "current_open_risk_usd": 0},
}


class RealisedVol(unittest.TestCase):
    def test_constant_growth_has_zero_vol(self):
        closes = [100 * 1.01 ** i for i in range(31)]
        self.assertAlmostEqual(vol_gauge.realised_vol(closes, 30), 0.0, places=12)

    def test_alternating_returns(self):
        # returns alternate +r, -r: sample stdev = r * sqrt(n/(n-1))
        r, closes = 0.02, [100.0]
        for i in range(20):
            closes.append(closes[-1] * math.exp(r if i % 2 == 0 else -r))
        expected = r * math.sqrt(20 / 19) * math.sqrt(365)
        self.assertAlmostEqual(vol_gauge.realised_vol(closes, 20), expected, places=10)

    def test_not_enough_data(self):
        self.assertIsNone(vol_gauge.realised_vol([1, 2, 3], 20))


class Percentile(unittest.TestCase):
    def test_rank(self):
        self.assertEqual(vol_gauge.percentile_rank(5, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), 50.0)
        self.assertEqual(vol_gauge.percentile_rank(11, list(range(1, 11))), 100.0)

    def test_verdict_bootstrap_uses_ratio(self):
        v = vol_gauge.verdict(CFG, 0.72, 0.55, prior_ivs=[0.6] * 10)
        self.assertEqual(v["mode"], "ratio")
        self.assertEqual(v["label"], "RICH")  # 0.72/0.55 = 1.31 >= 1.2

    def test_verdict_percentile_mode(self):
        prior = [0.4 + 0.005 * i for i in range(100)]  # 0.40 .. 0.895
        self.assertEqual(vol_gauge.verdict(CFG, 0.80, 0.5, prior)["label"], "RICH")
        self.assertEqual(vol_gauge.verdict(CFG, 0.42, 0.5, prior)["label"], "CHEAP")
        self.assertEqual(vol_gauge.verdict(CFG, 0.60, 0.5, prior)["label"], "FAIR")

    def test_iv_units(self):
        self.assertAlmostEqual(vol_gauge.normalize_iv(65.0), 0.65)
        self.assertAlmostEqual(vol_gauge.normalize_iv(0.65), 0.65)
        self.assertIsNone(vol_gauge.normalize_iv(None))


class SpreadMaths(unittest.TestCase):
    def test_hand_calculation(self):
        # Sell 90 put, buy 80 put, collect $2 -> max loss 8, break-even 88 (the playbook example)
        m = suggest.spread_math(90, 80, 2.0)
        self.assertEqual((m["width"], m["max_profit"], m["max_loss"], m["break_even"]), (10, 2.0, 8.0, 88.0))

    def test_payoff(self):
        self.assertEqual(suggest.payoff_at_expiry(90, 80, 2, 95), 2)
        self.assertEqual(suggest.payoff_at_expiry(90, 80, 2, 88), 0)
        self.assertEqual(suggest.payoff_at_expiry(90, 80, 2, 50), -8)

    def test_sizing(self):
        cand = {"max_loss": 8.0, "contract_size": 1.0, "credit_conservative": 2.0}
        s = suggest.size(CFG, cand)
        self.assertEqual(s["contracts"], 25)          # floor(200 / 8)
        self.assertEqual(s["usd_at_risk"], 200.0)

    def test_sizing_respects_total_cap(self):
        cfg = {"risk": {"max_loss_per_trade_usd": 200, "max_total_spread_risk_usd": 600, "current_open_risk_usd": 560}}
        s = suggest.size(cfg, {"max_loss": 8.0, "contract_size": 10.0, "credit_conservative": 2.0})
        self.assertEqual(s["contracts"], 0)            # 40 left, one contract risks 80
        self.assertIsNotNone(s["note"])


class Model(unittest.TestCase):
    def test_put_call_parity(self):
        F, K, T, v = 130, 120, 30 / 365, 0.65
        c, p = bsmodel.price(F, K, T, v, "call"), bsmodel.price(F, K, T, v, "put")
        self.assertAlmostEqual(c - p, F - K, places=10)

    def test_put_delta_range(self):
        d = bsmodel.greeks(130, 110, 30 / 365, 0.65, "put")["delta"]
        self.assertTrue(-0.5 < d < 0)


class Conviction(unittest.TestCase):
    S = {"dte": 21, "break_even": 100.0, "credit_pct_of_width": 22, "liquidity": {"worst_spread_pct": 4, "min_oi": 1000}}

    def test_strong_setup_scores_high(self):
        import rationale
        v = {"mode": "percentile", "percentile": 92, "atm_iv": 0.7, "history_days": 150}
        c = rationale.conviction({}, v, self.S, 130.0)
        self.assertEqual(c["label"], "High")
        self.assertEqual(c["score"], 100)

    def test_bootstrap_is_capped_at_medium(self):
        import rationale
        v = {"mode": "ratio", "iv_rv_ratio": 2.0, "atm_iv": 0.7, "history_days": 150}
        c = rationale.conviction({}, v, self.S, 130.0)
        self.assertLessEqual(c["score"], 69)
        self.assertNotEqual(c["label"], "High")


class ExitPlan(unittest.TestCase):
    def test_levels_are_sensible(self):
        import exits
        leg = lambda k, iv: {"name": f"P{k}", "strike": k, "F": 100.0, "T": 30 / 365, "iv": iv}
        cand = {"short": leg(90, 0.70), "long": leg(80, 0.75), "credit_conservative": 1.5, "width": 10,
                "max_loss": 8.5, "contract_size": 1.0, "expiry": "2026-12-25", "prob_profit_approx": 0.78}
        cfg = {"exits": {"take_profit_pct": 50, "stop_multiple": 1.5, "iv_points_per_pct_drop": 1.0,
                         "stop_assumed_days": 3, "time_exit_dte": 7}}
        p = exits.plan(cfg, cand, {"contracts": 2}, 100.0)
        self.assertEqual(p["take_profit_price"], 0.75)
        self.assertLess(p["price_stop_spot"], 100.0)
        self.assertGreater(p["price_stop_spot"], 80.0)
        self.assertGreaterEqual(p["price_stop_spot"], p["price_stop_spot_no_vol"])  # vol jump -> earlier exit
        self.assertAlmostEqual(p["loss_at_stop_usd"], 1.5 * 1.5 * 2)                 # 1.5x credit x 2 SOL
        self.assertAlmostEqual(p["breakeven_win_rate"], 2.25 / (0.75 + 2.25))
        self.assertEqual(p["time_exit_date"], "2026-12-18")


class Dvol(unittest.TestCase):
    def test_scaled_fill_keeps_own_readings(self):
        rows = [{"date": f"2026-10-{d:02d}", "atm_iv_30d": 0.6, "iv_source": "snapshot"} for d in range(1, 26)]
        dv = [(f"2026-09-{d:02d}", 70.0) for d in range(1, 31)] + [(f"2026-10-{d:02d}", 75.0) for d in range(1, 26)]
        out, info = vol_gauge.merge_dvol(rows, dv)
        by = {r["date"]: r for r in out}
        self.assertAlmostEqual(info["ratio"], 0.8)
        self.assertAlmostEqual(by["2026-09-05"]["atm_iv_30d"], 0.56)
        self.assertEqual(by["2026-09-05"]["iv_source"], "dvol_scaled")
        self.assertEqual(by["2026-10-05"]["atm_iv_30d"], 0.6)       # own reading untouched

    def test_too_little_overlap_fills_nothing(self):
        rows = [{"date": "2026-10-01", "atm_iv_30d": 0.6, "iv_source": "snapshot"}]
        out, info = vol_gauge.merge_dvol(rows, [("2026-09-01", 70.0), ("2026-10-01", 75.0)])
        self.assertEqual(info["filled"], 0)

    def test_fetch_never_raises(self):
        import data_fetch
        from datetime import datetime, timezone
        class Bad:
            def get(self, *a, **k):
                raise data_fetch.DeribitError("currency not supported")
        r = data_fetch.fetch_dvol({}, Bad(), datetime.now(timezone.utc))
        self.assertFalse(r["available"])


class Window(unittest.TestCase):
    def test_old_regime_does_not_count(self):
        # 100 very high-IV days two years ago, then 70 calmer days recently: today's shading uses the recent year only
        from datetime import date, timedelta
        rows = [{"date": (date(2024, 6, 1) + timedelta(days=i)).isoformat(), "atm_iv_30d": 1.0, "rv30": 0.8} for i in range(100)]
        rows += [{"date": (date(2026, 8, 1) + timedelta(days=i)).isoformat(), "atm_iv_30d": 0.40 + 0.003 * i, "rv30": 0.5} for i in range(70)]
        out = vol_gauge.annotate_history(CFG, rows)
        self.assertEqual(out[-1]["verdict"], "RICH")   # highest of the recent 70 days


class NoOrderCode(unittest.TestCase):
    def test_no_private_methods_anywhere(self):
        for p in ROOT.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            for bad in ("private/", "/buy", "/sell", "client_secret", "access_token"):
                self.assertNotIn(bad, text, f"{p.name} contains {bad!r}")


if __name__ == "__main__":
    unittest.main()
