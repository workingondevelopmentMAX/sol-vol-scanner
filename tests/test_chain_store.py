"""Step 2 tests: stored daily chains, input fingerprints, byte-identical backtests, and a locked backtest slice."""
import gzip
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
import chain_store  # noqa: E402
import engine  # noqa: E402
import golden_tools  # noqa: E402
import main  # noqa: E402
import suggest  # noqa: E402
import vol_gauge  # noqa: E402

SAMPLE = json.loads((ROOT / "data" / "sample" / "snapshot.json").read_text(encoding="utf-8"))


def raw_from_chain(chain: list[dict]) -> list[dict]:
    """Turn engine-shaped options back into Deribit-shaped records (to build stored-chain test files)."""
    return [{"instrument_name": o["name"], "strike": o["strike"], "option_type": o["type"],
             "expiration_timestamp": o["expiry_ms"], "contract_size": o["contract_size"],
             "min_trade_amount": o["min_trade_amount"], "bid_price": o["bid"], "ask_price": o["ask"],
             "mark_price": o["mark"], "mark_iv": o["iv"] * 100, "open_interest": o["oi"],
             "underlying_price": o["F"]} for o in chain]


def snap_for(day: str, spot: float, options: list[dict]) -> dict:
    return {"fetched_at": f"{day}T00:00:00+00:00", "index_price": spot,
            "instruments": options, "book_summary": options}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class Store(Tmp):
    def test_round_trip_gives_the_same_chain(self):
        p = chain_store.save(SAMPLE, self.dir)
        self.assertEqual(p.name, "2026-11-15.json.gz")
        back = chain_store.load("2026-11-15", self.dir)
        by_name = lambda c: sorted(c, key=lambda o: o["name"])
        self.assertEqual(by_name(vol_gauge.build_chain(back)), by_name(vol_gauge.build_chain(SAMPLE)))
        cfg = main.load_config(local=False)
        rich = {"label": "RICH"}
        live = engine.decide(cfg, cfg["_strategy"], rich, vol_gauge.build_chain(SAMPLE))["trade"]
        stored = engine.decide(cfg, cfg["_strategy"], rich, vol_gauge.build_chain(back))["trade"]
        self.assertEqual(suggest.strip(stored), suggest.strip(live))   # same suggestion from the stored file

    def test_same_snapshot_same_bytes(self):
        a = chain_store.encode(chain_store.compact(SAMPLE))
        b = chain_store.encode(chain_store.compact(json.loads(json.dumps(SAMPLE))))
        self.assertEqual(a, b)
        self.assertEqual(a[4:8], b"\x00\x00\x00\x00")          # no timestamp in the gzip header

    def test_first_snapshot_of_the_day_is_permanent(self):
        p = chain_store.save(SAMPLE, self.dir)
        before = p.read_bytes()
        later = dict(SAMPLE, fetched_at="2026-11-15T13:00:00+00:00", index_price=1.0)
        self.assertIsNone(chain_store.save(later, self.dir))
        self.assertEqual(p.read_bytes(), before)

    def test_only_sol_options_and_engine_fields(self):
        other = {"instrument_name": "BTC_USDC-27NOV26-90000-P", "bid_price": 1, "ask_price": 2}
        snap = dict(SAMPLE, book_summary=SAMPLE["book_summary"] + [other])
        data = json.loads(gzip.decompress(chain_store.encode(chain_store.compact(snap))))
        names = [o["instrument_name"] for o in data["options"]]
        self.assertTrue(all(n.startswith("SOL_USDC-") for n in names))
        self.assertEqual(set(data["options"][0]), set(chain_store.FIELDS))
        self.assertEqual(len(names), len(SAMPLE["book_summary"]))

    def test_sample_runs_store_nothing(self):
        old = main.CHAINS_DIR
        try:
            main.use_sample_paths()
            self.assertIsNone(main.CHAINS_DIR)
        finally:
            main.CHAINS_DIR = old


class BacktestUsesStoredChains(Tmp):
    DAY = "2024-06-14"      # first rich-day trade of the golden slice: model picks the 126/113 put spread

    def setUp(self):
        super().setUp()
        self.cfg = main.load_config(local=False)
        self.st = self.cfg["_strategy"]
        self.days = backtest.load_days(self.cfg, golden_tools.BACKTEST_HISTORY)

    def first_trade(self):
        trades = backtest.run(self.cfg, self.st, self.days, True, self.dir)
        return next(t for t in trades if t["date"] == self.DAY)

    def test_model_when_nothing_stored(self):
        t = self.first_trade()
        self.assertEqual((t["Ks"], t["chain"]), (126.0, "model"))

    def test_stored_chain_replaces_the_model(self):
        d = next(x for x in self.days if x["date"] == self.DAY)
        chain = [o for o in engine.synthetic_chain(self.st, self.DAY, d["S"], d["iv"]) if o["strike"] != 126.0]
        chain_store.save(snap_for(self.DAY, d["S"], raw_from_chain(chain)), self.dir)
        t = self.first_trade()
        self.assertEqual(t["chain"], "stored")
        self.assertNotEqual(t["Ks"], 126.0)                 # the strike missing from the stored chain is never used
        inputs, _ = backtest.input_files(engine.DEFAULT_STRATEGY, golden_tools.BACKTEST_HISTORY, self.dir, self.days)
        self.assertEqual([k for k in inputs if k.endswith(".json.gz")], [f"{self.DAY}.json.gz"])


class ManageWithRealQuotes(unittest.TestCase):
    def test_real_quotes_are_used_when_both_legs_quoted(self):
        st = engine.load_strategy()
        exp = datetime(2026, 12, 25, 8, tzinfo=timezone.utc)
        pos = {"Ks": 64.0, "Kl": 58.0, "credit": 1.2, "expiry_ms": int(exp.timestamp() * 1000), "expiry_dt": exp,
               "stop_spot": 1.0, "stop_mark": 99.0}
        leg = lambda k, bid, ask: {"type": "put", "strike": k, "expiry_ms": pos["expiry_ms"], "bid": bid,
                                   "ask": ask, "mid": (bid + ask) / 2, "mark": (bid + ask) / 2}
        chain = [leg(64.0, 0.30, 0.34), leg(58.0, 0.08, 0.10)]
        cost, mark = engine.real_buyback_cost(st, pos, 80.0, chain)
        self.assertAlmostEqual(mark, 0.32)
        self.assertAlmostEqual(cost, 0.34 - 0.08 + engine.fee(st["backtest"], 80.0, 0.32)
                               + engine.fee(st["backtest"], 80.0, 0.09))
        why, pnl = engine.manage(st, pos, "2026-11-20", 80.0, 0.65, chain)
        self.assertEqual(why, "take profit")                 # bought back for well under half the credit
        self.assertIsNone(engine.real_buyback_cost(st, pos, 80.0, chain[:1]))   # a missing leg -> model


class Reproducible(unittest.TestCase):
    def test_backtest_output_byte_identical_and_fingerprinted(self):
        a = backtest.dumps(backtest.run_all(quiet=True, history_path=golden_tools.BACKTEST_HISTORY,
                                            chains_dir=golden_tools.NO_CHAINS))
        b = backtest.dumps(backtest.run_all(quiet=True, history_path=golden_tools.BACKTEST_HISTORY,
                                            chains_dir=golden_tools.NO_CHAINS))
        self.assertEqual(a, b)
        inputs = json.loads(a)["inputs"]
        self.assertEqual(set(inputs), {"config.toml", "strategies/bull_put_spread.toml",
                                       "tests/golden/backtest_history.csv"})
        self.assertTrue(all(len(h) == 64 for h in inputs.values()))
        self.assertEqual(inputs["tests/golden/backtest_history.csv"],
                         chain_store.sha256(golden_tools.BACKTEST_HISTORY))


class GoldenBacktest(unittest.TestCase):
    def test_backtest_slice_unchanged(self):
        """Fails if any code or rule change alters the backtest on the frozen 2024-04 to 2025-12 slice.
        After a deliberate change: python tests/golden_tools.py --update, and commit the new golden files."""
        want = golden_tools.BACKTEST_GOLDEN.read_text(encoding="utf-8")
        self.assertEqual(golden_tools.dump(golden_tools.backtest_slice()), want)


if __name__ == "__main__":
    unittest.main()
