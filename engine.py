"""
Trading Engine - Core Bot Logic
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

load_dotenv()
logger = logging.getLogger(__name__)

FOREX_SYMBOLS = {"XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD"}

from alerts.manager import alert_manager
from broker_symbols import resolve_symbols
from brokers import get_broker_manager
from analytic_engine import AnalyticEngine
from predictive_engine import PredictiveEngine
from ensemble_decision import EnsembleDecision
from trade_logger import TradeLogger
from pending_order_manager import PendingOrderManager
from conditional_watchlist_manager import ConditionalWatchlistManager
from bible_logic import validate_trade
from technical_analysis import scan_symbols
from journal.writer import strategy_journal
from strategy.adaptive_weights import AdaptiveWeightManager
from market.regime import regime_policy
from analytics.forward_validation import current_strategy_context, record_forward_trade
from analytics.performance import summarize_performance
from analytics.ict_diagnostics import record_blocker
from licensing import get_license_manager


class TradingEngine:
    def __init__(self):
        self.broker_manager = get_broker_manager()
        self.broker_profile = self.broker_manager.get_active_profile(include_secret=True) or {}
        self.broker = self.broker_manager.create_adapter(self.broker_profile)
        self.mt5 = self.broker
        self.startup_validation_error = None
        self.logger = TradeLogger()
        self.is_running = False
        
        # CRITICAL: Add threading locks for shared data
        self._lock = threading.RLock()
        self._signals_lock = threading.Lock()
        self._positions_lock = threading.Lock()
        self._trades_lock = threading.Lock()
        
        symbol_env = "BINANCE_TRADING_SYMBOLS" if str(self.broker_profile.get("broker_type", "")).lower() == "binance" else "TRADING_SYMBOLS"
        symbol_default = "BTCUSDT,ETHUSDT" if symbol_env == "BINANCE_TRADING_SYMBOLS" else "EURUSD,GBPUSD,USDJPY"
        self.configured_symbols = self._parse_symbols(os.getenv(symbol_env, symbol_default))
        self.symbols = list(self.configured_symbols)
        self.timeframe = 5  # M5 timeframe
        self.volume = float(os.getenv("TRADE_VOLUME", 0.01))
        self.active_trades = {}

        # recent detected signals (list of dict)
        self.recent_signals = []
        # favorable signals (passed validation) for UI insight
        self.favorable_signals = []
        # rejection log (exposed via /api/logs)
        self.rejection_logs = []
        # logic feed for dashboard (real-time reasoning)
        self.logic_feed = []
        # all detected signals (for logs and watchlist)
        self.signal_history = []
        # future trade candidates for dashboard
        self.future_trades = []
        self.current_regimes = {}
        self.last_scan_results = []
        self.symbol_visibility = {}
        self.symbol_mapping = {}
        self.scanner_rejection_counts = {}
        self.strategy_context = current_strategy_context(self.symbols)
        # trade journal (open/closed trade tracking)
        self.trade_journal = []
        self.last_known_profit = {}
        self.adaptive_weights = AdaptiveWeightManager()

        # kill switches per symbol or global
        self.killed = {"all": False}  # set symbol to True to disable
        # rule toggle configuration (set from UI)
        self.rule_config = {"ema": True, "volume": True, "po3": True}

        # risk management
        self.risk_pct = float(os.getenv("RISK_PERCENT", 0.01))  # percent of equity per trade
        self.position_sizing_mode = os.getenv("POSITION_SIZING_MODE", "fixed").strip().lower()
        self.max_exposure_pct = float(os.getenv("MAX_EXPOSURE_PERCENT", 0.05))  # max exposure on open positions
        self.no_revenge_cooldown = int(os.getenv("NO_REVENGE_COOLDOWN_SECONDS", 24 * 3600))
        self.cooldown_until = None

        # minimum profit target for signal (in pips)
        self.min_profit_pips = float(os.getenv("MIN_PROFIT_PIPS", 10))

        # Daily profit cap - shut down after hitting target
        self.daily_profit_cap = float(os.getenv("DAILY_PROFIT_CAP", 0.02))  # 2% daily profit cap
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PERCENT", 0.05))
        self.daily_start_equity = None
        self.start_equity = None
        self.peak_equity = None

        # ========== GLOBAL TRADE REGISTRY - SIGNAL LOCKOUT SYSTEM ==========
        # Prevents looping triggers by tracking active trades and cooldowns per symbol
        self.trade_registry = {}  # {symbol: {"active_trades": count, "last_trade_time": datetime, "cooldown_until": datetime}}
        self.signal_lockout_enabled = True  # Master switch for lockout system
        self.max_trades_per_symbol = int(os.getenv("MAX_TRADES_PER_SYMBOL", 1))  # Default: 1 trade per symbol
        self.trade_cooldown_minutes = int(os.getenv("TRADE_COOLDOWN_MINUTES", 1))  # Faster early-entry retry window
        self.trade_interval_minutes = int(os.getenv("TRADE_INTERVAL_MINUTES", 0))  # Global interval disabled; using symbol-specific cooldown only
        self.last_trade_timestamp = None
        self.scan_interval_seconds = int(os.getenv("SCAN_INTERVAL_SECONDS", 3))
        self.engine_loop_sleep_seconds = float(os.getenv("ENGINE_LOOP_SLEEP_SECONDS", min(3, self.scan_interval_seconds)))
        self.scan_on_new_candle = os.getenv("SCAN_ON_NEW_CANDLE", "false").lower() in ["true", "1", "yes"]
        self.scan_timeframe_minutes = int(os.getenv("SCAN_TIMEFRAME_MINUTES", 5))
        self.auto_append_market_watch_symbols = os.getenv("AUTO_APPEND_MARKET_WATCH_SYMBOLS", "false").lower() in ["true", "1", "yes"]
        self.last_scan_at = None
        self.last_scan_candle_key = None
        self.next_scan_at = None
        self.last_scan_signal_count = 0
        self._signal_log_cache = {}
        self.duplicate_signal_cooldown_seconds = int(os.getenv("DUPLICATE_SIGNAL_COOLDOWN_SECONDS", 300))
        self.market_execution_score_threshold = float(os.getenv("MARKET_EXECUTION_SCORE_THRESHOLD", 0.45))
        self.market_execution_conviction_threshold = float(os.getenv("MARKET_EXECUTION_CONVICTION_THRESHOLD", 0.35))
        self.conviction_threshold = float(os.getenv("CONVICTION_THRESHOLD", os.getenv("MIN_CONVICTION", 0.70)))
        self.trailing_stop_trigger_pct = float(os.getenv("TRAILING_STOP_TRIGGER_PCT", 0.20))  # Protect after 20% of TP reached
        self.trailing_stop_lock_pips = float(os.getenv("TRAILING_STOP_LOCK_PIPS", 0.5))
        self.trailing_stop_step_pct = float(os.getenv("TRAILING_STOP_STEP_PCT", 0.15))
        self.trailing_stop_min_step_pips = float(os.getenv("TRAILING_STOP_MIN_STEP_PIPS", 0.3))
        self.trailing_tp_enabled = os.getenv("FEATURE_TRAILING_TAKE_PROFIT", "true").lower() in ["true", "1", "yes"]
        self.trailing_tp_trigger_pct = float(os.getenv("TRAILING_TP_TRIGGER_PCT", 0.8))
        self.trailing_tp_extension_pct = float(os.getenv("TRAILING_TP_EXTENSION_PCT", 0.5))
        self.trailing_tp_cooldown_seconds = int(os.getenv("TRAILING_TP_COOLDOWN_SECONDS", 300))
        self.partial_tp_enabled = os.getenv("FEATURE_PARTIAL_TAKE_PROFIT", "true").lower() in ["true", "1", "yes"]
        self.partial_tp_trigger_r = float(os.getenv("PARTIAL_TP_TRIGGER_R", 0.30))
        self.partial_tp_close_pct = float(os.getenv("PARTIAL_TP_CLOSE_PCT", 0.5))
        self.partial_tp_move_sl_to_be = os.getenv("PARTIAL_TP_MOVE_SL_TO_BE", "false").lower() in ["true", "1", "yes"]
        self.reverse_profit_exit_enabled = os.getenv("FEATURE_REVERSE_PROFIT_EXIT", "true").lower() in ["true", "1", "yes"]
        self.reverse_profit_min_r = float(os.getenv("REVERSE_PROFIT_MIN_R", 0.15))
        self.reverse_profit_giveback_pct = float(os.getenv("REVERSE_PROFIT_GIVEBACK_PCT", 0.25))
        self.reverse_profit_close_pct = float(os.getenv("REVERSE_PROFIT_CLOSE_PCT", 1.0))
        self.reverse_after_partial_lock_r = float(os.getenv("REVERSE_AFTER_PARTIAL_LOCK_R", 0.10))
        self.min_expected_r = float(os.getenv("MIN_EXPECTED_R", 1.2))
        self.min_expected_r_scalp = float(os.getenv("MIN_EXPECTED_R_SCALP", 0.8))
        self.take_profit_r_multiplier = float(os.getenv("TAKE_PROFIT_R_MULTIPLIER", 1.5))
        self.take_profit_r_multiplier_scalp = float(os.getenv("TAKE_PROFIT_R_MULTIPLIER_SCALP", 1.2))
        self.execution_conviction_threshold = float(os.getenv("EXECUTION_CONVICTION_THRESHOLD", os.getenv("MIN_CONVICTION", 0.70)))
        self.execution_setup_score_threshold = float(os.getenv("EXECUTION_SETUP_SCORE_THRESHOLD", os.getenv("MIN_SETUP_SCORE", 0.80)))
        self.execution_archetype_score_threshold = float(os.getenv("EXECUTION_ARCHETYPE_SCORE_THRESHOLD", 0.58))
        self.professional_gate_enabled = os.getenv("FEATURE_PROFESSIONAL_EXECUTION_GATE", "true").lower() in ["true", "1", "yes"]
        self.min_execution_grade = os.getenv("MIN_EXECUTION_GRADE", "B").strip().upper() or "B"
        self.allow_c_scalps = os.getenv("ALLOW_C_GRADE_SCALPS", "false").lower() in ["true", "1", "yes"]
        self.min_professional_score = float(os.getenv("MIN_PROFESSIONAL_SETUP_SCORE", 0.62))
        self.min_professional_conviction = float(os.getenv("MIN_PROFESSIONAL_CONVICTION", 0.30))
        self.min_session_score_for_trade = float(os.getenv("MIN_SESSION_SCORE_FOR_TRADE", 0.45))
        self.min_session_score_for_scalp = float(os.getenv("MIN_SESSION_SCORE_FOR_SCALP", 0.65))
        self.block_context_watch_trades = os.getenv("BLOCK_CONTEXT_WATCH_TRADES", "true").lower() in ["true", "1", "yes"]
        self.strict_quality_gate_enabled = os.getenv("FEATURE_STRICT_QUALITY_GATE", "true").lower() in ["true", "1", "yes"]
        self.min_structural_quality_score = float(os.getenv("MIN_STRUCTURAL_QUALITY_SCORE", 0.55))
        self.min_displacement_body_ratio = float(os.getenv("MIN_DISPLACEMENT_BODY_RATIO", 1.35))
        self.min_candle_close_quality = float(os.getenv("MIN_CANDLE_CLOSE_QUALITY", 0.62))
        self.min_volatility_quality = float(os.getenv("MIN_VOLATILITY_QUALITY", 0.35))
        self.min_market_quality_score = float(os.getenv("MIN_MARKET_QUALITY_SCORE", 0.42))
        self.min_confidence_persistence = int(os.getenv("MIN_CONFIDENCE_PERSISTENCE", 2))
        self.require_htf_agreement = os.getenv("REQUIRE_HTF_AGREEMENT", "true").lower() in ["true", "1", "yes"]
        self.require_liquidity_context = os.getenv("REQUIRE_LIQUIDITY_CONTEXT", "true").lower() in ["true", "1", "yes"]
        self.max_entry_drift_pct = float(os.getenv("MAX_ENTRY_DRIFT_PCT", 0.35))
        self.max_entry_drift_pips = float(os.getenv("MAX_ENTRY_DRIFT_PIPS", 10))
        self.wait_for_retest = os.getenv("WAIT_FOR_RETEST", "true").lower() in ["true", "1", "yes"]
        self.early_entry_enabled = False
        self.early_entry_min_score = float(os.getenv("EARLY_ENTRY_MIN_SCORE", 0.50))
        self.false_move_detection_enabled = os.getenv("FEATURE_FALSE_MOVE_DETECTION", "true").lower() in ["true", "1", "yes"]
        self.news_mode_enabled = os.getenv("FEATURE_NEWS_MODE", "true").lower() in ["true", "1", "yes"]
        self.news_block_unsafe = os.getenv("NEWS_BLOCK_UNSAFE", "true").lower() in ["true", "1", "yes"]
        self.news_risk_multiplier = float(os.getenv("NEWS_RISK_MULTIPLIER", 0.35))
        self.news_allow_retest_follow = os.getenv("NEWS_ALLOW_RETEST_FOLLOW", "true").lower() in ["true", "1", "yes"]
        self.news_ladder_enabled = os.getenv("FEATURE_NEWS_LADDER", "true").lower() in ["true", "1", "yes"]
        self.news_ladder_max_addons = int(os.getenv("NEWS_LADDER_MAX_ADDONS", 2))
        self.news_ladder_min_r = float(os.getenv("NEWS_LADDER_MIN_R", 0.55))
        self.news_ladder_volume_pct = float(os.getenv("NEWS_LADDER_VOLUME_PCT", 0.35))
        self.news_ladder_cooldown_seconds = int(os.getenv("NEWS_LADDER_COOLDOWN_SECONDS", 180))
        self.signal_persistence = {}
        self.backtest_mode = False
        self.license_manager = get_license_manager()
        self.license_status = self.license_manager.validate().to_dict()
        self.license_alerts_sent = set()
        self.live_trading_disabled = not bool(self.license_status.get("trading_allowed"))

        self.ict_enabled = os.getenv("ICT_ENABLED", "false").lower() in ["true", "1", "yes"]
        self.ict_require_liquidity_sweep = os.getenv("ICT_REQUIRE_LIQUIDITY_SWEEP", "true").lower() in ["true", "1", "yes"]
        self.ict_require_fvg_retest = os.getenv("ICT_REQUIRE_FVG_RETEST", "true").lower() in ["true", "1", "yes"]
        self.ict_require_bos_or_choch = os.getenv("ICT_REQUIRE_BOS_OR_CHOCH", "true").lower() in ["true", "1", "yes"]
        self.ict_min_risk_reward = float(os.getenv("ICT_MIN_RISK_REWARD", 1.5))
        self.min_setup_score = float(os.getenv("MIN_SETUP_SCORE", 0.80))
        self.min_conviction = float(os.getenv("MIN_CONVICTION", 0.70))
        self.min_rr = float(os.getenv("MIN_RR", 1.5))
        self.ict_max_trades_per_session = int(os.getenv("ICT_MAX_TRADES_PER_SESSION", 2))
        self.ict_allowed_sessions = {
            item.strip().replace(" ", "")
            for item in os.getenv("ICT_ALLOWED_SESSIONS", "London,NewYork").split(",")
            if item.strip()
        }
        self.ict_session_trades = {}
        self.enforce_backtest_validation = os.getenv("ENFORCE_BACKTEST_VALIDATION", "true").lower() in ["true", "1", "yes"]
        self.min_backtest_profit_factor = float(os.getenv("MIN_BACKTEST_PROFIT_FACTOR", 1.2))

        # predefined market sessions (UTC times)
        self.sessions = {
            "Asia": {"start": "00:00", "end": "09:00"},
            "London": {"start": "08:00", "end": "17:00"},
            "New York": {"start": "13:00", "end": "22:00"},
        }

        # Advanced trading features
        self.pending_order_manager = PendingOrderManager(self.mt5)
        self.conditional_watchlist_manager = ConditionalWatchlistManager(self.mt5)
        
        # War Room Engines
        self.analytic_engine = AnalyticEngine()
        self.predictive_engine = PredictiveEngine()
        analytic_weight = float(os.getenv("ANALYTIC_WEIGHT", 0.6))
        predictive_weight = float(os.getenv("PREDICTIVE_WEIGHT", 0.4))
        self.ensemble_decision = EnsembleDecision(analytic_weight, predictive_weight)
        self.conviction_threshold = float(os.getenv("CONVICTION_THRESHOLD", os.getenv("MIN_CONVICTION", 0.70)))
        
        # Feature toggles (can be controlled via UI)
        self.features = {
            "pending_orders": os.getenv("FEATURE_PENDING_ORDERS", "true").lower() in ["true", "1"],
            "conditional_watchlist": os.getenv("FEATURE_CONDITIONAL_WATCHLIST", "true").lower() in ["true", "1"],
            "war_room": os.getenv("FEATURE_WAR_ROOM", "true").lower() in ["true", "1"],
        }
        
        # CRITICAL: Validate all config values on startup
        self._validate_config()
        self._log_active_broker()
        self.startup_validation_error = self._validate_broker_symbol_compatibility()

    def _broker_type(self):
        return str(self.broker_profile.get("broker_type") or getattr(self.broker, "broker_type", "mt5") or "mt5").lower()

    def _log_active_broker(self):
        logger.info("ACTIVE BROKER: %s", self.broker_profile.get("name") or "Default MT5")
        logger.info("BROKER TYPE: %s", self._broker_type())
        logger.info("ACCOUNT: %s", self.broker_profile.get("account") or "")
        logger.info("SERVER: %s", self.broker_profile.get("server") or "")

    def _validate_broker_symbol_compatibility(self):
        broker_type = self._broker_type()
        symbols = {str(symbol or "").upper() for symbol in self.symbols}
        invalid = sorted(symbols & FOREX_SYMBOLS)
        if broker_type == "binance" and invalid:
            return (
                "Broker/symbol mismatch: Binance cannot trade forex/CFD symbols "
                f"{', '.join(invalid)}. Use MT5 as active broker or set BINANCE_TRADING_SYMBOLS "
                "to crypto spot symbols like BTCUSDT,ETHUSDT."
            )
        return None

    def _parse_symbols(self, value):
        """Normalize symbol configuration from env/UI into a unique uppercase list."""
        if isinstance(value, str):
            raw = value.split(",")
        else:
            raw = value or []
        symbols = []
        for item in raw:
            symbol = str(item or "").strip().upper()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        return symbols

    def _validate_config(self):
        """CRITICAL FIX: Validate all configuration values on startup"""
        try:
            # Validate TRADING_SYMBOLS
            symbols = self._parse_symbols(self.symbols)
            
            if not symbols:
                logger.error("No valid trading symbols configured. Using broker defaults.")
                self.symbols = ["BTCUSDT", "ETHUSDT"] if self._broker_type() == "binance" else ["EURUSD", "GBPUSD", "USDJPY"]
            else:
                self.symbols = symbols
                self.configured_symbols = list(symbols)
                logger.info(f"Trading symbols validated: {self.symbols}")
            
            # Validate TRADE_VOLUME
            if self.volume <= 0:
                logger.error(f"Invalid TRADE_VOLUME: {self.volume}. Must be positive. Using default 0.1")
                self.volume = 0.1
            elif self.volume > 10:
                logger.warning(f"TRADE_VOLUME is very high: {self.volume}. Consider reducing.")
            if self.position_sizing_mode not in ["fixed", "risk_percent"]:
                logger.error(f"Invalid POSITION_SIZING_MODE: {self.position_sizing_mode}. Using fixed")
                self.position_sizing_mode = "fixed"
            
            # Validate RISK_PERCENT
            if self.risk_pct >= 1 and self.risk_pct <= 100:
                self.risk_pct = self.risk_pct / 100.0
            if self.risk_pct <= 0 or self.risk_pct > 1:
                logger.error(f"Invalid RISK_PERCENT: {self.risk_pct}. Must be 0 < x <= 1. Using default 0.01")
                self.risk_pct = 0.01
            
            # Validate MAX_EXPOSURE_PERCENT (allow 5 or 0.05 style inputs)
            if self.max_exposure_pct <= 0:
                logger.error(f"Invalid MAX_EXPOSURE_PERCENT: {self.max_exposure_pct}. Using default 0.05")
                self.max_exposure_pct = 0.05
            elif self.max_exposure_pct > 1 and self.max_exposure_pct <= 100:
                self.max_exposure_pct = self.max_exposure_pct / 100.0
            elif self.max_exposure_pct > 100:
                logger.error(f"Unrealistic MAX_EXPOSURE_PERCENT: {self.max_exposure_pct}. Using default 0.05")
                self.max_exposure_pct = 0.05
            
            # Validate MIN_PROFIT_PIPS
            if self.min_profit_pips < 1:
                logger.error(f"Invalid MIN_PROFIT_PIPS: {self.min_profit_pips}. Using default 10")
                self.min_profit_pips = 10

            self.trailing_stop_trigger_pct = max(0.5, self.trailing_stop_trigger_pct)
            self.trailing_stop_lock_pips = max(0.0, self.trailing_stop_lock_pips)
            self.partial_tp_trigger_r = max(0.75, self.partial_tp_trigger_r)
            self.partial_tp_close_pct = max(0.05, min(1.0, self.partial_tp_close_pct))
            self.reverse_profit_min_r = max(0.6, self.reverse_profit_min_r)
            self.reverse_profit_giveback_pct = max(0.05, min(0.95, self.reverse_profit_giveback_pct))
            self.reverse_profit_close_pct = max(0.1, min(1.0, self.reverse_profit_close_pct))
            self.reverse_after_partial_lock_r = max(0.35, self.reverse_after_partial_lock_r)
            self.min_professional_score = max(0.0, min(1.0, self.min_professional_score))
            self.min_professional_conviction = max(0.0, min(1.0, self.min_professional_conviction))
            self.min_session_score_for_trade = max(0.0, min(1.0, self.min_session_score_for_trade))
            self.min_session_score_for_scalp = max(0.0, min(1.0, self.min_session_score_for_scalp))
            self.min_structural_quality_score = max(0.0, min(1.0, self.min_structural_quality_score))
            self.min_displacement_body_ratio = max(0.0, self.min_displacement_body_ratio)
            self.min_candle_close_quality = max(0.0, min(1.0, self.min_candle_close_quality))
            self.min_volatility_quality = max(0.0, min(1.0, self.min_volatility_quality))
            self.min_market_quality_score = max(0.0, min(1.0, self.min_market_quality_score))
            self.min_confidence_persistence = max(1, min(10, self.min_confidence_persistence))
            self.news_risk_multiplier = max(0.05, min(1.0, self.news_risk_multiplier))
            self.news_ladder_max_addons = max(0, min(5, self.news_ladder_max_addons))
            self.news_ladder_min_r = max(0.1, self.news_ladder_min_r)
            self.news_ladder_volume_pct = max(0.05, min(1.0, self.news_ladder_volume_pct))
            self.news_ladder_cooldown_seconds = max(30, self.news_ladder_cooldown_seconds)
            self.take_profit_r_multiplier = max(0.1, self.take_profit_r_multiplier)
            self.take_profit_r_multiplier_scalp = max(0.1, self.take_profit_r_multiplier_scalp)
            if self.min_execution_grade not in {"A", "B", "C", "D"}:
                self.min_execution_grade = "B"
            
            logger.info(f"Config validated: Sizing={self.position_sizing_mode}, Volume={self.volume}, Risk={self.risk_pct*100}%, MaxOpenRisk={self.max_exposure_pct*100}%, MinProfit={self.min_profit_pips}p")
            
            # Initialize symbols from MT5 Market Watch for multi-pair awareness
            self._initialize_symbols_from_market_watch()
            
        except Exception as e:
            logger.critical(f"Config validation error: {e}. Using safe defaults.")
            self.symbols = ["EURUSD"]
            self.volume = 0.01
            self.risk_pct = 0.01
            self.position_sizing_mode = "fixed"
            self.max_exposure_pct = 0.05
            self.min_profit_pips = 10

    def _initialize_symbols_from_market_watch(self):
        """Initialize trading symbols from MT5 Market Watch for multi-pair awareness"""
        try:
            if str(self.broker_profile.get("broker_type", "")).lower() != "mt5":
                self.symbols = list(getattr(self, "configured_symbols", None) or self.symbols or [])
                self.symbol_visibility = {symbol: True for symbol in self.symbols}
                self.symbol_mapping = {
                    symbol: {"resolved": symbol, "visible": True, "reason": "Active broker profile"}
                    for symbol in self.symbols
                }
                logger.info("Broker symbol initialization: %s symbols for %s", len(self.symbols), self.broker_profile.get("broker_type"))
                return
            if not self.mt5.ensure_connected():
                logger.warning("MT5 not connected, using configured symbols")
                return
            
            # Get all symbols from Market Watch
            all_symbols = self.mt5.get_symbols()
            if all_symbols is None:
                logger.warning("Failed to get symbols from MT5, using configured symbols")
                return
            
            # Filter for visible symbols (in Market Watch)
            market_watch_symbols = [s.name for s in all_symbols if s.visible]
            configured = list(getattr(self, "configured_symbols", None) or self.symbols or [])
            resolved_configured, mapping = resolve_symbols(configured)
            self.symbol_mapping = mapping
            self.symbol_visibility = {
                entry.get("resolved") or key: bool(entry.get("visible"))
                for key, entry in mapping.items()
            }
            
            if resolved_configured:
                if self.auto_append_market_watch_symbols:
                    # Use resolved configured symbols first, then additional Market Watch symbols.
                    combined_symbols = list(resolved_configured) + [s for s in market_watch_symbols if s not in resolved_configured]
                else:
                    combined_symbols = list(resolved_configured)
                
                self.symbols = combined_symbols[:50]  # Limit to 50 symbols to avoid overload
                logger.info(f"✓ Multi-pair awareness activated: {len(self.symbols)} symbols from Market Watch")
                for requested, entry in mapping.items():
                    logger.info(
                        "Symbol mapping: %s -> %s (%s)",
                        requested,
                        entry.get("resolved") or "unresolved",
                        entry.get("reason"),
                    )
                logger.info(
                    "Market Watch expansion %s",
                    "enabled" if self.auto_append_market_watch_symbols else "disabled",
                )
                logger.info(f"Scanner active symbols: {self.symbols}")
            elif market_watch_symbols and self.auto_append_market_watch_symbols:
                self.symbols = market_watch_symbols[:50]
                logger.warning("No configured symbols resolved; falling back to Market Watch expansion")
            else:
                logger.warning("No symbols visible in Market Watch, using configured symbols")
                
        except Exception as e:
            logger.error(f"Error initializing symbols from Market Watch: {e}")
            # Fall back to configured symbols

    def add_logic(self, symbol: str, message: str, level: str = "info"):
        """Add a logic feed entry for dashboard tracing."""
        symbol = str(symbol or "SYSTEM").strip().upper()
        entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "message": message,
            "level": level,
        }
        self.logic_feed.append(entry)
        self.logic_feed = self.logic_feed[-100:]
        if level == "error":
            logger.error(f"{symbol}: {message}")
        elif level == "warning":
            logger.warning(f"{symbol}: {message}")
        else:
            logger.info(f"{symbol}: {message}")

    def log_rejection(self, symbol: str, reason: str):
        """Store rejection reasons for UI consumption."""
        symbol = str(symbol or "SYSTEM").strip().upper()
        reason = str(reason or "Unknown rejection")
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "SIGNAL_REJECTED",
            "symbol": symbol,
            "reason": reason,
        }
        self.rejection_logs.append(entry)
        self.rejection_logs = self.rejection_logs[-50:]
        try:
            self.logger._save_log(entry)
        except Exception as e:
            logger.error(f"Failed to persist rejection log: {e}")
        if str(reason).startswith("War Room:"):
            alert_manager.create(
                "War Room rejected trade",
                f"{symbol}: {reason}",
                severity="warning",
                category="trade",
                symbol=symbol,
                event="war_room_rejected",
                metadata={"reason": reason},
                dedupe_key=f"war_room:{symbol}:{reason}",
                cooldown_seconds=300,
            )
        self.add_logic(symbol, f"Rejected: {reason}", level="warning")
        self.scanner_rejection_counts[reason] = self.scanner_rejection_counts.get(reason, 0) + 1

    def _record_scan_diagnostics(self, diagnostics):
        """Persist the latest scanner pass with per-symbol visibility and result details."""
        now = datetime.now().isoformat()
        rows = []
        for item in diagnostics or []:
            symbol = str(item.get("symbol") or "UNKNOWN").strip().upper()
            status = item.get("status") or "unknown"
            reason = item.get("reason") or "No diagnostic reason"
            row = {**item, "symbol": symbol, "timestamp": now}
            rows.append(row)
            self.symbol_visibility[symbol] = item.get("visible")
            configured = str(item.get("configured_symbol") or "").strip().upper()
            if configured:
                self.symbol_mapping[configured] = {
                    "requested": configured,
                    "resolved": item.get("resolved_symbol") or symbol,
                    "visible": item.get("visible"),
                    "mapped": item.get("mapped"),
                    "reason": item.get("mapping_reason") or "",
                    "candidates": item.get("symbol_candidates") or [],
                }
            if status == "skipped":
                self.scanner_rejection_counts[reason] = self.scanner_rejection_counts.get(reason, 0) + 1
                self.add_logic(symbol, f"Scanner skipped: {reason}", level="warning")
            else:
                self.add_logic(symbol, reason, level="info")
        self.last_scan_results = rows[-100:]

    def get_scanner_debug(self):
        """Return scanner state for API/debug UI without touching execution state."""
        return {
            "configured_symbols": list(getattr(self, "configured_symbols", []) or []),
            "active_symbols": list(self.symbols or []),
            "symbols_loaded": len(self.symbols or []),
            "symbols_scanned": len(self.last_scan_results or []),
            "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
            "last_signal_count": self.last_scan_signal_count,
            "last_scan_results": list(self.last_scan_results or []),
            "symbols_skipped": [
                item for item in (self.last_scan_results or [])
                if item.get("status") == "skipped"
            ],
            "symbol_visibility": dict(self.symbol_visibility or {}),
            "symbol_mapping": dict(getattr(self, "symbol_mapping", {}) or {}),
            "rejection_counts": dict(sorted(self.scanner_rejection_counts.items(), key=lambda item: item[1], reverse=True)),
        }

    def connect(self):
        """Connect to the active broker adapter."""
        connected = self.broker.connect()
        if connected:
            info = self.broker.get_account_info() or {}
            self.start_equity = info.get("equity")
            self.peak_equity = self.start_equity
        return connected

    def disconnect(self):
        """Disconnect from the active broker adapter."""
        self.broker.disconnect()

    def _get_equity(self):
        info = self.mt5.get_account_info() or {}
        return info.get("equity")

    def _get_symbol_info(self, symbol: str):
        return self.mt5.get_symbol_info(symbol)

    def _get_active_broker_spread(self):
        symbol = self.symbols[0] if self.symbols else None
        if not symbol:
            return {}
        try:
            tick = self.broker.get_symbol_tick(symbol)
            info = self.broker.get_symbol_info(symbol)
            if not tick or not info:
                return {"symbol": symbol, "spread_pips": None}
            ask = float(getattr(tick, "ask", 0) or 0)
            bid = float(getattr(tick, "bid", 0) or 0)
            digits = int(getattr(info, "digits", 5) or 5)
            pip_size = 0.0001 if digits > 3 else 0.01
            spread_pips = ((ask - bid) / pip_size) if ask and bid and pip_size else None
            return {"symbol": symbol, "spread_pips": round(spread_pips, 2) if spread_pips is not None else None}
        except Exception:
            return {"symbol": symbol, "spread_pips": None}

    def _get_pip_value(self, symbol: str):
        """Return the value of one pip for 1 lot."""
        info = self._get_symbol_info(symbol)
        if not info:
            return None

        digits = getattr(info, "digits", None)
        if digits is None:
            return None

        pip_size = 0.0001 if digits > 3 else 0.01
        tick_value = (
            getattr(info, "trade_tick_value", None)
            or getattr(info, "trade_tick_value_profit", None)
            or getattr(info, "trade_tick_value_loss", None)
        )
        tick_size = getattr(info, "trade_tick_size", None) or getattr(info, "point", None)

        if tick_value and tick_size and tick_value > 0 and tick_size > 0:
            return float(tick_value) * (pip_size / float(tick_size))

        contract_size = getattr(info, "trade_contract_size", None)
        if not contract_size or contract_size <= 0:
            logger.warning(f"Cannot calculate pip value for {symbol}: missing tick value and contract size")
            return None

        tick = None
        try:
            tick = self.mt5.get_symbol_tick(symbol)
        except Exception:
            tick = None

        price = None
        if tick:
            bid = getattr(tick, "bid", None)
            ask = getattr(tick, "ask", None)
            if bid and ask:
                price = (float(bid) + float(ask)) / 2
            elif bid:
                price = float(bid)
            elif ask:
                price = float(ask)

        clean_symbol = "".join(ch for ch in str(symbol).upper() if ch.isalpha())
        base = clean_symbol[:3]
        quote = clean_symbol[3:6]
        account = self.mt5.get_account_info() or {}
        account_currency = str(account.get("currency") or "USD").upper()

        # Fallback for common USD-denominated symbols. Broker tick metadata is preferred above.
        if quote == account_currency:
            return float(contract_size) * pip_size
        if base == account_currency and price and price > 0:
            return (float(contract_size) * pip_size) / price
        if symbol.upper().startswith("XAU") and quote == account_currency:
            return float(contract_size) * pip_size

        logger.warning(
            f"Cannot accurately calculate pip value for {symbol}: no broker tick value and no {account_currency} conversion path"
        )
        return None

    def _has_hit_daily_profit_cap(self) -> bool:
        """Check if daily profit cap has been reached."""
        try:
            account = self.mt5.get_account_info()
            if not account:
                return False
            
            current_equity = account.get("equity", 0)
            
            # Initialize daily start equity if not set
            if self.daily_start_equity is None:
                self.daily_start_equity = current_equity
                return False
            
            # Check if it's a new day (equity reset or significant change)
            if current_equity < self.daily_start_equity * 0.8:  # Reset if equity dropped >20%
                self.daily_start_equity = current_equity
                return False
            
            daily_profit_pct = (current_equity - self.daily_start_equity) / self.daily_start_equity
            
            if daily_profit_pct >= self.daily_profit_cap:
                logger.info(f"Daily profit cap reached: {daily_profit_pct*100:.2f}% >= {self.daily_profit_cap*100:.1f}%")
                alert_manager.create(
                    "Daily profit cap reached",
                    f"Daily profit is {daily_profit_pct*100:.2f}% against cap {self.daily_profit_cap*100:.1f}%.",
                    severity="success",
                    category="risk",
                    event="daily_profit_cap_reached",
                    metadata={"daily_profit_pct": daily_profit_pct, "daily_profit_cap": self.daily_profit_cap},
                    dedupe_key="daily_profit_cap_reached",
                    cooldown_seconds=3600,
                )
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error checking daily profit cap: {e}")
            return False

    def _round_lot(self, volume: float, lot_step: float) -> float:
        """Round a lot size down to the broker's volume step."""
        try:
            step = float(lot_step or 0.01)
            if step <= 0:
                step = 0.01
            steps = int(float(volume) / step)
            rounded = steps * step
            return round(max(step, rounded), 8)
        except Exception:
            return round(float(volume or self.volume), 2)

    def _round_symbol_lot(self, symbol: str, volume: float) -> float:
        """Round volume using the symbol's broker lot settings."""
        try:
            info = self._get_symbol_info(symbol)
            if not info:
                return self._round_lot(volume, 0.01)
            min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
            max_lot = float(getattr(info, "volume_max", 100) or 100)
            lot_step = float(getattr(info, "volume_step", 0.01) or 0.01)
            rounded = self._round_lot(volume, lot_step)
            return max(min_lot, min(max_lot, rounded))
        except Exception:
            return self._round_lot(volume, 0.01)

    def _get_symbol_min_lot(self, symbol: str) -> float:
        try:
            info = self._get_symbol_info(symbol)
            if not info:
                return 0.01
            return float(getattr(info, "volume_min", 0.01) or 0.01)
        except Exception:
            return 0.01

    def _calculate_volume(self, symbol: str, entry: float, sl: float) -> float:
        """CRITICAL FIX: Calculate lot size with proper error handling and zero checks"""
        if self.position_sizing_mode == "fixed":
            info = self._get_symbol_info(symbol)
            if info:
                min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
                max_lot = float(getattr(info, "volume_max", 100) or 100)
                lot_step = float(getattr(info, "volume_step", 0.01) or 0.01)
                if self.volume < min_lot:
                    logger.error(f"Fixed lot {self.volume} is below broker minimum {min_lot} for {symbol}; rejecting trade")
                    return 0.0
                volume = min(max_lot, self._round_lot(self.volume, lot_step))
            else:
                volume = self._round_lot(self.volume, 0.01)
            logger.debug(f"Fixed lot sizing for {symbol}: volume={volume:.2f}")
            return volume

        equity = self._get_equity() or 0
        if equity <= 0:
            logger.warning(f"Cannot calculate volume for {symbol}: equity={equity}")
            return self.volume

        risk_amount = equity * self.risk_pct
        pip_value = self._get_pip_value(symbol)
        if pip_value is None:
            logger.warning(f"Cannot get pip value for {symbol}")
            return self.volume

        info = self._get_symbol_info(symbol)
        if not info:
            logger.warning(f"Cannot get symbol info for {symbol}")
            return self.volume

        digits = getattr(info, "digits", 5)
        pip_size = 0.0001 if digits > 3 else 0.01
        
        # CRITICAL FIX: Validate pip_size
        if pip_size <= 0:
            logger.error(f"Invalid pip_size for {symbol}: {pip_size}")
            return self.volume
        
        stop_pips = abs(entry - sl) / pip_size
        
        # CRITICAL FIX: Validate stop_pips
        if stop_pips <= 0:
            logger.error(f"Invalid stop distance for {symbol}: entry={entry}, sl={sl}, stop_pips={stop_pips}")
            return self.volume

        risk_per_lot = stop_pips * pip_value
        
        # CRITICAL FIX: Validate risk_per_lot before division
        if risk_per_lot <= 0:
            logger.error(f"Invalid risk_per_lot for {symbol}: {risk_per_lot}")
            return self.volume

        # Now safe to divide
        volume = risk_amount / risk_per_lot
        
        # Respect symbol lot constraints
        min_lot = getattr(info, "volume_min", 0.01)
        max_lot = getattr(info, "volume_max", 100)
        lot_step = getattr(info, "volume_step", 0.01)
        volume = max(min_lot, min(max_lot, volume))
        volume = self._round_lot(volume, lot_step)
        
        logger.debug(f"Calculated volume for {symbol}: {volume:.2f} (risk=${risk_amount:.2f}, per_lot=${risk_per_lot:.2f})")
        return volume

    def _calculate_risk_amount(self, symbol: str, entry: float, sl: float, volume: float) -> float:
        """Calculate the dollar risk amount for a given trade."""
        pip_value = self._get_pip_value(symbol)
        if pip_value is None:
            return 0.0

        info = self._get_symbol_info(symbol)
        if not info:
            return 0.0

        digits = getattr(info, "digits", 5)
        pip_size = 0.0001 if digits > 3 else 0.01
        stop_pips = abs(entry - sl) / pip_size if pip_size else 0
        return stop_pips * pip_value * volume

    def _get_pip_size(self, symbol: str):
        """Return pip size for the symbol (e.g. 0.0001 for EURUSD, 0.01 for JPY pairs)."""
        info = self._get_symbol_info(symbol)
        if not info:
            return None
        digits = getattr(info, "digits", None)
        if digits is None:
            return None
        return 0.0001 if digits > 3 else 0.01

    def _calculate_exposure(self):
        """Estimate current risk exposure of open trades."""
        exposure, _ = self._calculate_exposure_details()
        return exposure

    def _calculate_exposure_details(self):
        """Estimate current risk exposure and return per-position details."""
        positions = self.mt5.get_positions() or []
        exposure = 0.0
        details = []
        for pos in positions:
            symbol = pos.get("symbol")
            entry = pos.get("entry")
            sl = pos.get("sl")
            volume = pos.get("volume")
            if not symbol or entry is None or sl is None or volume is None:
                continue
            risk_amount = self._calculate_risk_amount(symbol, entry, sl, volume)
            exposure += risk_amount
            details.append({
                "symbol": symbol,
                "ticket": pos.get("ticket"),
                "type": pos.get("type"),
                "volume": volume,
                "entry": entry,
                "sl": sl,
                "risk": risk_amount,
            })
        return exposure, details

    def _format_exposure_details(self, details):
        if not details:
            return "no open positions with SL risk"
        return "; ".join(
            f"{item.get('symbol')} {item.get('type')} vol={float(item.get('volume') or 0):.2f} "
            f"risk=${float(item.get('risk') or 0):.2f}"
            for item in details
        )

    def _refresh_license_status(self):
        try:
            result = self.license_manager.validate()
            self.license_status = result.to_dict()
            self.live_trading_disabled = not bool(self.license_status.get("trading_allowed"))
            status = str(self.license_status.get("status") or "unknown")
            days = self.license_status.get("days_remaining")
            key = ((self.license_status.get("license") or {}).get("license_key") or "unlicensed")
            if status == "expired":
                self._license_alert("License expired", "License expired and grace period ended. Live trading is disabled.", "danger", "expired")
            elif status == "grace":
                self._license_alert("License grace period", "License expired but grace period is active. Renew before trading is disabled.", "warning", "grace")
            elif isinstance(days, int) and days <= 7:
                self._license_alert("License expires in 7 days", f"License {key} expires in {days} day(s).", "warning", "7d")
            elif isinstance(days, int) and days <= 30:
                self._license_alert("License expires in 30 days", f"License {key} expires in {days} day(s).", "warning", "30d")
            elif not self.license_status.get("valid"):
                self._license_alert("License invalid", self.license_status.get("reason") or "License validation failed.", "danger", status)
        except Exception as exc:
            self.license_status = {"valid": False, "status": "error", "reason": str(exc), "trading_allowed": False}
            self.live_trading_disabled = True
            self._license_alert("License validation error", str(exc), "danger", "error")
        return self.license_status

    def _license_alert(self, title: str, message: str, severity: str, key: str):
        dedupe_key = f"license:{key}"
        if dedupe_key in self.license_alerts_sent and key not in {"expired", "error"}:
            return
        self.license_alerts_sent.add(dedupe_key)
        alert_manager.create(
            title,
            message,
            severity=severity,
            category="system",
            event="license_status",
            dedupe_key=dedupe_key,
            cooldown_seconds=3600,
        )

    def _can_trade(self):
        """CRITICAL FIX: Determine if new trades are allowed with equity validation"""
        self._refresh_license_status()
        if self.live_trading_disabled:
            reason = (self.license_status or {}).get("reason") or "License is not valid"
            return False, f"License blocked live trading: {reason}"

        # Check cooldown
        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
            logger.info(f"In cooldown. {remaining:.0f} minutes remaining")
            return False, f"In cooldown ({remaining:.0f}m remaining)"

        # Global trade interval disabled: rely on symbol-specific cooldown and exposure checks only

        # Get equity
        equity = self._get_equity()
        
        # CRITICAL FIX: Handle None and check > 0
        if equity is None:
            logger.error("Cannot get account equity")
            return False, "Cannot get account equity"
        
        if equity <= 0:
            logger.critical(f"🚨 ACCOUNT LIQUIDATED! Equity: {equity}. Stopping engine.")
            self.is_running = False  # Stop immediately
            # Log liquidation event
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "LIQUIDATION_ALERT",
                "equity": equity,
            })
            return False, "Account liquidated - stopping engine"

        # Check exposure
        current_exposure, exposure_details = self._calculate_exposure_details()
        max_exposure = equity * self.max_exposure_pct
        
        if current_exposure >= max_exposure:
            logger.info(
                f"Max exposure reached. Current: ${current_exposure:.2f}, Max: ${max_exposure:.2f}. "
                f"Details: {self._format_exposure_details(exposure_details)}"
            )
            return False, f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})"

        return True, "OK"

    def _can_place_pending_orders(self):
        """Check if the bot can place or refresh pending orders without blocking from market-only intervals."""
        self._refresh_license_status()
        if self.live_trading_disabled:
            reason = (self.license_status or {}).get("reason") or "License is not valid"
            return False, f"License blocked pending orders: {reason}"

        if self.cooldown_until and datetime.now() < self.cooldown_until:
            remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
            logger.info(f"Pending order blocked by cooldown. {remaining:.0f} minutes remaining")
            return False, f"Cooldown active ({remaining:.0f}m remaining)"

        equity = self._get_equity()
        if equity is None:
            logger.error("Cannot get account equity for pending orders")
            return False, "Cannot get account equity"
        if equity <= 0:
            logger.critical(f"🚨 ACCOUNT LIQUIDATED! Equity: {equity}. Stopping engine.")
            self.is_running = False
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "LIQUIDATION_ALERT",
                "equity": equity,
            })
            return False, "Account liquidated - stopping engine"

        current_exposure, exposure_details = self._calculate_exposure_details()
        max_exposure = equity * self.max_exposure_pct
        if current_exposure >= max_exposure:
            logger.info(
                f"Pending order blocked: max exposure reached. Current: ${current_exposure:.2f}, Max: ${max_exposure:.2f}. "
                f"Details: {self._format_exposure_details(exposure_details)}"
            )
            return False, f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})"

        return True, "OK"

    # ========== GLOBAL TRADE REGISTRY METHODS ===========

    def _check_signal_lockout(self, symbol: str, is_addon: bool = False, strategy: str | None = None) -> tuple[bool, str]:
        """Check if a symbol is locked out from new trades due to active positions or cooldown.

        News ladder add-ons are attached to an existing active trade and should not be blocked by the
        symbol-level trade slot limit.
        """
        if is_addon or (strategy and strategy == "news_ladder"):
            return True, "Addon bypass lockout"

        if not self.signal_lockout_enabled:
            return True, "Lockout disabled"

        with self._trades_lock:
            registry = self.trade_registry.get(symbol, {
                "active_trades": 0,
                "last_trade_time": None,
                "cooldown_until": None
            })

            # Check active trades limit
            if registry["active_trades"] >= self.max_trades_per_symbol:
                return False, f"Max trades reached ({registry['active_trades']}/{self.max_trades_per_symbol})"

            # Check cooldown period
            if registry["cooldown_until"] and datetime.now() < registry["cooldown_until"]:
                remaining = (registry["cooldown_until"] - datetime.now()).total_seconds() / 60
                return False, f"Cooldown active ({remaining:.1f}m remaining)"

            return True, "OK"

    def _should_use_market_execution(self, signal: dict, scalp_data: dict, ensemble_decision: dict = None) -> bool:
        """Determine whether a signal should be executed as a market trade."""
        score = float(scalp_data.get("score", 0.0)) if scalp_data else 0.0
        conviction = float(ensemble_decision.get("conviction", 0.0)) if ensemble_decision else 0.0
        confluence = float(signal.get("confluence_score", 0.0))
        setup_score = float((signal.get("setup_score") or {}).get("score", 0.0))

        if signal.get("early_entry") and setup_score >= self.early_entry_min_score:
            return True
        if setup_score >= max(0.55, self.early_entry_min_score):
            return True
        if confluence >= 0.70:
            return True
        if score >= self.market_execution_score_threshold:
            return True
        if score >= self.market_execution_score_threshold + 0.10:
            return True
        if conviction >= self.market_execution_conviction_threshold and score >= max(0.45, self.market_execution_score_threshold - 0.10):
            return True
        if conviction >= self.market_execution_conviction_threshold + 0.10 and score >= 0.40:
            return True
        if confluence >= 0.55 and score >= 0.45:
            return True
        return False

    def _refresh_signal_for_market_execution(self, signal: dict) -> tuple[bool, str]:
        """Rebuild market order levels around the current tick while preserving signal R:R."""
        symbol = signal.get("symbol")
        action = str(signal.get("action", "")).upper()
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")

        if not symbol or action not in ["BUY", "SELL"] or entry is None or sl is None or tp is None:
            return False, "Cannot refresh market levels; missing symbol/action/entry/SL/TP"

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            return False, f"Cannot refresh market levels; no tick for {symbol}"

        price = float(tick.ask if action == "BUY" else tick.bid)
        risk_distance = abs(float(entry) - float(sl))
        reward_distance = abs(float(tp) - float(entry))
        if risk_distance <= 0 or reward_distance <= 0:
            return False, "Cannot refresh market levels; invalid original risk/reward distance"

        original_entry = float(entry)
        if action == "BUY":
            signal["entry"] = price
            signal["sl"] = price - risk_distance
            signal["tp"] = price + reward_distance
        else:
            signal["entry"] = price
            signal["sl"] = price + risk_distance
            signal["tp"] = price - reward_distance

        signal["market_refresh"] = {
            "original_entry": original_entry,
            "price": price,
            "risk_distance": risk_distance,
            "reward_distance": reward_distance,
        }
        return True, f"Market levels refreshed from {original_entry:.5f} to {price:.5f}"

    def _register_trade_open(self, symbol: str):
        """Register a new trade opening in the global registry."""
        with self._trades_lock:
            if symbol not in self.trade_registry:
                self.trade_registry[symbol] = {
                    "active_trades": 0,
                    "last_trade_time": None,
                    "cooldown_until": None
                }

            registry = self.trade_registry[symbol]
            registry["active_trades"] += 1
            registry["last_trade_time"] = datetime.now()

            self.add_logic(symbol, f"Trade registered in Global Registry (active: {registry['active_trades']})", level="info")

    def _register_trade_close(self, symbol: str):
        """Register a trade closing in the global registry."""
        with self._trades_lock:
            if symbol in self.trade_registry:
                registry = self.trade_registry[symbol]
                if registry["active_trades"] > 0:
                    registry["active_trades"] -= 1

                    # Set cooldown if this was the last active trade
                    if registry["active_trades"] == 0 and self.trade_cooldown_minutes > 0:
                        registry["cooldown_until"] = datetime.now() + timedelta(minutes=self.trade_cooldown_minutes)
                        self.add_logic(symbol, f"Cooldown activated ({self.trade_cooldown_minutes}m)", level="info")

                    self.add_logic(symbol, f"Trade removed from Global Registry (active: {registry['active_trades']})", level="info")

    def _get_registry_status(self, symbol: str = None) -> dict:
        """Get the current status of the trade registry for dashboard display."""
        with self._trades_lock:
            if symbol:
                registry = self.trade_registry.get(symbol, {
                    "active_trades": 0,
                    "last_trade_time": None,
                    "cooldown_until": None
                })
                return {
                    "symbol": symbol,
                    "active_trades": registry["active_trades"],
                    "max_trades": self.max_trades_per_symbol,
                    "cooldown_active": registry["cooldown_until"] is not None and datetime.now() < registry["cooldown_until"],
                    "cooldown_remaining": None if not registry["cooldown_until"] else
                        max(0, (registry["cooldown_until"] - datetime.now()).total_seconds() / 60),
                    "last_trade": registry["last_trade_time"].isoformat() if registry["last_trade_time"] else None
                }
            else:
                # Return status for all symbols
                return {
                    symbol: self._get_registry_status(symbol)
                    for symbol in self.symbols
                }

    def _calculate_expected_r(self, signal: dict):
        """Estimate R-multiple (R = reward/risk) for a given signal."""
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")
        action = str(signal.get("action", "")).upper()

        if entry is None or sl is None or tp is None or action not in ["BUY", "SELL"]:
            return None

        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk == 0:
            return None
        return reward / risk

    def _env_key_for_symbol(self, prefix: str, symbol: str) -> str:
        normalized = "".join(ch for ch in str(symbol or "").upper() if ch.isalnum())
        return f"{prefix}_{normalized}"

    def _get_symbol_min_profit_pips(self, symbol: str, signal: dict | None = None) -> float:
        override = os.getenv(self._env_key_for_symbol("MIN_PROFIT_PIPS", symbol))
        if override is not None:
            try:
                return float(override)
            except Exception:
                pass

        clean_symbol = str(symbol or "").upper()
        if "XAU" in clean_symbol or "GOLD" in clean_symbol:
            return float(os.getenv("MIN_PROFIT_PIPS_XAU", 30))
        if clean_symbol.startswith("NZDUSD") or clean_symbol.startswith("AUDUSD") or clean_symbol.startswith("USDCAD"):
            return float(os.getenv("MIN_PROFIT_PIPS_FX", 2))
        if clean_symbol.endswith("JPY"):
            return float(os.getenv("MIN_PROFIT_PIPS_JPY", 1))
        if signal and self._is_scalp_signal(signal):
            return float(os.getenv("MIN_PROFIT_PIPS_SCALP", 2))
        return self.min_profit_pips

    def _get_symbol_max_entry_drift_pips(self, symbol: str) -> float:
        override = os.getenv(self._env_key_for_symbol("MAX_ENTRY_DRIFT_PIPS", symbol))
        if override is not None:
            try:
                return float(override)
            except Exception:
                pass

        clean_symbol = str(symbol or "").upper()
        if "XAU" in clean_symbol or "GOLD" in clean_symbol:
            return float(os.getenv("MAX_ENTRY_DRIFT_PIPS_XAU", 250))
        return self.max_entry_drift_pips

    def _is_scalp_signal(self, signal: dict) -> bool:
        style = str(signal.get("trade_style", "")).lower()
        scalp = signal.get("scalp_potential") or {}
        label = str(scalp.get("label", "")).lower()
        return "scalp" in style or "scalp" in label

    def _get_required_r(self, signal: dict) -> float:
        if self._is_scalp_signal(signal):
            return self.min_expected_r_scalp
        return self.min_expected_r

    def _get_target_r(self, signal: dict) -> float:
        if self._is_scalp_signal(signal):
            return self.take_profit_r_multiplier_scalp
        return self.take_profit_r_multiplier

    def _normalize_signal_levels_to_rr(self, signal: dict) -> tuple[bool, str]:
        """Keep strategy SL, then derive TP from the configured target R multiple."""
        action = str(signal.get("action", "")).upper()
        entry = signal.get("entry")
        sl = signal.get("sl")
        if action not in ["BUY", "SELL"] or entry is None or sl is None:
            return False, "Cannot calculate TP/SL; missing action, entry, or SL"

        entry = float(entry)
        sl = float(sl)
        risk_distance = abs(entry - sl)
        if risk_distance <= 0:
            return False, "Cannot calculate TP; invalid SL distance"
        if action == "BUY" and sl >= entry:
            return False, "Cannot calculate BUY TP; SL must be below entry"
        if action == "SELL" and sl <= entry:
            return False, "Cannot calculate SELL TP; SL must be above entry"

        target_r = self._get_target_r(signal)
        tp = entry + (risk_distance * target_r) if action == "BUY" else entry - (risk_distance * target_r)
        original_tp = signal.get("tp")
        signal["entry"] = entry
        signal["sl"] = sl
        signal["tp"] = tp
        signal["target_r"] = target_r
        signal["rr_normalized"] = True
        signal["original_tp"] = original_tp
        return True, f"TP/SL normalized to {target_r:.2f}R"

    def _get_spread_safety(self, signal: dict) -> tuple[bool, str]:
        spread = signal.get("spread_safety")
        if not spread and signal.get("setup_score"):
            spread = signal["setup_score"].get("spread")
        if not spread:
            return True, "Spread data unavailable"
        if spread.get("safe") is False:
            reason = spread.get("description", "Spread unsafe")
            alert_manager.create(
                "Spread unsafe",
                f"{signal.get('symbol', 'Unknown')}: {reason}",
                severity="warning",
                category="execution",
                symbol=signal.get("symbol"),
                event="spread_unsafe",
                metadata={"spread": spread},
                dedupe_key=f"spread:{signal.get('symbol')}:{reason}",
                cooldown_seconds=120,
            )
            return False, reason
        return True, spread.get("description", "Spread safe")

    def _is_price_near_entry(self, signal: dict) -> tuple[bool, str]:
        entry = signal.get("entry")
        sl = signal.get("sl")
        current = signal.get("current_price")
        symbol = signal.get("symbol")
        if current is None and symbol:
            tick = self.mt5.get_symbol_tick(symbol)
            if tick:
                current = getattr(tick, "ask", None) if signal.get("action") == "BUY" else getattr(tick, "bid", None)

        if entry is None or sl is None or current is None:
            return True, "No current price drift check"

        risk_distance = abs(entry - sl)
        if risk_distance <= 0:
            return False, "Invalid entry drift risk distance"

        drift = abs(float(current) - float(entry))
        pip_size = self._get_pip_size(symbol) if symbol else None
        pip_drift_limit = (pip_size or 0) * self._get_symbol_max_entry_drift_pips(symbol)
        max_drift = max(risk_distance * self.max_entry_drift_pct, pip_drift_limit)
        if drift > max_drift:
            return False, f"Price drift too large ({drift:.5f} > {max_drift:.5f})"
        return True, f"Price drift acceptable ({drift:.5f} <= {max_drift:.5f})"

    def _execution_gate(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, str]:
        conviction = float((ensemble_decision or {}).get("conviction", 0.0))
        scalp_score = float((signal.get("scalp_potential") or {}).get("score", 0.0))
        setup = signal.get("setup_score") or {}
        spread_ok, spread_reason = self._get_spread_safety(signal)
        drift_ok, drift_reason = self._is_price_near_entry(signal)
        expected_r = self._calculate_expected_r(signal)

        if not spread_ok:
            return False, spread_reason
        if not drift_ok:
            return False, drift_reason
        if setup_value < self.min_setup_score:
            return False, f"Quality gate failed: setup_score={setup_value:.3f} < MIN_SETUP_SCORE={self.min_setup_score:.3f}"
        if conviction < self.min_conviction:
            return False, f"Quality gate failed: conviction={conviction:.3f} < MIN_CONVICTION={self.min_conviction:.3f}"
        if expected_r is None or expected_r < self.min_rr:
            return False, f"Quality gate failed: R:R={expected_r if expected_r is not None else 'N/A'} < MIN_RR={self.min_rr:.2f}"

        return True, (
            f"Quality gate passed: setup_score={setup_value:.3f}, conviction={conviction:.3f}, "
            f"R:R={expected_r:.2f}, scalp_score={scalp_score:.3f}, {spread_reason}, {drift_reason}"
        )

    def _backtest_validation_gate(self) -> tuple[bool, str]:
        """Block live trading when the latest historical validation file is not acceptable."""
        if self.backtest_mode or not self.enforce_backtest_validation:
            return True, "Backtest validation gate disabled for this run"
        path = os.path.join("data", "trades.csv")
        if not os.path.exists(path):
            return False, f"Backtest validation blocked live trading: {path} not found"
        try:
            import csv
            with open(path, "r", encoding="utf-8", newline="") as handle:
                trades = list(csv.DictReader(handle))
            metrics = summarize_performance(trades)
        except Exception as exc:
            return False, f"Backtest validation blocked live trading: could not read metrics ({exc})"

        sample = int(metrics.get("total_trades") or 0)
        expectancy = float(metrics.get("expectancy") or 0.0)
        profit_factor = metrics.get("profit_factor")
        profit_factor_value = float("inf") if profit_factor is None and expectancy > 0 else 0.0 if profit_factor is None else float(profit_factor)
        min_sample = int(os.getenv("VALIDATION_MIN_SAMPLE", 30))
        if sample < min_sample:
            return False, f"Backtest validation blocked live trading: sample {sample} < {min_sample}"
        if expectancy <= 0:
            return False, f"Backtest validation blocked live trading: expectancy {expectancy:.2f} <= 0"
        if profit_factor_value < self.min_backtest_profit_factor:
            return False, f"Backtest validation blocked live trading: profit factor {profit_factor_value:.2f} < {self.min_backtest_profit_factor:.2f}"
        return True, f"Backtest validation passed: sample={sample}, expectancy={expectancy:.2f}, PF={profit_factor_value:.2f}"

    def _ict_execution_gate(self, signal: dict) -> tuple[bool, str]:
        if not self.ict_enabled:
            return True, "ICT gate disabled"
        ict = signal.get("ict") or {}
        components = ict.get("components") or {}
        session = str(ict.get("session_name") or "Unknown").replace(" ", "")
        action = signal.get("action")
        expected_r = self._calculate_expected_r(signal)

        blockers = []
        if self.ict_allowed_sessions and session not in self.ict_allowed_sessions:
            blockers.append(f"session {session} not allowed")
        if expected_r is None or expected_r < self.ict_min_risk_reward:
            blockers.append(f"R:R {expected_r if expected_r is not None else 'N/A'} < {self.ict_min_risk_reward:.2f}")
        if not components.get("htf_bias_agrees"):
            blockers.append("HTF bias disagrees")
        if self.ict_require_liquidity_sweep and not components.get("liquidity_sweep_detected"):
            blockers.append("no liquidity sweep")
        if self.ict_require_bos_or_choch and not components.get("bos_or_choch_detected"):
            blockers.append("no BOS/CHoCH")
        if self.ict_require_fvg_retest and not components.get("fvg_retest_detected"):
            blockers.append("no FVG retest")
        if not components.get("fvg_present"):
            blockers.append("no valid FVG present")
        if not components.get("fvg_retest_detected"):
            blockers.append("no valid FVG confirmation")
        if not (components.get("fvg_retest_detected") or components.get("order_block_valid")):
            blockers.append("no FVG retest or valid order block")
        if self.wait_for_retest and not components.get("order_block_valid") and not components.get("fvg_retest_detected"):
            blockers.append("WAIT_FOR_RETEST requires retrace into FVG or order block")

        day_key = datetime.utcnow().strftime("%Y-%m-%d")
        session_key = (day_key, session)
        session_count = self.ict_session_trades.get(session_key, 0)
        if session_count >= self.ict_max_trades_per_session:
            blockers.append(f"ICT session trade cap reached ({session_count}/{self.ict_max_trades_per_session})")

        if blockers:
            summary = "; ".join(blockers)
            logger.info(f"ICT blocked {signal.get('symbol')} {action}: {summary}")
            return False, f"ICT blocked: {summary}"
        reason = ict.get("entry_reason") or "ICT components aligned"
        return True, f"ICT approved: {reason}; R:R={expected_r:.2f}"

    def _mark_ict_session_trade(self, signal: dict):
        if not self.ict_enabled:
            return
        session = str((signal.get("ict") or {}).get("session_name") or "Unknown").replace(" ", "")
        day_key = datetime.utcnow().strftime("%Y-%m-%d")
        key = (day_key, session)
        self.ict_session_trades[key] = self.ict_session_trades.get(key, 0) + 1

    def _professional_execution_gate(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, str]:
        """Final discretionary-style filter: dashboard may watch C/D setups, execution only takes clean ones."""
        if not self.professional_gate_enabled:
            return True, "Professional gate disabled"

        setup = signal.get("setup_score") or {}
        grade = str(setup.get("grade") or "D").upper()
        archetype = str(setup.get("archetype") or "Context Watch")
        session = signal.get("session_bias") or setup.get("session_bias") or {}
        session_score = float(session.get("score", 0.0) or 0.0)
        conviction = float((ensemble_decision or {}).get("conviction", 0.0))
        scalp = self._is_scalp_signal(signal)
        scalp_score = float((signal.get("scalp_potential") or {}).get("score", 0.0))
        components = setup.get("components") or []
        passed = {c.get("key") for c in components if c.get("passed")}
        structural_pass = bool({"liquidity_sweep", "mss", "displacement"}.intersection(passed))

        grade_rank = {"A": 4, "B": 3, "C": 2, "D": 1}
        required_rank = grade_rank.get(self.min_execution_grade, 3)
        grade_ok = grade_rank.get(grade, 1) >= required_rank
        c_scalp_ok = (
            self.allow_c_scalps
            and grade == "C"
            and scalp
            and scalp_score >= 0.75
            and session_score >= self.min_session_score_for_scalp
            and structural_pass
        )

        if self.block_context_watch_trades and archetype == "Context Watch":
            return False, "Professional gate: Context Watch is watch-only"
        if not structural_pass:
            return False, "Professional gate: no liquidity sweep, MSS/BOS, or displacement"
        if not (grade_ok or c_scalp_ok):
            return False, f"Professional gate: Grade {grade} below execution grade {self.min_execution_grade}"
        if setup_value < self.min_professional_score and not c_scalp_ok:
            return False, f"Professional gate: setup score {setup_value:.2f} < {self.min_professional_score:.2f}"
        if conviction < self.min_professional_conviction and grade != "A":
            return False, f"Professional gate: conviction {conviction:.2f} < {self.min_professional_conviction:.2f}"
        if session_score < self.min_session_score_for_trade and grade != "A":
            return False, f"Professional gate: weak session score {session_score:.2f}"
        if scalp and session_score < self.min_session_score_for_scalp and grade != "A":
            return False, f"Professional gate: scalp blocked outside liquid session ({session_score:.2f})"

        return True, (
            f"Professional gate passed: Grade {grade}, {archetype}, "
            f"score={setup_value:.2f}, conviction={conviction:.2f}, session={session_score:.2f}"
        )

    def _event_execution_gate(self, signal: dict) -> tuple[bool, str]:
        """Block trap-chasing and manage reduced-risk post-news entries."""
        false_move = signal.get("false_move") or (signal.get("setup_score") or {}).get("false_move") or {}
        news_move = signal.get("news_move") or (signal.get("setup_score") or {}).get("news_move") or {}

        if self.false_move_detection_enabled:
            fm_type = false_move.get("type")
            fm_safe = false_move.get("safe", True)
            fm_direction = false_move.get("direction")
            action = str(signal.get("action") or "").upper()
            aligned_action = "BUY" if fm_direction == "Bullish" else "SELL" if fm_direction == "Bearish" else None
            if fm_type in ["FAILED_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL"] and aligned_action and action != aligned_action:
                return False, f"False-move gate: signal is chasing against {fm_direction} trap reversal"
            if fm_type in ["REAL_BREAKOUT", "BREAKOUT_UNCONFIRMED"] and not fm_safe:
                return False, f"False-move gate: {fm_type.replace('_', ' ').title()} lacks aligned follow-through"

        if self.news_mode_enabled:
            mode = news_move.get("mode", "NORMAL")
            plan = news_move.get("plan", "NORMAL")
            safe = news_move.get("safe", True)
            if mode == "ACTIVE" and self.news_block_unsafe:
                return False, f"News gate: {plan} - {news_move.get('description', 'unsafe event spike')}"
            if not safe and plan in ["WAIT_SPREAD", "WAIT_RETEST"] and self.news_block_unsafe:
                return False, f"News gate: {news_move.get('description', 'news spread unsafe')}"
            if mode == "FOLLOW_RETEST":
                if not self.news_allow_retest_follow:
                    return False, "News gate: post-news retest entries disabled"
                signal["risk_multiplier"] = min(float(signal.get("risk_multiplier") or 1.0), self.news_risk_multiplier)
                return True, f"News gate: post-news follow allowed at {self.news_risk_multiplier:.0%} risk"

        return True, "False-move/news gate passed"

    def _strict_quality_gate(self, signal: dict, ensemble_decision: dict, setup_value: float) -> tuple[bool, str]:
        """Aggressive quality filter to reduce chop and weak-structure overtrading."""
        if not self.strict_quality_gate_enabled:
            return True, "Strict quality gate disabled"

        setup = signal.get("setup_score") or {}
        policy = regime_policy(signal.get("market_regime") or setup.get("market_regime"))
        threshold_delta = float(policy.get("score_threshold_delta") or 0.0)
        components = setup.get("components") or []
        passed_keys = {str(c.get("key") or "").lower() for c in components if c.get("passed")}
        points = float(setup.get("points") or sum(float(c.get("points") or 0) for c in components))
        max_points = float(setup.get("max_points") or sum(float(c.get("max_points") or 0) for c in components) or 1)
        structural_score = points / max_points if max_points > 0 else 0.0
        conviction = float((ensemble_decision or {}).get("conviction") or signal.get("conviction") or 0.0)
        session = signal.get("session_bias") or setup.get("session_bias") or {}
        session_score = float(session.get("score") or 0.0)
        displacement = signal.get("displacement") or setup.get("displacement") or {}
        market_quality = signal.get("market_quality") or setup.get("market_quality") or {}
        spread = signal.get("spread_safety") or setup.get("spread") or {}
        htf = signal.get("higher_timeframe_bias") or setup.get("higher_timeframe_bias") or {}
        action = str(signal.get("action") or "").upper()
        htf_action = "BUY" if htf.get("direction") == "Bullish" else "SELL" if htf.get("direction") == "Bearish" else None
        body_ratio = float(displacement.get("body_ratio") or displacement.get("ratio") or 0.0)
        close_quality = float(displacement.get("close_quality") or market_quality.get("close_quality") or 0.0)
        volatility_quality = float(market_quality.get("volatility_quality") or 0.0)
        market_score = float(market_quality.get("score") or 0.0)
        liquidity_ok = bool({"liquidity_sweep", "mss", "ob_fvg"}.intersection(passed_keys))
        displacement_ok = "displacement" in passed_keys and not displacement.get("fake")
        spread_pips = spread.get("spread_pips")
        max_spread = spread.get("max_spread_pips")
        spread_ratio = (float(spread_pips) / float(max_spread)) if spread_pips is not None and max_spread else 0.0

        required_professional_score = min(0.95, self.min_professional_score + threshold_delta)
        required_structural_score = min(0.95, self.min_structural_quality_score + (threshold_delta * 0.5))
        if conviction < self.execution_conviction_threshold and setup_value < required_professional_score:
            return False, f"Strict quality: low conviction {conviction:.2f} and regime-adjusted setup score {setup_value:.2f} < {required_professional_score:.2f}"
        if structural_score < required_structural_score:
            return False, f"Strict quality: structural score {structural_score:.2f} < regime threshold {required_structural_score:.2f}"
        if not displacement_ok or body_ratio < self.min_displacement_body_ratio:
            return False, f"Strict quality: weak displacement body ratio {body_ratio:.2f} < {self.min_displacement_body_ratio:.2f}"
        if close_quality < self.min_candle_close_quality:
            return False, f"Strict quality: weak candle close quality {close_quality:.2f} < {self.min_candle_close_quality:.2f}"
        if volatility_quality < self.min_volatility_quality or market_score < self.min_market_quality_score:
            return False, f"Strict quality: poor volatility/market quality ({volatility_quality:.2f}/{market_score:.2f})"
        if market_quality.get("chop") or market_quality.get("low_momentum"):
            return False, f"Strict quality: anti-chop blocked - {market_quality.get('description', 'ranging or low momentum')}"
        if market_quality.get("fake_displacement"):
            return False, "Strict quality: fake displacement blocked"
        if market_quality.get("noisy") or spread_ratio >= 0.85 or spread.get("safe") is False:
            return False, f"Strict quality: noisy spread/close condition ({spread.get('description', 'spread not clean')})"
        if session_score < self.min_session_score_for_trade:
            return False, f"Strict quality: session score {session_score:.2f} < {self.min_session_score_for_trade:.2f}"
        if self.require_htf_agreement and htf_action and action and htf_action != action:
            return False, f"Strict quality: HTF bias {htf.get('direction')} disagrees with {action}"
        if self.require_htf_agreement and not htf_action:
            return False, "Strict quality: no HTF agreement"
        if self.require_liquidity_context and not liquidity_ok:
            return False, "Strict quality: weak liquidity context"
        persistence_ok, persistence_reason = self._check_confidence_persistence(signal, setup_value, conviction)
        if not persistence_ok:
            return False, persistence_reason

        return True, (
            f"Strict quality passed: structure={structural_score:.2f}, body={body_ratio:.2f}, "
            f"close={close_quality:.2f}, market={market_score:.2f}, regime={policy.get('regime')}, persistence ok"
        )

    def _check_confidence_persistence(self, signal: dict, setup_value: float, conviction: float) -> tuple[bool, str]:
        symbol = signal.get("symbol")
        action = signal.get("action")
        setup = signal.get("setup_score") or {}
        archetype = setup.get("archetype") or signal.get("nature") or "setup"
        key = f"{symbol}:{action}:{archetype}"
        now = datetime.now()
        floor = max(self.execution_setup_score_threshold, self.early_entry_min_score)
        confident = setup_value >= floor and conviction >= max(0.20, self.execution_conviction_threshold - 0.15)
        state = self.signal_persistence.get(key, {"count": 0, "last_seen": None})
        last_seen = state.get("last_seen")
        if last_seen and (now - last_seen).total_seconds() > max(180, self.scan_interval_seconds * 4):
            state = {"count": 0, "last_seen": None}
        state["count"] = int(state.get("count") or 0) + 1 if confident else 0
        state["last_seen"] = now
        self.signal_persistence[key] = state
        if state["count"] < self.min_confidence_persistence:
            return False, f"Strict quality: confidence persistence {state['count']}/{self.min_confidence_persistence}"
        return True, f"Confidence persisted {state['count']} scans"

    def _compute_scalp_potential(self, signal: dict):
        """Compute scalp potential score and classification."""
        from technical_analysis import calculate_scalp_potential

        if not signal or "entry" not in signal:
            return {
                "score": 0.0,
                "label": "Unknown",
                "risk_pips": 0,
                "reward_pips": 0,
                "r_ratio": 0,
            }

        scalp = calculate_scalp_potential(signal)
        return scalp

    def _classify_trade_style(self, signal: dict, scalp_data: dict) -> str:
        """Classify the trade name for logging and analysis."""
        action = signal.get("action", "UNKNOWN").upper()
        label = scalp_data.get("label", "Opportunity") if scalp_data else "Opportunity"

        if signal.get("order_block"):
            style = "Order Block"
        elif signal.get("divergence") and signal["divergence"].get("type") in ["Bullish", "Bearish"]:
            style = "Divergence"
        elif signal.get("structure_break"):
            style = "Structure"
        elif signal.get("liquidity_zone"):
            style = "Liquidity"
        elif "Scalp" in label:
            style = "Scalp"
        elif "Momentum" in label:
            style = "Momentum"
        elif "Trend" in label:
            style = "Trend"
        else:
            style = "Setup"

        if action == "BUY":
            return f"Long {style}"
        if action == "SELL":
            return f"Short {style}"
        return f"{action.title()} {style}"

    def _is_signal_big_enough(self, signal: dict):
        """Check if a signal has enough pip distance to justify a trade."""
        try:
            symbol = signal.get("symbol")
            entry = signal.get("entry")
            sl = signal.get("sl")
            tp = signal.get("tp")
            action = str(signal.get("action", "")).upper()
            pip_size = self._get_pip_size(symbol) if symbol else None

            if not symbol or entry is None or sl is None or tp is None or pip_size is None:
                return False, "Signal missing symbol, entry, SL, TP, or pip size"

            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk <= 0 or reward <= 0:
                return False, "Invalid risk/reward distance"

            reward_pips = reward / pip_size
            risk_pips = risk / pip_size
            expected_r = reward / risk

            min_profit_pips = self._get_symbol_min_profit_pips(symbol, signal)
            min_expected_r = self._get_required_r(signal)
            spread_ok, spread_reason = self._get_spread_safety(signal)

            if not spread_ok:
                return False, spread_reason
            if reward_pips < min_profit_pips:
                return False, f"Reward too small ({reward_pips:.1f}p < {min_profit_pips:.1f}p)"
            if expected_r < min_expected_r:
                return False, f"R:R too low ({expected_r:.2f}R < {min_expected_r:.2f}R)"
            if action == "BUY" and not (sl < entry < tp):
                return False, "BUY levels invalid; expected SL < entry < TP"
            if action == "SELL" and not (tp < entry < sl):
                return False, "SELL levels invalid; expected TP < entry < SL"

            return True, f"Signal accepted ({reward_pips:.1f}p reward, {risk_pips:.1f}p risk, {expected_r:.2f}R, {spread_reason})"
        except Exception as e:
            logger.error(f"Signal size filter error: {e}")
            return False, f"Signal size filter error: {e}"

    def _record_closed_trade(self, symbol: str, profit: float, risk: float, reason: str = "Closed", metadata: dict | None = None):
        r = profit / risk if risk and risk != 0 else None
        metadata = metadata or {}
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event": "TRADE_CLOSED",
            "symbol": symbol,
            "profit": profit,
            "risk": risk,
            "r": r,
            "reason": reason,
            **metadata,
        }
        self.logger._save_log(entry)
        try:
            record_forward_trade({**entry, **self.strategy_context})
        except Exception as e:
            logger.warning(f"Forward validation record failed: {e}")
        self.trade_journal.append(entry)
        if profit > 0:
            close_event = "tp_hit" if reason == "TP/SL Hit" else "trade_closed"
            title = "TP hit" if close_event == "tp_hit" else "Trade closed"
            severity = "success"
        elif profit < 0:
            close_event = "sl_hit" if reason == "TP/SL Hit" else "trade_closed"
            title = "SL hit" if close_event == "sl_hit" else "Trade closed"
            severity = "danger"
        else:
            close_event = "trade_closed"
            title = "Trade closed"
            severity = "info"
        alert_manager.create(
            title,
            f"{symbol}: {reason}, P&L={profit:.2f}",
            severity=severity,
            category="trade",
            symbol=symbol,
            event=close_event,
            metadata={"profit": profit, "risk": risk, "r": r, "reason": reason},
        )

    def _get_realized_profit_today(self) -> float:
        """Return realized P/L from today's closed trade logs."""
        try:
            logs = self.logger.get_logs()
            return sum(
                float(log.get("profit") or 0)
                for log in logs
                if log.get("event") == "TRADE_CLOSED"
            )
        except Exception as e:
            logger.error(f"Error calculating realized profit: {e}")
            return 0.0

    def _compute_bot_score(self, market_open=None):
        """Return a practical readiness score for dashboard monitoring."""
        components = []

        def add(name, value, weight, note):
            value = max(0.0, min(float(value), float(weight)))
            components.append({
                "name": name,
                "value": round(value, 2),
                "weight": weight,
                "pct": round((value / weight) * 100, 1) if weight else 0,
                "note": note,
            })

        connected = bool(getattr(self.mt5, "is_connected", False))
        broker_name = self.broker_profile.get("name") or self.broker_profile.get("broker_type") or "Broker"
        add("Connection", 12 if connected else 0, 12, f"{broker_name} connected" if connected else f"{broker_name} disconnected")
        add("Runtime", 8 if self.is_running else 0, 8, "Engine running" if self.is_running else "Engine stopped")

        if market_open is None:
            market_open = self._is_market_open()
        add("Market", 5 if market_open else 3, 5, "Market open" if market_open else "Market closed; signal snapshots only")

        if self.last_scan_at:
            age = max(0.0, (datetime.utcnow() - self.last_scan_at).total_seconds())
            expected = max(float(self.scan_interval_seconds or 3) * 3, 15.0)
            freshness = max(0.0, 1.0 - min(age / (expected * 2), 1.0))
            scan_value = 15 * freshness
            scan_note = f"Last scan {int(age)}s ago"
        else:
            scan_value = 3 if not self.is_running else 5
            scan_note = "No scan recorded yet"
        add("Scan Freshness", scan_value, 15, scan_note)

        risk_value = 0
        risk_notes = []
        if self.position_sizing_mode == "fixed":
            risk_value += 7
            risk_notes.append(f"fixed {self.volume:.2f} lots")
        elif 0 < self.risk_pct <= 0.01:
            risk_value += 7
            risk_notes.append("risk <= 1%")
        elif self.risk_pct <= 0.02:
            risk_value += 5
            risk_notes.append("risk <= 2%")
        elif self.risk_pct <= 0.03:
            risk_value += 3
            risk_notes.append("risk elevated")
        else:
            risk_notes.append("risk too high")
        if 0 < self.max_exposure_pct <= 0.05:
            risk_value += 5
            risk_notes.append("exposure guarded")
        elif self.max_exposure_pct <= 0.10:
            risk_value += 3
            risk_notes.append("exposure moderate")
        if self.signal_lockout_enabled and self.max_trades_per_symbol <= 1:
            risk_value += 4
            risk_notes.append("symbol lockout strict")
        elif self.signal_lockout_enabled:
            risk_value += 2
            risk_notes.append("symbol lockout enabled")
        if not self.killed.get("all"):
            risk_value += 2
            risk_notes.append("kill switch ready")
        if self.daily_profit_cap > 0:
            risk_value += 2
            risk_notes.append("daily cap enabled")
        add("Risk Guardrails", risk_value, 20, ", ".join(risk_notes) or "risk config unavailable")

        management_value = 3
        management_notes = ["trailing SL"]
        if self.trailing_tp_enabled:
            management_value += 4
            management_notes.append("trailing TP")
        if self.partial_tp_enabled:
            management_value += 4
            management_notes.append("partial TP")
        if self.reverse_profit_exit_enabled:
            management_value += 4
            management_notes.append("reverse exit")
        if self.professional_gate_enabled:
            management_value += 5
            management_notes.append("professional gate")
        add("Trade Management", management_value, 20, ", ".join(management_notes))

        candidates = list(self.future_trades[-30:]) + list(self.recent_signals[-20:])
        best_score = 0.0
        best_grade = "-"
        for signal in candidates:
            setup = signal.get("setup_score") or {}
            score = float(setup.get("score") or signal.get("confluence_score") or signal.get("conviction") or 0.0)
            if score > best_score:
                best_score = score
                best_grade = str(setup.get("grade") or "-")
        if candidates:
            signal_value = 20 * max(0.0, min(best_score, 1.0))
            signal_note = f"Best candidate {best_score:.2f} grade {best_grade}"
        else:
            signal_value = 6
            signal_note = "No current candidates"
        add("Signal Quality", signal_value, 20, signal_note)

        total = round(sum(c["value"] for c in components), 1)
        if total >= 85:
            grade, label = "A", "Operationally strong"
        elif total >= 72:
            grade, label = "B", "Tradeable with discipline"
        elif total >= 58:
            grade, label = "C", "Watch carefully"
        elif total >= 40:
            grade, label = "D", "Weak readiness"
        else:
            grade, label = "F", "Do not rely on automation"

        return {
            "score": total,
            "grade": grade,
            "label": label,
            "components": components,
            "summary": f"{grade} ({total:.1f}/100) - {label}",
        }

    def get_enriched_positions(self):
        """Return MT5 positions joined with active trade state for the dashboard."""
        positions = self.mt5.get_positions() or []
        enriched = []
        for pos in positions:
            item = dict(pos)
            symbol = item.get("symbol")
            trade = self.active_trades.get(symbol, {}) if symbol else {}
            item["trade_state"] = {
                "status": "ACTIVE" if symbol in self.active_trades else "EXTERNAL",
                "trade_style": trade.get("trade_style"),
                "trade_horizon": trade.get("trade_horizon"),
                "partial_tp_taken": bool(trade.get("partial_tp_taken")),
                "reverse_exit_done": bool(trade.get("reverse_exit_done")),
                "news_ladder_count": len(trade.get("news_ladder_addons") or []),
                "news_move": trade.get("news_move"),
                "false_move": trade.get("false_move"),
                "max_favorable_r": trade.get("max_favorable_r"),
                "max_favorable_profit": trade.get("max_favorable_profit"),
                "opened_at": trade.get("opened_at"),
                "risk": trade.get("risk"),
            }
            try:
                r_now = self._position_r_multiple(item)
                item["r_multiple"] = round(r_now, 3) if r_now is not None else None
            except Exception:
                item["r_multiple"] = None
            enriched.append(item)
        return enriched

    def _manage_pending_orders(self):
        """Manage pending orders (Set and Forget feature)."""
        try:
            can_trade, reason = self._can_place_pending_orders()
            if not can_trade:
                logger.info(f"Skipping pending order placement: {reason}")
                return

            # Scan for high-probability zones and place pending orders
            placed = self.pending_order_manager.scan_and_place_pending_orders(
                self.symbols,
                volume_func=self._calculate_volume,
                rr_ratio=self.take_profit_r_multiplier
            )
            
            if placed:
                logger.info(f"Placed {len(placed)} pending orders")
            
            # Monitor existing pending orders
            updates = self.pending_order_manager.monitor_pending_orders()
            if updates:
                for symbol, status in updates.items():
                    if status["status"] == "FILLED_OR_CANCELLED":
                        logger.info(f"Pending order for {symbol} was filled or cancelled")
        
        except Exception as e:
            logger.error(f"Error managing pending orders: {e}")

    def _manage_conditional_watchlist(self):
        """Manage conditional watchlist (Smart Watchlist feature)."""
        try:
            # Process watchlist through phases
            updates = self.conditional_watchlist_manager.process_watchlist()
            
            if updates:
                for symbol, update in updates.items():
                    logger.info(f"Watchlist update {symbol}: {update}")
            
            # Check for symbols ready for execution (Phase 3 complete)
            ready_symbols = self.conditional_watchlist_manager.get_ready_for_execution()
            
            for ready in ready_symbols:
                symbol = ready["symbol"]
                
                # Skip if already in trade or trading disabled
                if symbol in self.active_trades or self.killed.get(symbol) or self.killed.get("all"):
                    continue
                
                # Can we trade?
                can_trade, reason = self._can_trade()
                if not can_trade:
                    self.log_rejection(symbol, f"Watchlist blocked: {reason}")
                    continue
                
                # Calculate volume for this trade
                extreme_fvg = ready["extreme_fvg"]
                volume = self._calculate_volume(symbol, extreme_fvg["entry"], extreme_fvg["sl"])
                if volume <= 0:
                    self.log_rejection(symbol, f"Watchlist blocked: invalid fixed lot size for {symbol}")
                    continue
                
                # Place the conditional order
                order_result = self.conditional_watchlist_manager.place_conditional_order(symbol, volume)
                
                if order_result:
                    logger.info(f"Conditional order placed for {symbol}: {order_result}")
                    # Reset the watchlist symbol for next cycle
                    self.conditional_watchlist_manager.reset_symbol(symbol)
        
        except Exception as e:
            logger.error(f"Error managing conditional watchlist: {e}")

    def start(self):
        """Start the trading loop"""
        self._refresh_license_status()
        self.is_running = True
        logger.info("Trading engine started")
        alert_manager.create(
            "Bot started",
            "Trading engine loop started.",
            severity="success",
            category="system",
            event="engine_started",
            dedupe_key="engine_started",
            cooldown_seconds=5,
        )
        
        # Initialize conditional watchlist if enabled
        if self.features.get("conditional_watchlist"):
            self.conditional_watchlist_manager.initialize_watchlist(self.symbols)
        
        while self.is_running:
            try:
                self.scan_and_trade()
                
                # Handle pending orders (Set and Forget)
                if self.features.get("pending_orders"):
                    self._manage_pending_orders()
                
                # Handle conditional watchlist (Smart Watchlist)
                if self.features.get("conditional_watchlist"):
                    self._manage_conditional_watchlist()
                
                self.check_positions()
                time.sleep(self.engine_loop_sleep_seconds)
            except Exception as e:
                logger.error(f"Engine error: {e}")
                time.sleep(self.engine_loop_sleep_seconds)

    def stop(self):
        """Stop the trading loop"""
        self.is_running = False
        logger.info("Trading engine stopped")
        alert_manager.create(
            "Bot stopped",
            "Trading engine loop stopped.",
            severity="info",
            category="system",
            event="engine_stopped",
            dedupe_key="engine_stopped",
            cooldown_seconds=5,
        )

    def _is_market_open(self):
        """Check if forex markets are currently open"""
        from datetime import datetime
        now = datetime.utcnow()
        weekday = now.weekday()  # 0=Monday, 6=Sunday
        
        # Forex markets: Sunday 17:00 UTC to Friday 17:00 UTC
        if weekday == 6:  # Sunday
            return now.hour >= 17
        elif weekday >= 0 and weekday <= 4:  # Monday-Friday
            return True
        elif weekday == 5:  # Saturday
            return False
        else:
            return False

    def _current_scan_candle_key(self):
        """Return a stable key for the active scan candle window."""
        minutes = max(1, self.scan_timeframe_minutes)
        now = datetime.utcnow().replace(second=0, microsecond=0)
        floored_minute = (now.minute // minutes) * minutes
        candle = now.replace(minute=floored_minute)
        return candle.isoformat()

    def _seconds_until_next_scan(self):
        if self.scan_on_new_candle:
            minutes = max(1, self.scan_timeframe_minutes)
            now = datetime.utcnow()
            floored_minute = (now.minute // minutes) * minutes
            candle = now.replace(minute=floored_minute, second=0, microsecond=0)
            next_candle = candle + timedelta(minutes=minutes)
            return max(1, int((next_candle - now).total_seconds()))

        if self.last_scan_at is None:
            return 1
        elapsed = (datetime.utcnow() - self.last_scan_at).total_seconds()
        return max(1, int(self.scan_interval_seconds - elapsed))

    def _should_scan_now(self):
        if self.scan_on_new_candle:
            candle_key = self._current_scan_candle_key()
            if candle_key == self.last_scan_candle_key:
                self.next_scan_at = datetime.utcnow() + timedelta(seconds=self._seconds_until_next_scan())
                return False
            self.last_scan_candle_key = candle_key
            self.last_scan_at = datetime.utcnow()
            self.next_scan_at = self.last_scan_at + timedelta(seconds=self._seconds_until_next_scan())
            return True

        now = datetime.utcnow()
        if self.last_scan_at is None or (now - self.last_scan_at).total_seconds() >= self.scan_interval_seconds:
            self.last_scan_at = now
            self.next_scan_at = now + timedelta(seconds=max(1, self.scan_interval_seconds))
            return True
        self.next_scan_at = now + timedelta(seconds=self._seconds_until_next_scan())
        return False

    def _signal_key(self, signal):
        symbol = signal.get("symbol")
        action = signal.get("action")
        entry = signal.get("entry")
        sl = signal.get("sl")
        tp = signal.get("tp")
        nature = signal.get("nature")
        return (
            symbol,
            action,
            round(float(entry or 0), 5),
            round(float(sl or 0), 5),
            round(float(tp or 0), 5),
            nature,
        )

    def _should_log_signal(self, signal):
        """Suppress repeated log spam for the same signal inside the cooldown window."""
        key = self._signal_key(signal)
        now = datetime.utcnow()
        last_seen = self._signal_log_cache.get(key)
        self._signal_log_cache = {
            k: v for k, v in self._signal_log_cache.items()
            if (now - v).total_seconds() <= self.duplicate_signal_cooldown_seconds
        }
        if last_seen and (now - last_seen).total_seconds() < self.duplicate_signal_cooldown_seconds:
            return False
        self._signal_log_cache[key] = now
        return True

    def _write_strategy_journal(
        self,
        signal: dict,
        decision: str,
        rejection_reason: str | None = None,
        analytic_result: dict | None = None,
        predictive_result: dict | None = None,
        ensemble_decision: dict | None = None,
    ):
        """Persist a read-only journal entry for a scanned setup."""
        try:
            setup = signal.get("setup_score") or {}
            components = setup.get("components") or []
            confirmed = [c.get("label") for c in components if c.get("passed")]
            missing = [c.get("label") for c in components if not c.get("passed")]
            horizon = signal.get("trade_horizon") or {}
            spread = signal.get("spread_safety") or setup.get("spread") or {}
            session = signal.get("session_bias") or setup.get("session_bias") or {}
            market_quality = signal.get("market_quality") or setup.get("market_quality") or {}
            market_regime = signal.get("market_regime") or setup.get("market_regime") or {}

            strategy_journal.write({
                "timestamp": signal.get("timestamp") or datetime.now().isoformat(),
                "symbol": signal.get("symbol"),
                "direction": signal.get("action") or setup.get("action"),
                "trade_type": str(horizon.get("type") or signal.get("trade_style") or "intraday").lower(),
                "archetype": setup.get("archetype") or signal.get("setup_name") or signal.get("nature"),
                "score": float(setup.get("score") or signal.get("confluence_score") or 0.0),
                "grade": setup.get("grade"),
                "confirmed_components": [x for x in confirmed if x],
                "missing_blockers": [x for x in missing if x],
                "war_room_analytic_score": (analytic_result or {}).get("overall_score") or (ensemble_decision or {}).get("analytic_score"),
                "predictive_score": (predictive_result or {}).get("probability") or (ensemble_decision or {}).get("predictive_probability"),
                "final_conviction": (ensemble_decision or {}).get("conviction"),
                "execution_decision": str(decision or "WAIT").upper(),
                "rejection_reason": rejection_reason,
                "adaptive_weighting": signal.get("adaptive_weighting"),
                "market_quality": market_quality,
                "market_regime": market_regime,
                "regime_policy": signal.get("regime_policy") or regime_policy(market_regime),
                "strategy_context": self.strategy_context,
                "spread_state": {
                    "safe": spread.get("safe"),
                    "spread_pips": spread.get("spread_pips"),
                    "description": spread.get("description"),
                },
                "session_quality": {
                    "score": session.get("score"),
                    "label": session.get("label") or session.get("quality"),
                    "description": session.get("description") or session.get("reason"),
                },
            })
        except Exception as e:
            logger.warning(f"Strategy journal write failed: {e}")

    def scan_and_trade(self):
        """Scan for FVG signals and execute trades"""
        try:
            if not self._should_scan_now():
                return

            # Check if markets are open
            if not self._is_market_open():
                self.add_logic("SYSTEM", "Markets closed; scanning for signal snapshots only", level="info")
                # Still scan for signals to show in UI, but don't trade
                signals, scan_diagnostics = scan_symbols(self.symbols, self.timeframe, return_diagnostics=True, broker=self.broker)
                self._record_scan_diagnostics(scan_diagnostics)
                self.last_scan_signal_count = len(signals)
                for signal in signals:
                    signal = {**signal, "timestamp": datetime.now().isoformat()}
                    self.recent_signals.append(signal)
                    self._write_strategy_journal(signal, "WAIT", "Markets closed")
                self.recent_signals = self.recent_signals[-20:]
                return

            signals, scan_diagnostics = scan_symbols(self.symbols, self.timeframe, return_diagnostics=True, broker=self.broker)
            self._record_scan_diagnostics(scan_diagnostics)
            self.last_scan_signal_count = len(signals)
            logger.info(f"Scanner cycle complete: loaded={len(self.symbols)} scanned={len(scan_diagnostics)} signals={len(signals)}")

            # store recent signals (keep last 20)
            for signal in signals:
                self.recent_signals.append(signal)
            self.recent_signals = self.recent_signals[-20:]

            for signal in signals:
                symbol = signal.get("symbol")

                # Timestamp and classification events
                signal = {
                    **signal,
                    "timestamp": datetime.now().isoformat(),
                }

                # Compute scalp potential rating and store signal history
                scalp_data = self._compute_scalp_potential(signal)
                signal["scalp_potential"] = scalp_data
                setup_score = signal.get("setup_score") or {}
                setup_value = float(setup_score.get("score", 0.0))
                signal["regime_policy"] = regime_policy(signal.get("market_regime") or setup_score.get("market_regime"))
                self.current_regimes[symbol] = signal.get("market_regime") or {}
                adaptive_result = self.adaptive_weights.evaluate_signal(signal)
                signal["adaptive_weighting"] = adaptive_result
                signal["adaptive_score"] = adaptive_result.get("adjusted_score", setup_value)
                if setup_score:
                    setup_score["adaptive_score"] = signal["adaptive_score"]
                    setup_score["adaptive_multiplier"] = adaptive_result.get("multiplier", 1.0)
                    setup_score["adaptive_explanations"] = adaptive_result.get("explanations", [])
                if adaptive_result.get("suppressed"):
                    reason = adaptive_result.get("suppression_reason") or "Adaptive weighting suppressed this setup"
                    self.log_rejection(symbol, f"Adaptive weighting: {reason}")
                    self.add_logic(symbol, f"Adaptive weighting suppressed setup: {reason}", level="warning")
                    self._write_strategy_journal(signal, "WAIT", f"Adaptive weighting: {reason}")
                    continue
                setup_value = float(signal.get("adaptive_score") or setup_value)
                self.signal_history.append(signal)
                self.signal_history = self.signal_history[-200:]
                
                # Enhanced future trades with institutional context
                conviction_score = int(max(scalp_data['score'], setup_value, float(signal.get("confluence_score", 0.0))) * 100)
                setup_name = (
                    f"Grade {setup_score.get('grade')} Early Entry"
                    if signal.get("early_entry")
                    else "Institutional Sweep" if setup_value >= 0.70
                    else "Order Block Mitigation" if scalp_data['score'] >= 0.5
                    else "FVG Re-entry"
                )
                trigger = f"Wait for {signal.get('nature').split()[0]} confirmation at {signal.get('entry'):.5f}"
                
                trade_style = self._classify_trade_style(signal, scalp_data)
                signal["trade_style"] = trade_style
                trade_horizon = signal.get("trade_horizon") or {}
                false_move = signal.get("false_move") or {}
                news_move = signal.get("news_move") or {}
                event_tags = []
                if false_move.get("type") and false_move.get("type") not in ["UNKNOWN", "RANGE"]:
                    event_tags.append(false_move.get("type").replace("_", " ").title())
                if news_move.get("mode") and news_move.get("mode") != "NORMAL":
                    event_tags.append(f"News {news_move.get('mode').replace('_', ' ').title()}")

                future_trade = {
                    **signal,
                    "setup_name": setup_name,
                    "conviction_score": conviction_score,
                    "trigger": trigger,
                    "trade_style": trade_style,
                    "trade_horizon": trade_horizon,
                    "phase": "Monitoring" if conviction_score < 70 else "Ready",
                    "criteria": f"Conviction {conviction_score}% | {trade_horizon.get('type', 'INTRADAY')} | {scalp_data['label']}" + (f" | {' | '.join(event_tags)}" if event_tags else ""),
                    "action_needed": (
                        "Wait for news retest/spread normalisation"
                        if news_move.get("plan") in ["WAIT_RETEST", "WAIT_SPREAD"]
                        else "Fade failed breakout" if false_move.get("type") in ["FAILED_BREAKOUT", "LIQUIDITY_SWEEP_REVERSAL"]
                        else "Execute on M5 Shift" if conviction_score >= 80
                        else "Hunting FVG Fill"
                    )
                }
                self.future_trades.append(future_trade)
                self.future_trades = self.future_trades[-200:]

                # Log the signal for UI and analytics without spamming repeated identical setups.
                should_log_signal = self._should_log_signal(signal)
                if should_log_signal:
                    self.logger.log_signal(signal)
                context_reason = f"{setup_score.get('archetype', 'Structure')} identified with market context"
                if setup_value >= self.early_entry_min_score:
                    context_reason += f" | Early Score {setup_value:.2f} ({setup_score.get('summary', 'composite setup')})"
                if scalp_data['score'] >= 0.7:
                    context_reason += " | High Scalp Conviction"
                elif scalp_data['score'] >= 0.5:
                    context_reason += " | Momentum Setup detected"
                else:
                    context_reason += " | Trend Opportunity zone"
                if should_log_signal:
                    self.add_logic(symbol, f"Structure setup: {signal.get('nature')} ({scalp_data['label']}|score={scalp_data['score']}) - {context_reason}")
                    if trade_horizon:
                        self.add_logic(symbol, f"Trade horizon: {trade_horizon.get('type')} ({trade_horizon.get('hold_time')}) - {trade_horizon.get('reason')}")

                # Validate rules and compute war room decision
                trade_approved = False
                ensemble_decision = {}
                analytic_result = {}
                predictive_result = {}
                if self.features.get("war_room", True):
                    analytic_result = self.analytic_engine.evaluate_setup(symbol, signal)
                    predictive_result = self.predictive_engine.predict_probability(symbol)
                    ensemble_decision = self.ensemble_decision.make_decision(
                        analytic_result, predictive_result, signal
                    )

                    conviction = ensemble_decision.get("conviction", 0.5)
                    decision = ensemble_decision.get("decision", "WAIT")
                    confluence_score = float(signal.get("confluence_score", 0.0))

                    # Use configurable conviction threshold (lowered to 0.60)
                    if conviction < self.conviction_threshold:
                        decision = "WAIT"

                    if decision == "TRADE":
                        trade_approved = True
                        self.add_logic(symbol, f"War Room WATCHLIST approved; conviction={conviction:.3f}", level="info")
                        logger.info(f"War Room WATCHLIST approved setup for {symbol}: Conviction {conviction:.3f}")
                    else:
                        rejection_reason = ensemble_decision.get("reasoning", "Low conviction")
                        self.log_rejection(symbol, f"War Room: {rejection_reason}")
                        self.add_logic(symbol, f"War Room DECLINED ({rejection_reason}); conviction={conviction:.3f}", level="warning")
                        # Fall back to traditional validation
                    record_blocker(signal, ensemble_decision, self.timeframe)

                # Fallback to traditional validation if war room declined or disabled
                if not trade_approved:
                    valid, reason = validate_trade(symbol, {**self.rule_config, **{"action": signal.get("action")}})
                    if not valid:
                        self.log_rejection(symbol, f"Validation failed: {reason}")
                        self._write_strategy_journal(signal, "REJECTED", f"Validation failed: {reason}", analytic_result, predictive_result, ensemble_decision)
                        continue
                    else:
                        trade_approved = True
                        self.add_logic(symbol, f"Traditional validation PASSED", level="info")

                # If trade is approved, proceed with execution
                if not trade_approved:
                    continue

                rr_ok, rr_reason = self._normalize_signal_levels_to_rr(signal)
                if not rr_ok:
                    self.log_rejection(symbol, rr_reason)
                    self.add_logic(symbol, f"Signal rejected by TP/SL calculator: {rr_reason}", level="warning")
                    self._write_strategy_journal(signal, "REJECTED", rr_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, rr_reason, level="info")

                # Check daily profit cap
                if self._has_hit_daily_profit_cap():
                    self.add_logic(symbol, f"Daily profit cap reached ({self.daily_profit_cap*100:.1f}%), stopping trading for today", level="warning")
                    self.is_running = False  # Stop the bot
                    self._write_strategy_journal(signal, "WAIT", "Daily profit cap reached", analytic_result, predictive_result, ensemble_decision)
                    continue

                # Reject signals with too-small profit potential
                big_enough, size_reason = self._is_signal_big_enough(signal)
                if not big_enough:
                    self.log_rejection(symbol, size_reason)
                    self.add_logic(symbol, f"Signal rejected by size filter: {size_reason}", level="warning")
                    self._write_strategy_journal(signal, "REJECTED", size_reason, analytic_result, predictive_result, ensemble_decision)
                    continue

                execution_ok, execution_reason = self._execution_gate(signal, ensemble_decision, setup_value)
                if not execution_ok:
                    self.log_rejection(symbol, execution_reason)
                    self.add_logic(symbol, f"Signal rejected by execution gate: {execution_reason}", level="warning")
                    self._write_strategy_journal(signal, "REJECTED", execution_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, execution_reason, level="info")

                professional_ok, professional_reason = self._professional_execution_gate(signal, ensemble_decision, setup_value)
                if not professional_ok:
                    self.log_rejection(symbol, professional_reason)
                    self.add_logic(symbol, f"Signal held as watch-only: {professional_reason}", level="warning")
                    self._write_strategy_journal(signal, "WATCH", professional_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, professional_reason, level="info")

                event_ok, event_reason = self._event_execution_gate(signal)
                if not event_ok:
                    self.log_rejection(symbol, event_reason)
                    self.add_logic(symbol, f"Signal held by event/trap gate: {event_reason}", level="warning")
                    self._write_strategy_journal(signal, "WAIT", event_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, event_reason, level="info")

                quality_ok, quality_reason = self._strict_quality_gate(signal, ensemble_decision, setup_value)
                if not quality_ok:
                    self.log_rejection(symbol, quality_reason)
                    self.add_logic(symbol, f"Signal rejected by strict quality gate: {quality_reason}", level="warning")
                    self._write_strategy_journal(signal, "REJECTED", quality_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, quality_reason, level="info")

                ict_ok, ict_reason = self._ict_execution_gate(signal)
                if not ict_ok:
                    self.log_rejection(symbol, ict_reason)
                    self.add_logic(symbol, ict_reason, level="warning")
                    self._write_strategy_journal(signal, "REJECTED", ict_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, ict_reason, level="info")

                validation_ok, validation_reason = self._backtest_validation_gate()
                if not validation_ok:
                    self.log_rejection(symbol, validation_reason)
                    self.add_logic(symbol, validation_reason, level="warning")
                    self._write_strategy_journal(signal, "REJECTED", validation_reason, analytic_result, predictive_result, ensemble_decision)
                    continue
                self.add_logic(symbol, validation_reason, level="info")

                # Determine whether this signal is favorable (pass checks)
                can_trade, can_trade_reason = self._can_trade()
                status = "ready"
                status_reason = None
                if self.killed.get("all") or self.killed.get(symbol):
                    status = "killed"
                    status_reason = "Kill switch active"
                elif symbol in self.active_trades:
                    status = "active"
                    status_reason = "Already in trade"
                elif not can_trade:
                    status = "blocked"
                    status_reason = can_trade_reason

                expected_r = self._calculate_expected_r(signal)
                self.add_logic(symbol, f"Signal evaluation: status={status}, reason={status_reason or 'none'}, expected_r={expected_r if expected_r is not None else 'N/A'}")
                self.favorable_signals.append({
                    **signal,
                    "status": status,
                    "status_reason": status_reason,
                    "expected_r": expected_r,
                })
                self.favorable_signals = self.favorable_signals[-20:]

                # ========== SIGNAL LOCKOUT CHECK ==========
                # Check if this symbol is locked out from new trades
                lockout_check, lockout_reason = self._check_signal_lockout(symbol)
                if not lockout_check:
                    self.log_rejection(symbol, f"Signal Lockout: {lockout_reason}")
                    self.add_logic(symbol, f"Signal rejected by lockout system: {lockout_reason}", level="warning")
                    # Update favorable signals with lockout status
                    self.favorable_signals[-1]["status"] = "locked"
                    self.favorable_signals[-1]["status_reason"] = lockout_reason
                    self._write_strategy_journal(signal, "WAIT", f"Signal Lockout: {lockout_reason}", analytic_result, predictive_result, ensemble_decision)
                    continue

                if status != "ready":
                    if status == "blocked":
                        self.log_rejection(symbol, status_reason)
                    self._write_strategy_journal(signal, "WAIT", status_reason, analytic_result, predictive_result, ensemble_decision)
                    continue

                use_market_execution = self._should_use_market_execution(signal, scalp_data, ensemble_decision)
                signal["execution_type"] = "market" if use_market_execution else "pending"
                if use_market_execution:
                    refreshed, refresh_reason = self._refresh_signal_for_market_execution(signal)
                    if not refreshed:
                        self.log_rejection(symbol, refresh_reason)
                        self.add_logic(symbol, f"Signal rejected before market execution: {refresh_reason}", level="warning")
                        self._write_strategy_journal(signal, "REJECTED", refresh_reason, analytic_result, predictive_result, ensemble_decision)
                        continue
                    self.add_logic(symbol, refresh_reason, level="info")
                    self.add_logic(symbol, "High-probability market execution selected", level="info")
                    rr_ok, rr_reason = self._normalize_signal_levels_to_rr(signal)
                    if not rr_ok:
                        self.log_rejection(symbol, rr_reason)
                        self.add_logic(symbol, f"Signal rejected after market refresh: {rr_reason}", level="warning")
                        self._write_strategy_journal(signal, "REJECTED", rr_reason, analytic_result, predictive_result, ensemble_decision)
                        continue
                    self.add_logic(symbol, rr_reason, level="info")

                # Check if we have enough funds for this trade
                entry = signal.get("entry")
                sl = signal.get("sl")
                volume = self._calculate_volume(symbol, entry, sl)
                if volume <= 0:
                    self.log_rejection(symbol, f"Invalid fixed lot size for {symbol}; check TRADE_VOLUME and broker minimum")
                    self.add_logic(symbol, "Signal rejected by lot-size guard: fixed lot is below broker minimum or invalid", level="warning")
                    self._write_strategy_journal(signal, "REJECTED", "Invalid fixed lot size or broker minimum", analytic_result, predictive_result, ensemble_decision)
                    continue
                risk_multiplier = float(signal.get("risk_multiplier") or 1.0)
                if risk_multiplier < 1.0:
                    reduced_volume = volume * risk_multiplier
                    if reduced_volume < self._get_symbol_min_lot(symbol):
                        self.log_rejection(symbol, f"Reduced event volume below broker minimum for {symbol}")
                        self.add_logic(symbol, "Signal rejected: event risk reduction would round back up to minimum lot", level="warning")
                        self._write_strategy_journal(signal, "REJECTED", "Reduced event volume below broker minimum", analytic_result, predictive_result, ensemble_decision)
                        continue
                    volume = self._round_symbol_lot(symbol, reduced_volume)
                    self.add_logic(symbol, f"Risk reduced for event mode: volume multiplier {risk_multiplier:.0%}", level="info")

                regime_risk_multiplier = float((signal.get("regime_policy") or {}).get("risk_multiplier") or 1.0)
                if regime_risk_multiplier < 1.0:
                    reduced_volume = volume * regime_risk_multiplier
                    if reduced_volume < self._get_symbol_min_lot(symbol):
                        self.log_rejection(symbol, f"Regime risk reduction below broker minimum for {symbol}")
                        self._write_strategy_journal(signal, "REJECTED", "Regime risk reduction below broker minimum", analytic_result, predictive_result, ensemble_decision)
                        continue
                    volume = self._round_symbol_lot(symbol, reduced_volume)
                    self.add_logic(symbol, f"Regime policy reduced volume: {regime_risk_multiplier:.0%} for {(signal.get('market_regime') or {}).get('label', 'unknown')}", level="info")

                # Verify funds with favorable trade priority
                # If funds insufficient, skip and let favorable trades attempt first on next scan
                can_trade_check, trade_reason = self._can_trade()
                if not can_trade_check:
                    self.log_rejection(symbol, f"Insufficient funds - {trade_reason}")
                    self._write_strategy_journal(signal, "REJECTED", f"Insufficient funds - {trade_reason}", analytic_result, predictive_result, ensemble_decision)
                    continue

                self._write_strategy_journal(signal, "READY", None, analytic_result, predictive_result, ensemble_decision)
                self.execute_trade(signal, volume, use_market_execution=use_market_execution)
        except Exception as e:
            logger.error(f"Scan error: {e}")

    def execute_trade(self, signal, volume: float, use_market_execution: bool = False):
        """Execute a trade based on FVG signal."""
        try:
            self._refresh_license_status()
            if self.live_trading_disabled:
                reason = (self.license_status or {}).get("reason") or "License is not valid"
                self.log_rejection(signal.get("symbol", "Unknown"), f"License blocked execution: {reason}")
                return

            symbol = signal["symbol"]
            action = signal["action"]
            entry = signal["entry"]
            sl = signal["sl"]
            tp = signal["tp"]

            if use_market_execution:
                tick = self.mt5.get_symbol_tick(symbol)
                if tick is None:
                    logger.error(f"Failed to retrieve tick data for market execution: {symbol}")
                    return
                price = float(tick.ask if action == "BUY" else tick.bid)
                if action == "BUY":
                    order_id = self.mt5.place_buy_order(symbol, volume, price, sl, tp)
                else:
                    order_id = self.mt5.place_sell_order(symbol, volume, price, sl, tp)
            else:
                if action == "BUY":
                    order_id = self.mt5.place_buy_limit_order(symbol, volume, entry, sl, tp)
                else:
                    order_id = self.mt5.place_sell_limit_order(symbol, volume, entry, sl, tp)

            if order_id:
                trade_metadata = self._trade_metadata_from_signal(signal)
                if hasattr(self.mt5, "attach_order_metadata"):
                    self.mt5.attach_order_metadata(order_id, trade_metadata)
                self._mark_ict_session_trade(signal)
                risk_amount = self._calculate_risk_amount(symbol, entry, sl, volume)
                execution_type = "market" if use_market_execution else "pending"
                trade_style = signal.get("trade_style", "Setup")
                self.add_logic(symbol, f"Trade executed ({execution_type}) {trade_style} {action} @ {entry} SL={sl} TP={tp} vol={volume:.2f} risk=${risk_amount:.2f}", level="info")
                self.active_trades[symbol] = {
                    "order_id": order_id,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "original_sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "risk": risk_amount,
                    "opened_at": datetime.now().isoformat(),
                    "type": execution_type,
                    "trade_style": trade_style,
                    "trade_horizon": signal.get("trade_horizon"),
                    "risk_multiplier": signal.get("risk_multiplier", 1.0),
                    "false_move": signal.get("false_move"),
                    "news_move": signal.get("news_move"),
                    "market_regime": signal.get("market_regime"),
                    "regime_policy": signal.get("regime_policy"),
                    "strategy_context": self.strategy_context,
                    "strategy_type": (signal.get("ict") or {}).get("strategy_type") if self.ict_enabled else "current",
                    "ict": signal.get("ict"),
                    "entry_diagnostics": trade_metadata.get("entry_diagnostics"),
                    "entry_reason": (signal.get("ict") or {}).get("entry_reason") or signal.get("nature"),
                    "initial_volume": volume,
                    "news_ladder_addons": [],
                    "partial_tp_taken": False,
                    "reverse_exit_done": False,
                    "max_favorable_r": 0.0,
                    "max_favorable_profit": 0.0,
                    "max_favorable_price": entry,
                    "max_adverse_r": 0.0,
                    "max_adverse_profit": 0.0,
                }
                self.logger.log_trade({
                    "symbol": symbol,
                    "action": action,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "volume": volume,
                    "risk": risk_amount,
                    "order_id": order_id,
                    "type": execution_type,
                    "trade_style": trade_style,
                    "trade_horizon": signal.get("trade_horizon"),
                    "risk_multiplier": signal.get("risk_multiplier", 1.0),
                    "false_move": signal.get("false_move"),
                    "news_move": signal.get("news_move"),
                    "market_regime": signal.get("market_regime"),
                    "regime_policy": signal.get("regime_policy"),
                    "strategy_context": self.strategy_context,
                    "strategy_type": (signal.get("ict") or {}).get("strategy_type") if self.ict_enabled else "current",
                    "ict": signal.get("ict"),
                    "entry_diagnostics": trade_metadata.get("entry_diagnostics"),
                    "entry_reason": (signal.get("ict") or {}).get("entry_reason") or signal.get("nature"),
                })
                alert_manager.create(
                    "New trade opened",
                    f"{symbol} {action} {execution_type} opened @ {entry} vol={volume:.2f}",
                    severity="success",
                    category="trade",
                    symbol=symbol,
                    event="trade_opened",
                    metadata={
                        "action": action,
                        "entry": entry,
                        "sl": sl,
                        "tp": tp,
                        "volume": volume,
                        "risk": risk_amount,
                        "order_id": order_id,
                        "execution_type": execution_type,
                    },
                )
                logger.info(f"{execution_type.capitalize()} order placed: {symbol} {action} (vol={volume:.2f}, risk=${risk_amount:.2f})")
                self._register_trade_open(symbol)
            else:
                order_error = getattr(self.mt5, "last_order_error", None) or "MT5 returned no order id"
                self.add_logic(symbol, f"Trade execution failed: {order_error}", level="warning")
                logger.error(f"Failed to place {action} order: {symbol} - {order_error}")
        except Exception as e:
            logger.error(f"Trade execution error: {e}")

    def _trade_metadata_from_signal(self, signal: dict) -> dict:
        ict = signal.get("ict") or {}
        components = ict.get("components") or {}
        setup = signal.get("setup_score") or {}
        spread = signal.get("spread_safety") or setup.get("spread") or {}
        market_quality = signal.get("market_quality") or setup.get("market_quality") or {}
        premium_discount = ict.get("premium_discount") or {}
        entry_diagnostics = {
            "inside_premium_zone": bool(premium_discount.get("inside_premium")),
            "inside_discount_zone": bool(premium_discount.get("inside_discount")),
            "premium_discount_zone": premium_discount.get("zone"),
            "liquidity_sweep": bool(components.get("liquidity_sweep_detected")),
            "bos_confirmed": bool(components.get("bos_detected")),
            "choch_confirmed": bool(components.get("choch_detected")),
            "fvg_present": bool(components.get("fvg_present")),
            "fvg_retested": bool(components.get("fvg_retest_detected")),
            "htf_bias_aligned": bool(components.get("htf_bias_agrees")),
            "session_name": ict.get("session_name"),
            "spread": spread,
            "spread_pips": spread.get("spread_pips"),
            "volatility_state": market_quality.get("description") or market_quality.get("label"),
            "volatility_score": market_quality.get("volatility_quality"),
            "market_quality_score": market_quality.get("score"),
        }
        return {
            "strategy_type": ict.get("strategy_type") if self.ict_enabled else "current",
            "ict": ict,
            "ict_components": components,
            "entry_diagnostics": entry_diagnostics,
            **entry_diagnostics,
            "liquidity_sweep_detected": bool(components.get("liquidity_sweep_detected")),
            "bos_detected": bool(components.get("bos_detected")),
            "choch_detected": bool(components.get("choch_detected")),
            "fvg_present": bool(components.get("fvg_present")),
            "fvg_retest_detected": bool(components.get("fvg_retest_detected")),
            "order_block_valid": bool(components.get("order_block_valid")),
            "session_name": ict.get("session_name"),
            "entry_reason": ict.get("entry_reason") or signal.get("nature"),
        }

    def check_positions(self):
        """Monitor active positions and pending orders, apply trade management rules"""
        try:
            positions = self.mt5.get_positions() or []
            pending_orders = self.mt5.get_pending_orders() or []
            
            current_symbols = {p["symbol"] for p in positions}
            pending_symbols = {o["symbol"] for o in pending_orders}
            position_tickets = {p.get("ticket") for p in positions}
            pending_tickets = {o.get("ticket") for o in pending_orders}

            # Check for filled pending orders (now positions)
            for symbol in list(self.active_trades.keys()):
                trade = self.active_trades[symbol]
                if trade.get("type") == "pending":
                    ticket = trade.get("order_id")
                    if ticket in position_tickets:
                        # Pending order was filled - update to position
                        self.add_logic(symbol, f"Pending order filled - now active position", level="info")
                        trade["type"] = "position"
                        trade["filled_at"] = datetime.now().isoformat()
                        logger.info(f"Pending order filled for {symbol}, now monitoring as position")
                    elif ticket not in pending_tickets:
                        # Pending order was cancelled or expired
                        pending_info = self.pending_order_manager.pending_orders.get(symbol)
                        reason = "Cancelled or expired"
                        if pending_info and pending_info.get("placed_at"):
                            try:
                                placed_time = datetime.fromisoformat(pending_info["placed_at"])
                                age_hours = (datetime.now() - placed_time).total_seconds() / 3600
                                if age_hours >= 24:
                                    reason = "Expired"
                                else:
                                    reason = "Cancelled"
                            except Exception:
                                reason = "Cancelled or expired"

                        self.add_logic(symbol, f"Pending order {reason} - ticket {ticket} no longer exists in MT5", level="warning")
                        self.active_trades.pop(symbol, None)
                        self.pending_order_manager.pending_orders.pop(symbol, None)
                        self._record_closed_trade(symbol, 0, trade.get("risk"), reason, {"market_regime": trade.get("market_regime")})
                        continue
            
            # Update profit/loss for active positions
            for position in positions:
                symbol = position.get("symbol")
                if symbol in self.active_trades:
                    profit = position.get("profit", 0)
                    self.last_known_profit[symbol] = profit
            
            # Detect closed positions
            for symbol in list(self.active_trades.keys()):
                if symbol not in current_symbols and symbol not in pending_symbols:
                    # Trade was closed or cancelled
                    trade = self.active_trades.pop(symbol, None)
                    if trade:
                        self._register_trade_close(symbol)
                        # Try to get profit from last known position
                        profit = self.last_known_profit.get(symbol, 0)
                        risk = trade.get("risk") if isinstance(trade, dict) else None
                        reason = "TP/SL Hit" if profit != 0 else "Cancelled"
                        close_metadata = {
                            "market_regime": trade.get("market_regime"),
                            "strategy_type": trade.get("strategy_type"),
                            "ict": trade.get("ict"),
                            "entry_diagnostics": trade.get("entry_diagnostics"),
                            "entry_reason": trade.get("entry_reason"),
                            "exit_reason": reason,
                            "entry_price": trade.get("entry"),
                            "stop_loss": trade.get("sl"),
                            "take_profit": trade.get("tp"),
                            "highest_profit_reached": trade.get("max_favorable_profit"),
                            "lowest_drawdown": trade.get("max_adverse_profit"),
                            "mfe_r": trade.get("max_favorable_r"),
                            "mae_r": trade.get("max_adverse_r"),
                        }
                        self._record_closed_trade(symbol, profit, risk, reason, close_metadata)
                        self.last_known_profit.pop(symbol, None)
                        self.add_logic(symbol, f"Position closed: exit_reason={reason}, P&L=${profit:.2f}", level="info")

            # Update current profit tracking for open positions
            for pos in positions:
                symbol = pos.get("symbol")
                if not symbol:
                    continue
                self.last_known_profit[symbol] = pos.get("profit")
                if symbol in self.active_trades:
                    logger.info(f"{symbol}: P&L = {pos.get('profit')}")
                    self._track_favorable_excursion(pos)
                    self._apply_partial_take_profit(pos)
                    self._apply_news_ladder(pos)
                    self._apply_reverse_profit_exit(pos)
                    self._apply_trailing_stop(pos)
                    self._apply_trailing_take_profit(pos)
        except Exception as e:
            logger.error(f"Position check error: {e}")

    def _position_r_multiple(self, pos) -> float | None:
        """Return current open profit in R based on price movement versus initial stop."""
        try:
            side = pos.get("type")
            symbol = pos.get("symbol")
            trade = self.active_trades.get(symbol, {}) if symbol else {}
            entry = float(pos.get("entry"))
            current = float(pos.get("current"))
            sl = float(trade.get("original_sl") or pos.get("sl"))
            risk_distance = abs(entry - sl)
            if risk_distance <= 0:
                return None
            move = current - entry if side == "BUY" else entry - current
            return move / risk_distance
        except Exception:
            return None

    def _track_favorable_excursion(self, pos):
        """Track max open profit so reversals from green can be handled before breakeven."""
        symbol = pos.get("symbol")
        if symbol not in self.active_trades:
            return
        trade = self.active_trades[symbol]
        r_now = self._position_r_multiple(pos)
        profit_now = float(pos.get("profit") or 0)
        if r_now is None:
            return

        if r_now > float(trade.get("max_favorable_r") or 0):
            trade["max_favorable_r"] = r_now
            trade["max_favorable_price"] = pos.get("current")
        if r_now < float(trade.get("max_adverse_r") or 0):
            trade["max_adverse_r"] = r_now
            trade["max_adverse_price"] = pos.get("current")
        if profit_now > float(trade.get("max_favorable_profit") or 0):
            trade["max_favorable_profit"] = profit_now
        if profit_now < float(trade.get("max_adverse_profit") or 0):
            trade["max_adverse_profit"] = profit_now

    def _close_position_fraction(self, pos, fraction: float, reason: str) -> bool:
        ticket = pos.get("ticket")
        symbol = pos.get("symbol")
        volume = float(pos.get("volume") or 0)
        if not ticket or not symbol or volume <= 0:
            return False

        fraction = max(0.0, min(1.0, float(fraction or 0)))
        close_volume = None if fraction >= 0.999 else volume * fraction
        success = self.mt5.close_position_volume(ticket, close_volume, comment=reason)
        if success:
            self.add_logic(symbol, f"{reason.replace('_', ' ').title()}: closed {'all' if close_volume is None else f'{close_volume:.2f} lots'}", level="info")
            logger.info(f"{symbol}: {reason} closed volume={close_volume or volume}")
        return success

    def _apply_partial_take_profit(self, pos):
        """Bank part of the position once the trade reaches the configured R target."""
        if not self.partial_tp_enabled:
            return
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not trade or trade.get("partial_tp_taken"):
            return

        r_now = self._position_r_multiple(pos)
        if r_now is None or r_now < self.partial_tp_trigger_r:
            return

        if self._close_position_fraction(pos, self.partial_tp_close_pct, "PARTIAL_TP"):
            trade["partial_tp_taken"] = True
            trade["partial_tp_at"] = datetime.now().isoformat()
            trade["partial_tp_r"] = r_now
            trade["partial_tp_profit"] = pos.get("profit")
            entry = pos.get("entry")
            symbol = pos.get("symbol")
            if self.partial_tp_move_sl_to_be and entry is not None and symbol:
                self.mt5.modify_position_sl(pos.get("ticket"), symbol, entry)
                trade["sl"] = entry
                self.add_logic(symbol, f"Partial TP moved SL to break-even at {entry:.5f}", level="info")
            else:
                self.add_logic(symbol, f"Partial TP kept original SL; break-even move disabled", level="info")
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "PARTIAL_TP",
                "symbol": symbol,
                "r": r_now,
                "profit": pos.get("profit"),
                "close_pct": self.partial_tp_close_pct,
                "remaining_volume": pos.get("volume"),
            })
            alert_manager.create(
                "Partial TP executed",
                f"{symbol}: partial take-profit at {r_now:.2f}R, close {self.partial_tp_close_pct:.0%}.",
                severity="success",
                category="trade",
                symbol=symbol,
                event="partial_tp_executed",
                metadata={"r": r_now, "profit": pos.get("profit"), "close_pct": self.partial_tp_close_pct},
            )

    def _apply_news_ladder(self, pos):
        """Add controlled follow-up positions only after a news move confirms in profit."""
        if not (self.news_mode_enabled and self.news_ladder_enabled):
            return

        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not symbol or not trade:
            return
        if self.killed.get("all") or self.killed.get(symbol):
            return

        addons = trade.setdefault("news_ladder_addons", [])
        if len(addons) >= self.news_ladder_max_addons:
            return

        r_now = self._position_r_multiple(pos)
        if r_now is None:
            return
        if r_now < self.news_ladder_min_r and not trade.get("partial_tp_taken"):
            return

        last_addon_at = trade.get("last_news_ladder_at")
        if last_addon_at:
            try:
                elapsed = (datetime.now() - datetime.fromisoformat(last_addon_at)).total_seconds()
                if elapsed < self.news_ladder_cooldown_seconds:
                    return
            except Exception:
                pass

        try:
            from technical_analysis import detect_news_move, detect_spread_safety
            news_move = detect_news_move(symbol, self.timeframe)
            spread = detect_spread_safety(symbol)
        except Exception as e:
            self.add_logic(symbol, f"News ladder check failed: {e}", level="warning")
            return

        trade_news = trade.get("news_move") or {}
        mode = news_move.get("mode") or trade_news.get("mode") or "NORMAL"
        if mode not in ["FOLLOW_RETEST", "NORMAL"]:
            return
        if spread.get("safe") is False or news_move.get("safe") is False:
            self.add_logic(symbol, f"News ladder waiting: {news_move.get('description', 'spread/event state not safe')}", level="warning")
            return

        side = str(pos.get("type") or trade.get("action") or "").upper()
        if side not in ["BUY", "SELL"]:
            return

        current_volume = float(pos.get("volume") or trade.get("initial_volume") or 0)
        base_volume = float(trade.get("initial_volume") or current_volume or self.volume)
        raw_addon_volume = min(base_volume, current_volume) * self.news_ladder_volume_pct
        if raw_addon_volume < self._get_symbol_min_lot(symbol):
            self.add_logic(symbol, "News ladder skipped: add-on volume would round up to broker minimum", level="warning")
            return
        addon_volume = self._round_symbol_lot(symbol, raw_addon_volume)
        if addon_volume <= 0:
            return

        can_trade, reason = self._can_trade()
        if not can_trade:
            self.add_logic(symbol, f"News ladder blocked: {reason}", level="warning")
            return

        tick = self.mt5.get_symbol_tick(symbol)
        if tick is None:
            self.add_logic(symbol, "News ladder blocked: no tick data", level="warning")
            return

        entry = float(tick.ask if side == "BUY" else tick.bid)
        sl = trade.get("sl") or pos.get("sl")
        tp = trade.get("tp") or pos.get("tp")
        if sl is None or tp is None:
            self.add_logic(symbol, "News ladder blocked: missing SL/TP", level="warning")
            return

        if side == "BUY":
            order_id = self.mt5.place_buy_order(symbol, addon_volume, entry, sl, tp)
        else:
            order_id = self.mt5.place_sell_order(symbol, addon_volume, entry, sl, tp)

        if not order_id:
            order_error = getattr(self.mt5, "last_order_error", None) or "MT5 returned no order id"
            self.add_logic(symbol, f"News ladder add-on failed: {order_error}", level="warning")
            return

        risk_amount = self._calculate_risk_amount(symbol, entry, sl, addon_volume)
        addon = {
            "timestamp": datetime.now().isoformat(),
            "order_id": order_id,
            "action": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "volume": addon_volume,
            "risk": risk_amount,
            "r_trigger": r_now,
            "news_mode": mode,
        }
        addons.append(addon)
        trade["last_news_ladder_at"] = addon["timestamp"]
        trade["risk"] = float(trade.get("risk") or 0) + risk_amount
        trade["volume"] = float(trade.get("volume") or 0) + addon_volume
        self.add_logic(symbol, f"News ladder add-on placed #{len(addons)} at {r_now:.2f}R vol={addon_volume:.2f}", level="info")
        self.logger._save_log({
            "timestamp": addon["timestamp"],
            "event": "NEWS_LADDER_ADDON",
            "symbol": symbol,
            "order_id": order_id,
            "action": side,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "volume": addon_volume,
            "risk": risk_amount,
            "r_trigger": r_now,
            "news_mode": mode,
        })

    def _apply_reverse_profit_exit(self, pos):
        """Close green trades when price reverses sharply from max favorable excursion."""
        if not self.reverse_profit_exit_enabled:
            return
        symbol = pos.get("symbol")
        trade = self.active_trades.get(symbol)
        if not trade or trade.get("reverse_exit_done"):
            return

        r_now = self._position_r_multiple(pos)
        if r_now is None:
            return
        max_r = float(trade.get("max_favorable_r") or 0)
        if max_r < self.reverse_profit_min_r:
            return

        giveback = max_r - r_now
        giveback_trigger = max_r * max(0.0, min(1.0, self.reverse_profit_giveback_pct))
        partial_taken = bool(trade.get("partial_tp_taken"))
        near_breakeven_after_partial = partial_taken and r_now <= self.reverse_after_partial_lock_r and r_now > 0
        reversed_from_peak = r_now > 0 and giveback >= giveback_trigger

        if not (reversed_from_peak or near_breakeven_after_partial):
            return

        close_pct = self.reverse_profit_close_pct if partial_taken else min(self.reverse_profit_close_pct, 0.5)
        if self._close_position_fraction(pos, close_pct, "REVERSE_PROFIT_EXIT"):
            trade["reverse_exit_done"] = True
            trade["reverse_exit_at"] = datetime.now().isoformat()
            trade["reverse_exit_r"] = r_now
            self.logger._save_log({
                "timestamp": datetime.now().isoformat(),
                "event": "REVERSE_PROFIT_EXIT",
                "symbol": symbol,
                "r": r_now,
                "max_r": max_r,
                "giveback_r": giveback,
                "close_pct": close_pct,
                "profit": pos.get("profit"),
            })

    def _apply_trailing_stop(self, pos):
        """Lock profit after trigger, then step-trail as price keeps moving favorably."""
        try:
            ticket = pos.get("ticket")
            symbol = pos.get("symbol")
            side = pos.get("type")
            entry = pos.get("entry")
            current = pos.get("current")
            sl = pos.get("sl")
            tp = pos.get("tp")

            if not ticket or not symbol or entry is None or current is None or sl is None or tp is None:
                return

            pip_size = self._get_pip_size(symbol)
            if not pip_size:
                return

            tp_distance = abs(tp - entry)
            if tp_distance <= 0:
                return

            trade_state = self.active_trades.get(symbol, {})
            policy = trade_state.get("regime_policy") or regime_policy(trade_state.get("market_regime"))
            trigger_pct = max(0.05, min(0.95, self.trailing_stop_trigger_pct + float(policy.get("trailing_stop_trigger_delta") or 0.0)))
            break_even_threshold = entry + (tp_distance * trigger_pct) if side == "BUY" else entry - (tp_distance * trigger_pct)
            lock_distance = max(0.0, self.trailing_stop_lock_pips) * pip_size
            min_step = max(0.0, self.trailing_stop_min_step_pips) * pip_size
            trail_gap = tp_distance * max(0.0, self.trailing_stop_step_pct)

            if side == "BUY":
                if current < break_even_threshold:
                    return
                profit_lock_sl = entry + lock_distance
                step_trail_sl = current - trail_gap
                new_sl = max(profit_lock_sl, step_trail_sl)
                new_sl = min(new_sl, current - pip_size)
                if new_sl > sl + min_step:
                    if self.mt5.modify_position_sl(ticket, symbol, new_sl):
                        if symbol in self.active_trades:
                            self.active_trades[symbol]["sl"] = new_sl
                        logger.info(f"Trailing stop: Locked {symbol} BUY profit at {new_sl:.5f}")
                        alert_manager.create(
                            "Trailing SL updated",
                            f"{symbol} BUY trailing SL moved to {new_sl:.5f}.",
                            severity="info",
                            category="trade",
                            symbol=symbol,
                            event="trailing_sl_updated",
                            metadata={"side": side, "new_sl": new_sl, "ticket": ticket},
                            dedupe_key=f"trailing_sl:{ticket}:{round(new_sl, 5)}",
                            cooldown_seconds=10,
                        )
            elif side == "SELL":
                if current > break_even_threshold:
                    return
                profit_lock_sl = entry - lock_distance
                step_trail_sl = current + trail_gap
                new_sl = min(profit_lock_sl, step_trail_sl)
                new_sl = max(new_sl, current + pip_size)
                if new_sl < sl - min_step:
                    if self.mt5.modify_position_sl(ticket, symbol, new_sl):
                        if symbol in self.active_trades:
                            self.active_trades[symbol]["sl"] = new_sl
                        logger.info(f"Trailing stop: Locked {symbol} SELL profit at {new_sl:.5f}")
                        alert_manager.create(
                            "Trailing SL updated",
                            f"{symbol} SELL trailing SL moved to {new_sl:.5f}.",
                            severity="info",
                            category="trade",
                            symbol=symbol,
                            event="trailing_sl_updated",
                            metadata={"side": side, "new_sl": new_sl, "ticket": ticket},
                            dedupe_key=f"trailing_sl:{ticket}:{round(new_sl, 5)}",
                            cooldown_seconds=10,
                        )
        except Exception as e:
            logger.error(f"Trailing stop error: {e}")

    def _apply_trailing_take_profit(self, pos):
        """Extend take-profit when price reaches a configured percentage of the current TP distance."""
        if not self.trailing_tp_enabled:
            return
        try:
            ticket = pos.get("ticket")
            symbol = pos.get("symbol")
            side = pos.get("type")
            entry = pos.get("entry")
            current = pos.get("current")
            tp = pos.get("tp")

            if not ticket or not symbol or entry is None or current is None or tp is None:
                return

            trade_state = self.active_trades.get(symbol, {})
            last_extended_at = trade_state.get("last_tp_extended_at")
            if last_extended_at:
                try:
                    elapsed = (datetime.now() - datetime.fromisoformat(last_extended_at)).total_seconds()
                    if elapsed < self.trailing_tp_cooldown_seconds:
                        return
                except Exception:
                    pass

            tp_distance = abs(tp - entry)
            if tp_distance <= 0:
                return
            extension_multiplier = float((trade_state.get("regime_policy") or {}).get("trailing_tp_extension_multiplier") or 1.0)
            extension_pct = max(0.05, self.trailing_tp_extension_pct * extension_multiplier)

            if side == "BUY":
                trigger = entry + (tp_distance * self.trailing_tp_trigger_pct)
                if current < trigger:
                    return
                new_tp = tp + (tp_distance * extension_pct)
            elif side == "SELL":
                trigger = entry - (tp_distance * self.trailing_tp_trigger_pct)
                if current > trigger:
                    return
                new_tp = tp - (tp_distance * extension_pct)
            else:
                return

            if self.mt5.modify_position_tp(ticket, symbol, new_tp):
                if symbol in self.active_trades:
                    self.active_trades[symbol]["tp"] = new_tp
                    self.active_trades[symbol]["last_trailed_tp"] = new_tp
                    self.active_trades[symbol]["last_tp_extended_at"] = datetime.now().isoformat()
                self.add_logic(symbol, f"Trailing TP extended to {new_tp:.5f}", level="info")
                alert_manager.create(
                    "Trailing TP extended",
                    f"{symbol} trailing TP extended to {new_tp:.5f}.",
                    severity="success",
                    category="trade",
                    symbol=symbol,
                    event="trailing_tp_extended",
                    metadata={"side": side, "new_tp": new_tp, "ticket": ticket},
                    dedupe_key=f"trailing_tp:{ticket}:{round(new_tp, 5)}",
                    cooldown_seconds=10,
                )
        except Exception as e:
            logger.error(f"Trailing take-profit error: {e}")

    def get_status(self):
        """Get bot status"""
        try:
            license_status = self._refresh_license_status()
            account = self.mt5.get_account_info()
            positions = self.get_enriched_positions() or []
            market_open = self._is_market_open()
            bot_score = self._compute_bot_score(market_open=market_open)
            current_open_risk, open_risk_details = self._calculate_exposure_details()
            broker_spread = self._get_active_broker_spread()

            equity = account.get("equity") if account else None
            max_open_risk = equity * self.max_exposure_pct if equity is not None else None
            open_risk_pct = (current_open_risk / equity) if equity else None
            if equity is not None:
                if self.start_equity is None:
                    self.start_equity = equity
                if self.peak_equity is None or equity > self.peak_equity:
                    self.peak_equity = equity

            daily_profit = None
            floating_drawdown = None
            floating_profit = sum(float(pos.get("profit") or 0) for pos in positions)
            realized_profit = self._get_realized_profit_today()
            net_profit = realized_profit + floating_profit
            if equity is not None and self.start_equity is not None:
                daily_profit = equity - self.start_equity
            if equity is not None and self.peak_equity is not None:
                floating_drawdown = max(0.0, self.peak_equity - equity)
                if self.peak_equity > 0 and (floating_drawdown / self.peak_equity) >= self.max_drawdown_pct:
                    alert_manager.create(
                        "Drawdown limit reached",
                        f"Session drawdown is {(floating_drawdown / self.peak_equity) * 100:.2f}% against limit {self.max_drawdown_pct * 100:.2f}%.",
                        severity="danger",
                        category="risk",
                        event="drawdown_limit_reached",
                        metadata={
                            "drawdown": floating_drawdown,
                            "peak_equity": self.peak_equity,
                            "max_drawdown_pct": self.max_drawdown_pct,
                        },
                        dedupe_key="drawdown_limit_reached",
                        cooldown_seconds=300,
                    )

            return {
                "running": self.is_running,
                "connected": self.broker.is_connected,
                "broker": {
                    "id": self.broker_profile.get("id"),
                    "name": self.broker_profile.get("name") or "Default MT5",
                    "type": self.broker_profile.get("broker_type") or getattr(self.broker, "broker_type", "mt5"),
                    "account": self.broker_profile.get("account"),
                    "server": self.broker_profile.get("server"),
                    "connected": self.broker.is_connected,
                    "spread_symbol": broker_spread.get("symbol"),
                    "spread_pips": broker_spread.get("spread_pips"),
                },
                "market_open": market_open,
                "bot_score": bot_score,
                "symbols": self.symbols,
                "volume": self.volume,
                "position_sizing_mode": self.position_sizing_mode,
                "account": account,
                "balance": account.get("balance") if account else None,
                "equity": equity,
                "free_margin": account.get("free_margin") if account else None,
                "margin_level": account.get("margin_level") if account else None,
                "daily_profit": daily_profit,
                "floating_profit": floating_profit,
                "realized_profit": realized_profit,
                "net_profit": net_profit,
                "floating_drawdown": floating_drawdown,
                "current_open_risk": current_open_risk,
                "max_open_risk": max_open_risk,
                "open_risk_pct": open_risk_pct,
                "max_open_risk_pct": self.max_exposure_pct,
                "open_risk_details": open_risk_details,
                "positions": positions,
                "active_trades": len(self.active_trades),
                "scan": {
                    "interval_seconds": self.scan_interval_seconds,
                    "engine_loop_sleep_seconds": self.engine_loop_sleep_seconds,
                    "on_new_candle": self.scan_on_new_candle,
                    "timeframe_minutes": self.scan_timeframe_minutes,
                    "auto_append_market_watch_symbols": self.auto_append_market_watch_symbols,
                    "last_scan_at": self.last_scan_at.isoformat() if self.last_scan_at else None,
                    "next_scan_at": self.next_scan_at.isoformat() if self.next_scan_at else None,
                    "seconds_until_next_scan": self._seconds_until_next_scan(),
                    "last_signal_count": self.last_scan_signal_count,
                    "duplicate_signal_cooldown_seconds": self.duplicate_signal_cooldown_seconds,
                    "trade_cooldown_minutes": self.trade_cooldown_minutes,
                    "max_trades_per_symbol": self.max_trades_per_symbol,
                    "early_entry_enabled": self.early_entry_enabled,
                    "early_entry_min_score": self.early_entry_min_score,
                },
                "trade_management": {
                    "trailing_sl": True,
                    "trailing_sl_trigger_pct": self.trailing_stop_trigger_pct,
                    "trailing_sl_lock_pips": self.trailing_stop_lock_pips,
                    "trailing_sl_step_pct": self.trailing_stop_step_pct,
                    "trailing_sl_min_step_pips": self.trailing_stop_min_step_pips,
                    "trailing_tp": self.trailing_tp_enabled,
                    "trailing_tp_trigger_pct": self.trailing_tp_trigger_pct,
                    "trailing_tp_extension_pct": self.trailing_tp_extension_pct,
                    "trailing_tp_cooldown_seconds": self.trailing_tp_cooldown_seconds,
                    "partial_tp": self.partial_tp_enabled,
                    "partial_tp_trigger_r": self.partial_tp_trigger_r,
                    "partial_tp_close_pct": self.partial_tp_close_pct,
                    "partial_tp_move_sl_to_be": self.partial_tp_move_sl_to_be,
                    "reverse_profit_exit": self.reverse_profit_exit_enabled,
                    "reverse_profit_min_r": self.reverse_profit_min_r,
                    "reverse_profit_giveback_pct": self.reverse_profit_giveback_pct,
                    "reverse_profit_close_pct": self.reverse_profit_close_pct,
                    "professional_gate": self.professional_gate_enabled,
                    "min_execution_grade": self.min_execution_grade,
                    "min_professional_setup_score": self.min_professional_score,
                    "min_professional_conviction": self.min_professional_conviction,
                    "false_move_detection": self.false_move_detection_enabled,
                    "news_mode": self.news_mode_enabled,
                    "news_block_unsafe": self.news_block_unsafe,
                    "news_risk_multiplier": self.news_risk_multiplier,
                    "news_ladder": self.news_ladder_enabled,
                    "news_ladder_max_addons": self.news_ladder_max_addons,
                    "news_ladder_min_r": self.news_ladder_min_r,
                    "news_ladder_volume_pct": self.news_ladder_volume_pct,
                    "news_ladder_cooldown_seconds": self.news_ladder_cooldown_seconds,
                    "ict_enabled": self.ict_enabled,
                    "ict_min_risk_reward": self.ict_min_risk_reward,
                    "ict_allowed_sessions": sorted(self.ict_allowed_sessions),
                },
                "current_regimes": self.current_regimes,
                "strategy_context": self.strategy_context,
                "logic_feed": self.logic_feed[-20:],
                "signal_history": self.signal_history[-50:],
                "future_trades": self.future_trades[-50:],
                "license": license_status,
                "live_trading_disabled": self.live_trading_disabled,
            }
        except Exception as e:
            logger.error(f"Status error: {e}")
            return None


# Global engine instance
engine = None


def start_engine():
    """Initialize and start the engine"""
    global engine
    if engine is None:
        engine = TradingEngine()
        if engine.connect():
            thread = threading.Thread(target=engine.start, daemon=True)
            thread.start()
            return True
    return False


def stop_engine():
    """Stop the engine"""
    global engine
    if engine:
        engine.stop()
        engine.disconnect()
        return True
    return False


def get_engine():
    """Get the current engine instance"""
    global engine
    return engine
