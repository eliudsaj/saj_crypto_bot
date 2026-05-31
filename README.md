# Nexus Trading Bot

Fair Value Gap (FVG) trading automation system with an MT5 execution layer, AI-style War Room decisioning, live risk controls, and a real-time dashboard.

## Current Runtime Behavior

- Dashboard updates in real time through Socket.IO, with API polling as a fallback.
- The engine checks open positions and trailing management every loop.
- Signal scanning is currently configured for fast early-entry mode: **about every 3 seconds**, while M5 data is still used for structure.
- Trades are **not** placed every 5 minutes automatically. A trade only happens when signal quality, War Room conviction, validation rules, risk checks, kill switches, and lockout checks all pass.
- Per-symbol trade lockout defaults to **1 active trade per symbol** and a **3 minute cooldown** after the symbol is clear.

## Dashboard

The dashboard separates account and trade metrics so profit/loss is easier to read:

- **Open P&L**: floating profit/loss from currently open MT5 positions.
- **Closed P&L**: realized profit/loss from today's closed trade logs.
- **Net P&L**: open plus closed P&L.
- **Daily P&L**: equity movement since the bot session started.
- **Drawdown**: drop from the session peak equity.
- **Scan Mode / Last Scan / Next Scan**: current scan cadence.
- **Bot Score / Readiness**: composite health grade from connection, runtime, scan freshness, risk guardrails, trade management, and current signal quality.
- **Trailing SL / Trailing TP**: active trade-management state.
- **Trade Decision**: the best current setup, direction, trade type, entry/SL/TP, R:R, spread, and READY/WATCH/WAIT state.
- **Confirmed / Missing**: the decision panel separates passed components from blockers, so the dashboard shows why a setup is not ready.
- **Strategy Breakdown**: top scored setups with liquidity, MSS/BOS, order block, HTF, session, displacement, premium/discount, and spread components.
- **Execution Safety**: spread state per symbol so poor execution conditions stand out before entry.
- **Why No Trade?**: grouped rejection reasons by symbol.
- **Global Radar**: command view for watchlist candidates, early score, grade, spread safety, and component checks.
- **Trade Type**: each setup is classified as Scalp, Intraday, or Swing with suggested hold-time context.

## War Room Decision System

The bot combines rule-based and model-based inputs:

- **Analytic Engine** evaluates market structure, liquidity, volume, and FVG quality.
- **Predictive Engine** estimates directional probability from recent candle features.
- **Ensemble Decision** combines those scores into a final trade/wait decision.

The bot can still reject a detected FVG if conviction is weak, EMA validation fails, risk is too high, or a symbol is locked out.

## Project Modules

Recent stabilization work split cross-cutting features into small modules:

- `alerts/manager.py`: persistent JSONL alerts with Socket.IO push fallback.
- `journal/writer.py`: analytics-only strategy journal for every scanned setup decision.
- `risk/monitor.py`: read-only risk status exposed to the dashboard.
- `analytics/performance.py`: backtest and trade-performance metrics.
- `analytics/edge_diagnostics.py`: component-level edge attribution for symbols, sessions, setups, and confirmations.
- `analytics/forward_validation.py`: forward-test validation by strategy version, config version, and symbol group.
- `users/` and `tenants/`: dormant SaaS scaffolding for future user roles, subscriptions, and tenant file isolation. Local mode remains active while `SAAS_MODE=false`.
- Runtime data files are auto-created under `data/` when the app starts.

## Quick Start

### Windows

```powershell
run.bat
```

If `run.bat` fails:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

### Linux/Mac

```bash
chmod +x run.sh
./run.sh
```

If `run.sh` fails:

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Open the dashboard at:

```text
http://127.0.0.1:5000
```

## Configuration

Copy `.env.template` to `.env` and fill in your broker credentials.

```env
SAAS_MODE=false
SAAS_ADMIN_EMAIL=admin@nexus.local
SAAS_ADMIN_PASSWORD=change-me-now
SAAS_DEFAULT_TENANT=default
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=

TRADING_SYMBOLS=EURUSD,GBPUSD,USDJPY
AUTO_APPEND_MARKET_WATCH_SYMBOLS=false
TIMEFRAME=M5
TRADE_VOLUME=0.1
POSITION_SIZING_MODE=fixed
TAKE_PROFIT_R_MULTIPLIER=2.0
TAKE_PROFIT_R_MULTIPLIER_SCALP=1.5
```

### Risk

The bot uses fixed lot sizing by default. `TRADE_VOLUME` is the order size, while the strategy decides entry, SL, and TP.

Risk guardrail values are stored as decimal fractions in code:

```env
POSITION_SIZING_MODE=fixed
RISK_PERCENT=0.01
MAX_EXPOSURE_PERCENT=0.05
DAILY_PROFIT_CAP=0.02
MIN_EXPECTED_R=1.2
TAKE_PROFIT_R_MULTIPLIER=2.0
TAKE_PROFIT_R_MULTIPLIER_SCALP=1.5
```

Examples:

- `POSITION_SIZING_MODE=fixed` means use `TRADE_VOLUME` for every base order.
- `POSITION_SIZING_MODE=risk_percent` means calculate lots from equity, stop distance, and `RISK_PERCENT`.
- `TAKE_PROFIT_R_MULTIPLIER=2.0` means TP is calculated at 2R from the strategy SL distance.
- `0.01` means 1%.
- `0.05` means 5%.
- `0.02` means 2%.

### Scan Timing

Fast early-entry behavior:

```env
SCAN_ON_NEW_CANDLE=false
SCAN_TIMEFRAME_MINUTES=5
SCAN_INTERVAL_SECONDS=3
ENGINE_LOOP_SLEEP_SECONDS=3
DUPLICATE_SIGNAL_COOLDOWN_SECONDS=300
```

When `SCAN_ON_NEW_CANDLE=false`, the bot scans on the fixed interval above. This is faster and can show early-entry candidates sooner, but it can be noisier.

If you want calmer candle-close behavior instead:

```env
SCAN_ON_NEW_CANDLE=true
SCAN_TIMEFRAME_MINUTES=5
```

### Trade Lockout

```env
SIGNAL_LOCKOUT_ENABLED=true
MAX_TRADES_PER_SYMBOL=1
TRADE_COOLDOWN_MINUTES=1
```

This prevents repeated entries on the same symbol and reduces duplicate trigger loops.

### Early Entry Scoring

The bot no longer relies on FVG alone, and all confirmations do not need to happen at once. It detects setup archetypes:

- Sweep Reversal: liquidity sweep plus rejection/displacement or premium-discount alignment.
- Structure Continuation: MSS/BOS plus HTF bias or displacement.
- Order Block Mitigation: aligned order block plus value zone or HTF support.
- FVG Momentum: FVG plus displacement and one directional context filter.
- Scalp Retest: spread-safe retest with at least one structural trigger.

The supporting components are liquidity sweep, MSS/BOS, order block/FVG alignment, higher timeframe bias, session quality, displacement, premium/discount, and spread safety.

```env
FEATURE_EARLY_ENTRY=true
EARLY_ENTRY_MIN_SCORE=0.50
EXECUTION_ARCHETYPE_SCORE_THRESHOLD=0.58
```

When `FEATURE_EARLY_ENTRY=true`, the scanner can create a candidate even without a fresh FVG if a valid structure archetype appears. The dashboard shows this in **Trade Decision** with the archetype, score, confirmed components, and missing blockers.

### False Move and News Guard

The bot now classifies trap-like moves and event spikes before execution:

- **Liquidity Sweep Reversal**: price takes a prior high/low and reclaims the range.
- **Failed Breakout**: price breaks a level but cannot close with follow-through.
- **Real Breakout**: price closes beyond structure with a cleaner body.
- **News / Event Spike**: candle range or body expands far beyond recent average, with spread safety checked.
- **Post-News Retest**: event impulse is only tradable after confirmation and reduced risk.

```env
FEATURE_FALSE_MOVE_DETECTION=true
FEATURE_NEWS_MODE=true
NEWS_BLOCK_UNSAFE=true
NEWS_RISK_MULTIPLIER=0.35
NEWS_ALLOW_RETEST_FOLLOW=true
FEATURE_NEWS_LADDER=true
NEWS_LADDER_MAX_ADDONS=2
NEWS_LADDER_MIN_R=0.55
NEWS_LADDER_VOLUME_PCT=0.35
NEWS_LADDER_COOLDOWN_SECONDS=180
```

Unsafe news spikes are blocked. Confirmed post-news retest/follow trades are allowed only when spread is manageable and the bot reduces position size using `NEWS_RISK_MULTIPLIER`.

The news ladder is a controlled add-on system. It does not add during the first spike. It can add only when an existing position is already at least `NEWS_LADDER_MIN_R` in profit or has already taken partial profit, spread is safe, and the add-on cooldown has passed. Each add-on uses `NEWS_LADDER_VOLUME_PCT` of the base/remaining position size and is capped by `NEWS_LADDER_MAX_ADDONS`.

### Trade Horizon

Every signal is classified into a management style:

- **SCALP**: tight risk, smaller target, spread safe, usually 5-30 minutes.
- **INTRADAY**: normal M5/M15 opportunity, usually 30 minutes to 4 hours.
- **SWING**: wider target, HTF aligned, usually 4 hours to 2 days.

Global Radar can filter by these types and paginates the grid so the watchlist stays readable.

### War Room

```env
FEATURE_WAR_ROOM=true
ANALYTIC_WEIGHT=0.6
PREDICTIVE_WEIGHT=0.4
CONVICTION_THRESHOLD=0.25
MARKET_EXECUTION_SCORE_THRESHOLD=0.45
MARKET_EXECUTION_CONVICTION_THRESHOLD=0.35
```

### Professional Execution Gate

The dashboard can still show all C/D watchlist ideas, but execution is limited to cleaner setups:

```env
FEATURE_PROFESSIONAL_EXECUTION_GATE=true
MIN_EXECUTION_GRADE=B
ALLOW_C_GRADE_SCALPS=false
MIN_PROFESSIONAL_SETUP_SCORE=0.62
MIN_PROFESSIONAL_CONVICTION=0.30
MIN_SESSION_SCORE_FOR_TRADE=0.45
MIN_SESSION_SCORE_FOR_SCALP=0.65
BLOCK_CONTEXT_WATCH_TRADES=true
```

This gate requires real structure such as liquidity sweep, MSS/BOS, or displacement before the bot can execute. Context-only setups stay watch-only.

### Strict Quality Gate

To reduce overtrading, the live execution path rejects weak or choppy setups before they can reach MT5. The gate checks structural quality, displacement body strength, candle close quality, volatility quality, session quality, HTF agreement, liquidity context, spread noise, anti-chop state, and confidence persistence across scans.

```env
FEATURE_STRICT_QUALITY_GATE=true
MIN_STRUCTURAL_QUALITY_SCORE=0.55
MIN_DISPLACEMENT_BODY_RATIO=1.35
MIN_CANDLE_CLOSE_QUALITY=0.62
MIN_VOLATILITY_QUALITY=0.35
MIN_MARKET_QUALITY_SCORE=0.42
MIN_CONFIDENCE_PERSISTENCE=2
REQUIRE_HTF_AGREEMENT=true
REQUIRE_LIQUIDITY_CONTEXT=true
```

### Adaptive Statistical Weighting

The bot can apply an interpretable adaptive overlay that reads closed-trade diagnostics and slightly boosts or penalizes live setup scores by symbol, session, archetype, spread state, volatility, and core structure components. It uses minimum sample thresholds, capped adjustments, confidence checks, severe-drawdown cooldowns, and automatic toxic-symbol suppression.

```env
ADAPTIVE_WEIGHTS_ENABLED=true
ADAPTIVE_MIN_SAMPLE=12
ADAPTIVE_MIN_CONFIDENCE=0.55
ADAPTIVE_MAX_BOOST=0.12
ADAPTIVE_MAX_PENALTY=0.22
ADAPTIVE_SEVERE_DRAWDOWN_R=-6.0
ADAPTIVE_COOLDOWN_MINUTES=90
```

The dashboard Trade Decision panel shows the base score, adjusted score, multiplier, and plain-language reasons for each adaptive adjustment.

### Forward-Test Validation

Forward-test evidence is isolated from historical backtests. Closed live trades are appended to `data/forward_trades.jsonl` with:

- `STRATEGY_VERSION`
- `CONFIG_VERSION` or an automatic config fingerprint
- `SYMBOL_GROUP` or an automatic group inferred from configured symbols

The dashboard **Strategy Validation** card and `GET /api/analytics/strategy-validation` report rolling expectancy, Sharpe, drawdown, confidence interval, R multiple, and status:

- `UNPROVEN`: not enough stable forward evidence.
- `PROMISING`: positive early read, but not enough proof to scale.
- `VALIDATED`: minimum sample, positive expectancy, stable rolling expectancy, and CI above zero.
- `DEGRADED`: edge has weakened or recent rolling performance has turned negative.

Useful validation settings:

```env
STRATEGY_VERSION=local-dev
CONFIG_VERSION=
SYMBOL_GROUP=
VALIDATION_MIN_SAMPLE=30
VALIDATION_ROLLING_WINDOW=30
```

### Trade Management

```env
TRAILING_STOP_TRIGGER_PCT=0.25
TRAILING_STOP_LOCK_PIPS=1.0
TRAILING_STOP_STEP_PCT=0.20
TRAILING_STOP_MIN_STEP_PIPS=0.5

FEATURE_TRAILING_TAKE_PROFIT=true
TRAILING_TP_TRIGGER_PCT=0.8
TRAILING_TP_EXTENSION_PCT=0.5
TRAILING_TP_COOLDOWN_SECONDS=300

FEATURE_PARTIAL_TAKE_PROFIT=true
PARTIAL_TP_TRIGGER_R=0.35
PARTIAL_TP_CLOSE_PCT=0.5

FEATURE_REVERSE_PROFIT_EXIT=true
REVERSE_PROFIT_MIN_R=0.20
REVERSE_PROFIT_GIVEBACK_PCT=0.30
REVERSE_PROFIT_CLOSE_PCT=1.0
REVERSE_AFTER_PARTIAL_LOCK_R=0.10
```

- `TRAILING_STOP_STEP_PCT` is intentionally tight by default, but for high-volatility pairs such as `XAUUSD` it is safer to widen this value to `0.50` or higher so the trail has room to breathe before trailing TP extension can trigger.
- `FEATURE_NEWS_LADDER` addons scale an active trade and do not consume an additional `MAX_TRADES_PER_SYMBOL` slot.

Trailing stop-loss protects gains once price moves toward TP. Trailing take-profit can extend TP after price reaches the configured trigger percentage of the original TP distance. Partial take-profit banks part of the position at the configured R multiple, then moves SL to breakeven. Reverse profit exit watches max favorable excursion and closes profitable trades if price gives back too much of the open gain.

## API

Useful endpoints:

- `GET /api/bot/status`
- `POST /api/bot/start`
- `POST /api/bot/stop`
- `GET /api/positions`
- `GET /api/signals`
- `GET /api/logs`
- `GET /api/stats`
- `GET /api/alerts`
- `POST /api/alerts/clear`
- `GET /api/risk/status`
- `GET /api/journal`
- `GET /api/config`
- `POST /api/config`
- `GET /api/tenant/context`
- `GET /api/watchlist`
- `GET /api/pending-orders`
- `POST /api/pending-orders/place`
- `POST /api/panic-close`

## Safety Notes

- The dashboard has a global kill switch and panic close, but live trading still depends on MT5/broker execution rules.
- Keep `MAX_TRADES_PER_SYMBOL=1` while tuning strategy quality.
- Review logs after changing conviction thresholds or risk settings.
- Leave `SAAS_MODE=false` for the current single-user local bot. SaaS mode is only an architecture foundation and does not yet provide a full login/subscription product.

## Troubleshooting

- If dashboard stats show zeros, check whether MT5 is connected and whether today's log file is valid JSON.
- If repeated signals appear, verify `SCAN_ON_NEW_CANDLE=true` and `DUPLICATE_SIGNAL_COOLDOWN_SECONDS=300`.
- If no trades occur, inspect the logic feed for War Room rejection, EMA rejection, lockout, exposure, or kill-switch messages.
- If trades occur too frequently, increase `TRADE_COOLDOWN_MINUTES` or raise `CONVICTION_THRESHOLD`.
