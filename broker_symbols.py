"""Broker symbol resolution helpers for MT5 suffix/prefix compatibility."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60
_symbol_cache = {}
_symbols_snapshot = {"time": 0.0, "symbols": []}


@dataclass
class SymbolResolution:
    requested: str
    resolved: str | None
    info: object | None
    visible: bool
    mapped: bool
    reason: str
    candidates: list[str]


def normalize_symbol_name(symbol: str | None) -> str:
    return re.sub(r"[^A-Z]", "", str(symbol or "").upper())


def base_symbol(symbol: str | None) -> str:
    normalized = normalize_symbol_name(symbol)
    if len(normalized) <= 6:
        return normalized

    majors = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
    metals = ("XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD")
    for metal in metals:
        if metal in normalized:
            return metal

    for index in range(max(len(normalized) - 5, 0)):
        pair = normalized[index:index + 6]
        if pair[:3] in majors and pair[3:] in majors:
            return pair
    return normalized[:6]


def _symbols_get_safe():
    now = time.time()
    if now - _symbols_snapshot["time"] <= _CACHE_TTL_SECONDS:
        return _symbols_snapshot["symbols"]
    try:
        symbols = list(mt5.symbols_get() or [])
    except Exception as exc:
        logger.warning("MT5 symbols_get failed during symbol resolution: %s", exc)
        symbols = []
    _symbols_snapshot.update({"time": now, "symbols": symbols})
    return symbols


def _select_symbol_safe(symbol: str) -> bool:
    try:
        return bool(mt5.symbol_select(symbol, True))
    except Exception:
        return False


def _candidate_score(requested: str, candidate) -> tuple:
    name = getattr(candidate, "name", "") or ""
    req_upper = requested.upper()
    name_upper = name.upper()
    req_base = base_symbol(requested)
    cand_base = base_symbol(name)
    visible = bool(getattr(candidate, "visible", False))

    if name_upper == req_upper:
        match_rank = 0
    elif name_upper.startswith(req_upper):
        match_rank = 1
    elif cand_base == req_base:
        match_rank = 2
    elif req_base and req_base in normalize_symbol_name(name):
        match_rank = 3
    else:
        match_rank = 9

    suffix_penalty = max(len(name_upper) - len(req_upper), 0)
    return (match_rank, 0 if visible else 1, suffix_penalty, len(name_upper), name_upper)


def resolve_symbol(symbol: str | None) -> SymbolResolution:
    requested = str(symbol or "").strip().upper()
    if not requested:
        return SymbolResolution("", None, None, False, False, "Empty symbol", [])

    cached = _symbol_cache.get(requested)
    if cached and time.time() - cached[0] <= _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        exact_info = mt5.symbol_info(requested)
    except Exception:
        exact_info = None

    if exact_info is not None:
        visible = bool(getattr(exact_info, "visible", False)) or _select_symbol_safe(requested)
        result = SymbolResolution(requested, requested, exact_info, visible, False, "Exact MT5 symbol", [])
        _symbol_cache[requested] = (time.time(), result)
        return result

    req_base = base_symbol(requested)
    matches = []
    for candidate in _symbols_get_safe():
        name = getattr(candidate, "name", "") or ""
        if not name:
            continue
        name_key = normalize_symbol_name(name)
        if name.upper() == requested or name.upper().startswith(requested):
            matches.append(candidate)
        elif req_base and (base_symbol(name) == req_base or req_base in name_key):
            matches.append(candidate)

    matches = sorted(matches, key=lambda item: _candidate_score(requested, item))
    candidate_names = [getattr(item, "name", "") for item in matches[:10] if getattr(item, "name", "")]

    if not matches:
        result = SymbolResolution(requested, None, None, False, False, "Symbol not found in MT5", [])
        _symbol_cache[requested] = (time.time(), result)
        return result

    selected = matches[0]
    resolved = getattr(selected, "name", "") or requested
    visible = bool(getattr(selected, "visible", False)) or _select_symbol_safe(resolved)
    try:
        info = mt5.symbol_info(resolved) or selected
    except Exception:
        info = selected

    mapped = resolved.upper() != requested
    reason = f"Mapped {requested} -> {resolved}" if mapped else "Exact MT5 symbol from Market Watch"
    result = SymbolResolution(requested, resolved, info, visible, mapped, reason, candidate_names)
    _symbol_cache[requested] = (time.time(), result)
    return result


def resolve_symbols(symbols) -> tuple[list[str], dict[str, dict]]:
    resolved_symbols = []
    mapping = {}
    for item in symbols or []:
        resolution = resolve_symbol(item)
        key = str(item or "").strip().upper()
        mapping[key] = {
            "requested": resolution.requested,
            "resolved": resolution.resolved,
            "visible": resolution.visible,
            "mapped": resolution.mapped,
            "reason": resolution.reason,
            "candidates": resolution.candidates,
        }
        if resolution.resolved and resolution.visible and resolution.resolved not in resolved_symbols:
            resolved_symbols.append(resolution.resolved)
    return resolved_symbols, mapping
