"""Stored daily option chains: one compressed file per day, kept permanently.

    data/chains/YYYY-MM-DD.json.gz    SOL options only, only the fields the engine reads

The daily scan saves the first chain it sees each UTC day (the 01:07 UTC run = 09:07 Singapore) and never
overwrites it, so a stored day never changes. The backtest reads these files back (through
vol_gauge.build_chain, exactly as a live run would) instead of rebuilding that day from a model.

Files are written byte-for-byte reproducibly (sorted keys, fixed gzip header), so the same snapshot always
gives the same file and the same SHA-256 fingerprint. Nothing here touches the network.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHAINS_DIR = ROOT / "data" / "chains"
FORMAT = 1
PREFIX = "SOL_USDC-"

# Everything vol_gauge.build_chain() reads from Deribit's instrument list and book summary.
FIELDS = ("instrument_name", "strike", "option_type", "expiration_timestamp", "contract_size", "min_trade_amount",
          "bid_price", "ask_price", "mark_price", "mark_iv", "open_interest", "underlying_price")


def day_of(snap: dict) -> str:
    return datetime.fromisoformat(snap["fetched_at"]).astimezone(timezone.utc).strftime("%Y-%m-%d")


def path_for(day: str, chains_dir: Path | str = CHAINS_DIR) -> Path:
    return Path(chains_dir) / f"{day}.json.gz"


def compact(snap: dict, prefix: str = PREFIX) -> dict:
    """The stored form of a snapshot: one merged record per SOL option, sorted by name."""
    meta = {i["instrument_name"]: i for i in snap.get("instruments", [])}
    options = []
    for b in snap.get("book_summary", []):
        name = b.get("instrument_name", "")
        if not name.startswith(prefix):
            continue
        merged = {**meta.get(name, {}), **b}
        options.append({k: merged.get(k) for k in FIELDS})
    options.sort(key=lambda o: o["instrument_name"])
    return {"format": FORMAT, "fetched_at": snap["fetched_at"], "index_price": snap["index_price"],
            "options": options}


def encode(data: dict) -> bytes:
    """Same data -> same bytes, always (no timestamp or file name in the gzip header)."""
    raw = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as g:
        g.write(raw)
    return buf.getvalue()


def save(snap: dict, chains_dir: Path | str = CHAINS_DIR, prefix: str = PREFIX) -> Path | None:
    """Store today's chain once. Returns the new file, or None if this day is already stored (or empty)."""
    data = compact(snap, prefix)
    if not data["options"]:
        return None
    p = path_for(day_of(snap), chains_dir)
    if p.exists():
        return None                      # the first snapshot of the day is the permanent one
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(encode(data))
    tmp.replace(p)
    return p


def load(day: str, chains_dir: Path | str = CHAINS_DIR) -> dict | None:
    """A stored day in the same shape as a live snapshot (for vol_gauge.build_chain), or None."""
    p = path_for(day, chains_dir)
    if not p.exists():
        return None
    data = json.loads(gzip.decompress(p.read_bytes()).decode("utf-8"))
    return {"fetched_at": data["fetched_at"], "index_price": data["index_price"],
            "instruments": data["options"], "book_summary": data["options"]}


# ------------------------------------------------------------------ fingerprints
def sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprints(paths, root: Path = ROOT) -> dict:
    """{path relative to the repository: SHA-256}, sorted by path."""
    out = {}
    for p in paths:
        p = Path(p)
        try:
            key = p.resolve().relative_to(root).as_posix()
        except ValueError:
            key = p.name
        out[key] = sha256(p)
    return dict(sorted(out.items()))


def combined(fps: dict) -> str:
    """One short fingerprint for a whole set of input files."""
    text = "".join(f"{k}  {v}\n" for k, v in sorted(fps.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
