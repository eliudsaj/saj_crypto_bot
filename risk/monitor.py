"""Read-only risk intelligence snapshot for the dashboard."""

from __future__ import annotations

from datetime import datetime
from market.regime import regime_performance


def _safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class RiskMonitor:
    """Collect current engine risk state without changing trading rules."""

    def __init__(self, engine=None):
        self.engine = engine

    def status(self) -> dict:
        if not self.engine:
            return self._offline_status()

        engine = self.engine
        status = engine.get_status() or {}
        account = engine.mt5.get_account_info() if getattr(engine, "mt5", None) else None
        positions = engine.mt5.get_positions() if getattr(engine, "mt5", None) else []

        equity = _safe_float((account or {}).get("equity"), _safe_float(status.get("equity")))
        total_open_risk = _safe_float(status.get("current_open_risk"))
        max_open_risk = _safe_float(status.get("max_open_risk"), equity * _safe_float(getattr(engine, "max_exposure_pct", 0.0)))
        risk_used_pct = total_open_risk / max_open_risk if max_open_risk > 0 else 0.0

        daily_realized = _safe_float(status.get("realized_profit"))
        floating_pnl = _safe_float(status.get("floating_profit"), sum(_safe_float(p.get("profit")) for p in positions))
        session_drawdown = _safe_float(status.get("floating_drawdown"))
        max_drawdown_allowed = equity * _safe_float(getattr(engine, "max_drawdown_pct", 0.0)) if equity > 0 else 0.0
        drawdown_used_pct = session_drawdown / max_drawdown_allowed if max_drawdown_allowed > 0 else 0.0

        daily_cap_pct = _safe_float(getattr(engine, "daily_profit_cap", 0.0))
        daily_start = _safe_float(getattr(engine, "daily_start_equity", None))
        daily_cap_amount = daily_start * daily_cap_pct if daily_start > 0 else 0.0
        daily_cap_progress = daily_realized / daily_cap_amount if daily_cap_amount > 0 else 0.0

        exposure_by_symbol = self._exposure_by_symbol(status.get("open_risk_details", []), positions)
        lockouts = self._symbol_lockouts()
        kill_switch = dict(getattr(engine, "killed", {}) or {})
        spread_danger = self._spread_danger()
        news_mode = self._news_mode_state()
        current_regime = self._current_regime()
        consecutive_losses = self._consecutive_losses()

        blockers = self._risk_blockers(
            kill_switch=kill_switch,
            lockouts=lockouts,
            spread_danger=spread_danger,
            risk_used_pct=risk_used_pct,
            drawdown_used_pct=drawdown_used_pct,
            daily_cap_progress=daily_cap_progress,
        )
        risk_status = self._risk_status(blockers, risk_used_pct, drawdown_used_pct, daily_cap_progress)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "risk_status": risk_status,
            "exposure_by_symbol": exposure_by_symbol,
            "total_open_risk": total_open_risk,
            "max_open_risk": max_open_risk,
            "risk_used_pct": risk_used_pct,
            "daily_realized_pnl": daily_realized,
            "floating_pnl": floating_pnl,
            "session_drawdown": session_drawdown,
            "max_drawdown_allowed": max_drawdown_allowed,
            "drawdown_used_pct": drawdown_used_pct,
            "daily_cap_amount": daily_cap_amount,
            "daily_cap_progress": daily_cap_progress,
            "consecutive_losses": consecutive_losses,
            "symbols_locked_out": lockouts,
            "kill_switch_state": kill_switch,
            "news_mode_state": news_mode,
            "current_market_regime": current_regime,
            "regime_performance": regime_performance(),
            "spread_danger_state": spread_danger,
            "risk_blockers": blockers,
        }

    def _offline_status(self) -> dict:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "risk_status": "SAFE",
            "exposure_by_symbol": [],
            "total_open_risk": 0.0,
            "max_open_risk": 0.0,
            "risk_used_pct": 0.0,
            "daily_realized_pnl": 0.0,
            "floating_pnl": 0.0,
            "session_drawdown": 0.0,
            "max_drawdown_allowed": 0.0,
            "drawdown_used_pct": 0.0,
            "daily_cap_amount": 0.0,
            "daily_cap_progress": 0.0,
            "consecutive_losses": 0,
            "symbols_locked_out": [],
            "kill_switch_state": {"all": False},
            "news_mode_state": {"enabled": False, "block_unsafe": False, "risk_multiplier": 1.0, "ladder_enabled": False},
            "current_market_regime": {"label": "unknown", "confidence": 0.0, "drivers": ["Bot offline"]},
            "regime_performance": {},
            "spread_danger_state": [],
            "risk_blockers": [],
        }

    def _exposure_by_symbol(self, details, positions):
        rows = {}
        for item in details or []:
            symbol = item.get("symbol") or "UNKNOWN"
            row = rows.setdefault(symbol, {"symbol": symbol, "risk": 0.0, "positions": 0, "floating_pnl": 0.0})
            row["risk"] += _safe_float(item.get("risk"))
            row["positions"] += 1
        for pos in positions or []:
            symbol = pos.get("symbol") or "UNKNOWN"
            row = rows.setdefault(symbol, {"symbol": symbol, "risk": 0.0, "positions": 0, "floating_pnl": 0.0})
            row["floating_pnl"] += _safe_float(pos.get("profit"))
            if row["positions"] == 0:
                row["positions"] = 1
        return sorted(rows.values(), key=lambda x: x["risk"], reverse=True)

    def _symbol_lockouts(self):
        lockouts = []
        registry = getattr(self.engine, "trade_registry", {}) or {}
        for symbol, state in registry.items():
            cooldown_until = state.get("cooldown_until")
            cooldown_active = bool(cooldown_until and datetime.now() < cooldown_until)
            active_limit = int(state.get("active_trades") or 0) >= int(getattr(self.engine, "max_trades_per_symbol", 1))
            if cooldown_active or active_limit:
                lockouts.append({
                    "symbol": symbol,
                    "active_trades": int(state.get("active_trades") or 0),
                    "cooldown_active": cooldown_active,
                    "cooldown_until": cooldown_until.isoformat() if cooldown_until else None,
                    "reason": "Cooldown active" if cooldown_active else "Max trades per symbol",
                })
        killed = getattr(self.engine, "killed", {}) or {}
        for symbol, disabled in killed.items():
            if symbol != "all" and disabled:
                lockouts.append({"symbol": symbol, "active_trades": 0, "cooldown_active": False, "cooldown_until": None, "reason": "Kill switch"})
        return lockouts

    def _news_mode_state(self):
        return {
            "enabled": bool(getattr(self.engine, "news_mode_enabled", False)),
            "block_unsafe": bool(getattr(self.engine, "news_block_unsafe", False)),
            "risk_multiplier": _safe_float(getattr(self.engine, "news_risk_multiplier", 1.0), 1.0),
            "ladder_enabled": bool(getattr(self.engine, "news_ladder_enabled", False)),
        }

    def _current_regime(self):
        regimes = getattr(self.engine, "current_regimes", {}) or {}
        if regimes:
            latest = list(regimes.values())[-1] or {}
            return latest if isinstance(latest, dict) else {"label": str(latest), "confidence": 0.0}
        candidates = (getattr(self.engine, "recent_signals", []) or [])[-10:]
        for signal in reversed(candidates):
            regime = signal.get("market_regime") or (signal.get("setup_score") or {}).get("market_regime")
            if regime:
                return regime if isinstance(regime, dict) else {"label": str(regime), "confidence": 0.0}
        return {"label": "unknown", "confidence": 0.0, "drivers": ["No recent regime classification"]}

    def _spread_danger(self):
        danger = {}
        for signal in (getattr(self.engine, "recent_signals", []) or [])[-50:]:
            spread = signal.get("spread_safety") or (signal.get("setup_score") or {}).get("spread") or {}
            if spread.get("safe") is False:
                symbol = signal.get("symbol") or "UNKNOWN"
                danger[symbol] = {
                    "symbol": symbol,
                    "safe": False,
                    "spread_pips": spread.get("spread_pips"),
                    "description": spread.get("description", "Spread unsafe"),
                }
        return list(danger.values())

    def _consecutive_losses(self):
        logs = self.engine.logger.get_logs() if getattr(self.engine, "logger", None) else []
        closed = [item for item in logs if item.get("event") == "TRADE_CLOSED"]
        losses = 0
        for item in reversed(closed):
            if _safe_float(item.get("profit")) < 0:
                losses += 1
            else:
                break
        return losses

    def _risk_blockers(self, **kwargs):
        blockers = []
        if kwargs["kill_switch"].get("all"):
            blockers.append({"severity": "danger", "reason": "Global kill switch is active"})
        if kwargs["risk_used_pct"] >= 1:
            blockers.append({"severity": "danger", "reason": "Open risk is at or above allowed cap"})
        elif kwargs["risk_used_pct"] >= 0.75:
            blockers.append({"severity": "warning", "reason": "Open risk is elevated"})
        if kwargs["drawdown_used_pct"] >= 1:
            blockers.append({"severity": "danger", "reason": "Session drawdown limit reached"})
        elif kwargs["drawdown_used_pct"] >= 0.7:
            blockers.append({"severity": "warning", "reason": "Session drawdown is elevated"})
        if kwargs["daily_cap_progress"] >= 1:
            blockers.append({"severity": "warning", "reason": "Daily profit cap reached or exceeded"})
        if kwargs["spread_danger"]:
            blockers.append({"severity": "warning", "reason": "One or more symbols have unsafe spread"})
        if kwargs["lockouts"]:
            blockers.append({"severity": "info", "reason": "Symbol lockouts are active"})
        return blockers

    def _risk_status(self, blockers, risk_used_pct, drawdown_used_pct, daily_cap_progress):
        if any(item.get("severity") == "danger" for item in blockers) or risk_used_pct >= 1 or drawdown_used_pct >= 1:
            return "DANGER"
        if blockers or risk_used_pct >= 0.75 or drawdown_used_pct >= 0.7 or daily_cap_progress >= 0.9:
            return "CAUTION"
        return "SAFE"
