"""Read-only access to Deribit's public market-data API.

There is deliberately no code here (or anywhere in this project) that can
authenticate or send an order: only public/* GET methods are called.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "logs" / "raw"
SAMPLE_FILE = ROOT / "data" / "sample" / "snapshot.json"

ALLOWED_PREFIX = "public/"


class DeribitError(RuntimeError):
    pass


class Client:
    def __init__(self, cfg: dict, base_url: str | None = None):
        d = cfg["deribit"]
        self.base = (base_url or d["base_url"]).rstrip("/") + "/"
        self.timeout = float(d.get("timeout_seconds", 15))
        self.min_gap = float(d.get("min_seconds_between_calls", 0.15))
        self._last = 0.0
        self.calls = 0

    def get(self, method: str, retries: int = 3, **params):
        """Call a public method, e.g. get('get_index_price', index_name='sol_usdc')."""
        name = method[len(ALLOWED_PREFIX):] if method.startswith(ALLOWED_PREFIX) else method
        if "/" in name:  # belt and braces: only public market-data methods, nothing authenticated
            raise DeribitError("Only public methods are allowed")
        clean = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items() if v is not None}
        url = self.base + name + ("?" + urllib.parse.urlencode(clean) if clean else "")
        delay = 1.0
        last_err = None
        for attempt in range(retries + 1):
            wait = self.min_gap - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "sol-vol-scanner/0.1 (read-only)"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                if "error" in body:
                    raise DeribitError(f"{name}: {body['error']}")
                return body["result"]
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 429 or e.code >= 500:  # rate limited / server busy -> back off
                    time.sleep(delay)
                    delay *= 2
                    continue
                try:
                    detail = e.read().decode("utf-8")[:300]
                except Exception:
                    detail = ""
                raise DeribitError(f"{name}: HTTP {e.code} {detail}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(delay)
                delay *= 2
        raise DeribitError(f"{name}: failed after {retries + 1} attempts ({last_err})")


def _cache(name: str, payload) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    (RAW_DIR / f"{stamp}_{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def prune_cache(keep_days: int) -> None:
    if not RAW_DIR.exists():
        return
    cutoff = time.time() - keep_days * 86400
    for p in RAW_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


def fetch_snapshot(cfg: dict) -> dict:
    """Everything one daily run needs, in one dict (same shape as the sample file)."""
    c = Client(cfg)
    d = cfg["deribit"]
    prefix = d["instrument_prefix"]
    now = datetime.now(timezone.utc)

    index = c.get("get_index_price", index_name=d["index_name"])
    instruments = [i for i in c.get("get_instruments", currency="USDC", kind="option", expired=False)
                   if i.get("instrument_name", "").startswith(prefix)]
    book = [b for b in c.get("get_book_summary_by_currency", currency="USDC", kind="option")
            if b.get("instrument_name", "").startswith(prefix)]
    start = int((now - timedelta(days=400)).timestamp() * 1000)
    end = int(now.timestamp() * 1000)
    chart = c.get("get_tradingview_chart_data", instrument_name=d["perp_instrument"],
                  start_timestamp=start, end_timestamp=end, resolution="1D")
    try:
        delivery = c.get("get_delivery_prices", index_name=d["index_name"], count=120)
    except DeribitError:
        delivery = {"data": []}

    snap = {
        "fetched_at": now.isoformat(),
        "index_price": index["index_price"],
        "instruments": instruments,
        "book_summary": book,
        "chart": chart,
        "delivery_prices": delivery,
    }
    _cache("snapshot", snap)
    prune_cache(int(d.get("keep_raw_cache_days", 14)))
    if not instruments or not book:
        raise DeribitError("Deribit returned no SOL options - instrument prefix may have changed")
    return snap


def load_sample() -> dict:
    return json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))


def fetch_trades_for_day(cfg: dict, client: Client, day_start: datetime) -> tuple[list, int]:
    """Public USDC option trades for one UTC day, filtered to SOL. Each trade carries its 'iv'.

    The day is split into slices so a busy morning of BTC/ETH trades can't crowd out the rest of the day.
    Returns (sol_trades, total_trades_scanned).
    """
    b = cfg["backfill"]
    prefix = cfg["deribit"]["instrument_prefix"]
    n = int(b.get("windows_per_day", 4))
    out, scanned = [], 0
    for w in range(n):
        start = int((day_start + timedelta(days=w / n)).timestamp() * 1000)
        end = int((day_start + timedelta(days=(w + 1) / n)).timestamp() * 1000) - 1
        for _ in range(int(b.get("max_pages_per_window", 5))):
            res = client.get("get_last_trades_by_currency_and_time", currency="USDC", kind="option",
                             start_timestamp=start, end_timestamp=end, count=1000, sorting="asc")
            trades = res.get("trades", []) if isinstance(res, dict) else []
            scanned += len(trades)
            out.extend(t for t in trades if t.get("instrument_name", "").startswith(prefix))
            if not trades or not res.get("has_more"):
                break
            start = trades[-1]["timestamp"] + 1
    return out, scanned
