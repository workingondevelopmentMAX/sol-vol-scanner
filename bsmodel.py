"""Black-76 pricing and Greeks for European options on a forward/future price.

Deribit prices its options off an underlying future (``underlying_price``), so
Black-76 with zero rates is a close, simple model. All values are per 1 SOL of
underlying, in USDC.

Units returned by ``greeks``:
  delta  - USDC change per $1 move in SOL
  gamma  - change in delta per $1 move in SOL
  theta  - USDC change per calendar day (negative = option loses value daily)
  vega   - USDC change per 1 vol point (e.g. IV 60% -> 61%)
"""
from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(F: float, K: float, T: float, sigma: float):
    st = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / st
    return d1, d1 - st


def price(F: float, K: float, T: float, sigma: float, kind: str) -> float:
    """Option value per 1 unit of underlying. kind is 'call' or 'put'."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, F - K) if kind == "call" else max(0.0, K - F)
        return intrinsic
    d1, d2 = _d1d2(F, K, T, sigma)
    if kind == "call":
        return F * norm_cdf(d1) - K * norm_cdf(d2)
    return K * norm_cdf(-d2) - F * norm_cdf(-d1)


def greeks(F: float, K: float, T: float, sigma: float, kind: str) -> dict:
    if T <= 0 or sigma <= 0:
        itm = (F > K) if kind == "call" else (F < K)
        d = (1.0 if itm else 0.0) if kind == "call" else (-1.0 if itm else 0.0)
        return {"delta": d, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, _ = _d1d2(F, K, T, sigma)
    sqT = math.sqrt(T)
    pdf = norm_pdf(d1)
    delta = norm_cdf(d1) if kind == "call" else norm_cdf(d1) - 1.0
    gamma = pdf / (F * sigma * sqT)
    vega = F * pdf * sqT / 100.0              # per 1 vol point
    theta = -(F * pdf * sigma) / (2.0 * sqT) / 365.0   # per calendar day
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}
