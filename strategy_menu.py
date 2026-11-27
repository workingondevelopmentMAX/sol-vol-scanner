"""The strategy menu (strategies/menu.toml) and today's status for each entry. DISPLAY ONLY.

Every decision still comes from engine.decide() for the strategies the daily scan runs; this module only turns
those results and today's vol verdict into one status line per menu entry, from fixed templates (no LLM).

    suggested     a live strategy has an idea today (or, for "IV fair: no options trade", today is FAIR)
    no_trade      the strategy's market state is today's, but nothing passed its filters
    not_eligible  today's market state is not the one the strategy is for
    not_built     not built (or still being tested) yet
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MENU = ROOT / "strategies" / "menu.toml"

MARKETS = {"rich", "cheap", "fair", "any"}
STATUSES = {"live", "in_testing", "not_built"}
SIDES = {"call", "put", "both"}
BUDGETS = {"premium caps", "big-move budget (1% of portfolio per month)", "core-holding share", "owner-set", "none"}
JUDGED_ON = {"standard", "hedge", "accumulation", "none"}
FIELDS = {"id", "name", "market", "purpose", "budget", "status", "file", "roadmap_step", "judged_on", "side"}


def load(path: Path | str = MENU) -> list[dict]:
    """The menu entries, in file order. Refuses typos so a mistake can never silently hide a strategy."""
    entries = tomllib.loads(Path(path).read_text(encoding="utf-8")).get("strategy", [])
    seen = set()
    for e in entries:
        where = f"menu entry {e.get('id')!r}"
        bad = set(e) - FIELDS
        if bad:
            raise ValueError(f"{where}: unknown field(s) {sorted(bad)}")
        for k in ("id", "name", "market", "purpose", "budget", "status"):
            if not e.get(k):
                raise ValueError(f"{where}: missing {k}")
        if e["id"] in seen:
            raise ValueError(f"{where}: duplicate id")
        seen.add(e["id"])
        if e["market"] not in MARKETS:
            raise ValueError(f"{where}: market must be one of {sorted(MARKETS)}")
        if e["status"] not in STATUSES:
            raise ValueError(f"{where}: status must be one of {sorted(STATUSES)}")
        if e.get("judged_on", "standard") not in JUDGED_ON:
            raise ValueError(f"{where}: judged_on must be one of {sorted(JUDGED_ON)}")
        if e["budget"] not in BUDGETS:
            raise ValueError(f"{where}: budget must be one of {sorted(BUDGETS)}")
        if e.get("file") and not (Path(path).parent / e["file"]).exists():
            raise ValueError(f"{where}: strategy file {e['file']} does not exist")
        if e.get("side") and e["side"] not in SIDES:
            raise ValueError(f"{where}: side must be one of {sorted(SIDES)}")
        if e["status"] != "live" and not e.get("roadmap_step"):
            raise ValueError(f"{where}: an entry that is not live needs a roadmap_step")
    if not entries:
        raise ValueError("the strategy menu is empty")
    return entries


def _today(verdict_label: str | None) -> str | None:
    v = (verdict_label or "").lower()
    return v if v in ("rich", "cheap", "fair") else None


def statuses(entries: list[dict], verdict_label: str | None, ran: dict) -> list[dict]:
    """One status per menu entry. `ran` maps a strategy file name to what the scan produced for it today:
    {"stamp": strategy stamp, "suggestion": dict or None, "no_trade_reason": str or None}."""
    today = _today(verdict_label)
    shown = (verdict_label or "unknown").upper()
    out = []
    for e in entries:
        row = {k: e.get(k) for k in ("id", "name", "market", "purpose", "budget", "file", "roadmap_step")}
        row["menu_status"] = e["status"]
        row["side"] = e.get("side")
        row["strategy"] = None
        row["suggestion"] = None
        r = ran.get(e.get("file")) if e.get("file") else None
        if r:
            row["strategy"] = r["stamp"]
        if e["status"] == "not_built":
            row["status"] = "not_built"
            row["reason"] = f"Not built yet: step {e['roadmap_step']} on the roadmap."
        elif r and r.get("debit") is not None and e.get("side"):
            _debit_row(row, e, r["debit"], shown)
        elif e["status"] == "in_testing":
            row["status"] = "not_built"
            row["reason"] = (f"In testing (step {e['roadmap_step']} on the roadmap): "
                             "not suggested until it passes the unseen-period test.")
        elif today is None and e["market"] != "any":
            row["status"] = "not_eligible"
            row["reason"] = "No IV verdict today, so no strategy is matched to the market."
        elif e["market"] not in ("any", today):
            row["status"] = "not_eligible"
            row["reason"] = f"IV is {shown}; this strategy is for {e['market'].upper()} days."
        elif not e.get("file"):                      # a live rule with no trade, e.g. "IV fair: no options trade"
            row["status"] = "suggested"
            row["reason"] = f"IV is {shown}: no options trade today. Waiting is the plan."
        elif r is None:
            row["status"] = "no_trade"
            row["reason"] = "Not run in today's scan."
        elif r["suggestion"]:
            row["status"] = "suggested"
            row["reason"] = f"IV is {shown}: today's idea passed every rule in {e['file']}."
            cv = r["suggestion"].get("conviction")
            if cv:
                row["reason"] += f" Conviction {cv['label']} ({cv['score']}/100): {cv['action']}"
            row["suggestion"] = r["suggestion"]
        else:
            row["status"] = "no_trade"
            row["reason"] = r["no_trade_reason"] or "No trade passed the rules today."
        out.append(row)
    return out


READING_SIDE = {"bullish": "call", "bearish": "put", "neutral": "both"}


def _debit_row(row: dict, e: dict, d: dict, shown: str) -> None:
    """Debit spreads: one strategy file, three menu rows (call / put / both). The row whose side matches today's
    trade shows it: a live idea while the pass rule holds, otherwise a paper idea (in testing)."""
    if d["reason"] and not d["trade"]:
        verdict_issue = d["reason"].startswith("IV is ")
        row["status"] = "not_eligible" if verdict_issue else "no_trade"
        row["reason"] = d["reason"]
        return
    v = d["view"]
    today_side = READING_SIDE.get(v["direction"], "both")
    if e["side"] != today_side:
        row["status"] = "not_eligible"
        row["reason"] = (f"Direction reading is {v['direction']}: today's big-move trade is the "
                         + {"call": "call debit spread", "put": "put debit spread",
                            "both": "no-direction trade (both sides)"}[today_side] + ".")
        return
    if d["status"] == "live":
        row["status"] = "suggested"
        row["reason"] = f"IV is {shown}, direction {v['direction']}: today's idea passed every rule in {e['file']}."
        row["suggestion"] = v
    else:
        row["status"] = "paper_idea"
        row["reason"] = "In testing: paper trade recorded."
