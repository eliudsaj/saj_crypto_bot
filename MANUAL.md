# Nexus Trading Bot - Complete User Manual

**Version 1.0**  
**Last Updated: March 2026**

---

## Table of Contents

1. [Introduction](#introduction)
2. [System Requirements](#system-requirements)
3. [Installation & Setup](#installation--setup)
4. [Configuration Guide](#configuration-guide)
5. [Trading Rules & Logic](#trading-rules--logic)
6. [Risk Management](#risk-management)
7. [Dashboard Usage Guide](#dashboard-usage-guide)
8. [Dashboard Deep Dive](#dashboard-deep-dive)
9. [Step-by-Step Trading Workflow](#step-by-step-trading-workflow)
10. [Advanced Settings](#advanced-settings)
11. [Monitoring & Performance](#monitoring--performance)
12. [Troubleshooting](#troubleshooting)
13. [FAQ](#faq)

---

## Introduction

### What is Nexus Trading Bot?

**Nexus Trading Bot** is an automated trading system designed for **MetaTrader5 (MT5)** that identifies and executes trades based on **Fair Value Gap (FVG)** technical analysis combined with strict risk management and trade validation rules.

### Key Capabilities

✅ **Automated FVG Detection** – Scans forex/crypto markets continuously  
✅ **Rule-Based Validation** – Only enters trades that meet predefined criteria  
✅ **Smart Risk Sizing** – Automatically calculates lot sizes based on your risk tolerance  
✅ **Position Management** – Handles trailing stops, break-even adjustments, and multiple positions  
✅ **Real-Time Dashboard** – Web-based control panel with live metrics  
✅ **Emotional Discipline** – Revenge trading cooldowns and max exposure limits  
✅ **Complete Logging** – Every decision (signal, rejection, trade) is logged for analysis  

### The Trading Philosophy

The bot operates under the **Trade Bible** philosophy:
- **No emotion** → Strictly rule-based execution
- **Positive expectancy** → Only take trades with sufficient risk/reward
- **Capital preservation** → Risk a small percentage per trade
- **Discipline** → No revenge trading, no overexposure

---

## System Requirements

### Hardware
- **CPU**: Dual-core or better
- **RAM**: 2GB minimum, 4GB recommended
- **Disk**: 500MB free space
- **Network**: Stable internet connection

### Software
- **Windows 10+** / Linux / macOS
- **Python 3.8+**
- **MetaTrader5** (running with active connection)
- **Web browser** (for dashboard access)

### Broker Requirements
- Active MT5 account with liquidity provider
- Valid trading credentials (Account ID, Password, Server)
- Sufficient account balance to cover minimum lot size

---

## Installation & Setup

### Step 1: Download & Extract

```bash
# Clone or download the project
git clone <repository-url>
cd bottter/mASTER
```

### Step 2: Install Python Dependencies

```bash
# On Windows
pip install -r requirements.txt

# On Linux/Mac
pip3 install -r requirements.txt
```

**Required packages:**
- Flask (web framework)
- pandas (data processing)
- MetaTrader5 (broker API)
- python-dotenv (environment config)

### Step 3: Configure Environment

Copy `.env.template` to `.env`:

```bash
# Windows
copy .env.template .env

# Linux/Mac
cp .env.template .env
```

### Step 4: Add Your MT5 Credentials

Edit `.env` with your broker details:

```
MT5_ACCOUNT=YOUR_ACCOUNT_ID
MT5_PASSWORD=YOUR_PASSWORD
MT5_SERVER=YOUR_SERVER_NAME
```

*Find these in MetaTrader5:* File → Account Information

### Step 5: Start the Bot

**Windows:**
```bash
run.bat
```

**Linux/Mac:**
```bash
chmod +x run.sh
./run.sh
```

**Or manually:**
```bash
python app.py
```

✅ **Bot is running when you see:**
```
WARNING in app.run
Running on http://127.0.0.1:5000
```

### Step 6: Access Dashboard

Open your web browser and go to:
```
http://localhost:5000
```

---

## Configuration Guide

### Essential Settings (in `.env`)

#### 1. **Trading Symbols**
```
TRADING_SYMBOLS=EURUSD,GBPUSD,USDJPY,AUDUSD
```
- Comma-separated list of forex pairs or symbols
- Bot will scan these 24/5 for signals
- Common pairs: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD

#### 2. **Trade Volume (Fixed Lot Size)**
```
TRADE_VOLUME=0.01
```
- Initial default lot size (in contracts)
- Will be adjusted by risk% algorithm
- Example: 0.01 = 10,000 units for EURUSD

#### 3. **Risk Percentage Per Trade**
```
RISK_PERCENT=1
```
- % of account equity risked per trade
- Example: 1% means if account = $10,000, risk = $100
- **Recommended range:** 0.5% – 2%

#### 4. **Maximum Exposure Percentage**
```
MAX_EXPOSURE_PERCENT=5
```
- Max total $-risk on all open positions
- Example: 5% means never more than 5% equity at risk
- Prevents over-trading

#### 5. **Minimum Profit Target (Pips)**
```
MIN_PROFIT_PIPS=50
```
- Minimum expected profit distance in pips
- Signals with small TP targets are rejected
- Example: 50 means TP must be ≥50 pips away from entry

#### 6. **No-Revenge Trading Cooldown**
```
NO_REVENGE_COOLDOWN_SECONDS=86400
```
- Seconds to pause trading after a loss
- Default = 86400 (24 hours)
- Prevents emotional revenge trading

#### 7. **Timeframe**
```
TIMEFRAME=M5
```
- Candlestick aggregation period
- Supported: M1, M5, M15, M30, H1, H4, D1
- M5 is default (5-minute candles)

#### 8. **Rule Toggles**
```
RULE_EMA=true
RULE_VOLUME=true
RULE_PO3=true
```
- Enable/disable specific validation rules
- Can be toggled live from dashboard

---

## Trading Rules & Logic

### Rule 1: Fair Value Gap (FVG) Detection ✅

**What is an FVG?**
An FVG is a zone of imbalance created when price moves sharply, leaving a gap that price tends to fill.

**Detection Logic:**
```
Bullish FVG = Gap between Low[2] > High[0]
Bearish FVG = Gap between High[2] < Low[0]
```

**Signal Generation:**
- **Entry**: High boundary of bullish FVG (or low for bearish)
- **Stop Loss**: Lowest point of the pivot candle
- **Take Profit**: 2x the gap size (aggressive targeting)

**Example:**
```
Bar[2]: High=100, Low=99
Bar[1]: High=101, Low=100.5    (pivot)
Bar[0]: High=105, Low=102      (sweep)

Bullish FVG detected if 102 > 100
Entry = 100, SL = 100.5, TP = 102 + (102-100) = 104
```

---

### Rule 2: EMA Filter 📊

**Purpose:** Ensure trade direction aligns with short-term trend

---

## 2026 Safety & Signal Lockout Update (Critical)

A major update has been added to prevent repeated entries on the same symbol and avoid over-trading:

1. Global Trade Registry in `engine.py`
   - `trade_registry` dictionary maintains per-symbol:
     - `active_trades`
     - `last_trade_time`
     - `cooldown_until`

2. Signal lockout rules:
   - If `active_trades(symbol) >= max_trades_per_symbol` → new signals are blocked
   - After close, symbol is in cooldown for `trade_cooldown_minutes` (default 15)

3. New methods:
   - `_check_signal_lockout(symbol)`
   - `_register_trade_open(symbol)`
   - `_register_trade_close(symbol)`
   - `_get_registry_status(symbol=None)`

4. Configurable UI/API fields:
   - `MAX_TRADES_PER_SYMBOL` (default 1)
   - `TRADE_COOLDOWN_MINUTES` (default 15)
   - `SIGNAL_LOCKOUT_ENABLED`

5. Hard guard is now in scan execution before final trade order:
   - Any lockout rejection is logged as `Signal Lockout: reason`
   - Signal is marked in dashboard less fav

6. Safety improvements:
   - `/api/positions` returns stable response when engine is offline
   - `/api/kill` no longer returns error when engine is down

7. Critical risk rules emphasized:
   - `MAX_EXPOSURE_PERCENT` controls total risk bucket
   - `DAILY_PROFIT_CAP` cuts trading after daily target
   - `NO_REVENGE_COOLDOWN_SECONDS` enforces cooldown after a loss

---

### Risk-Provable Audits

- `validate_trade` is now correctly imported from `bible_logic.py` in `engine.py`.
- Fallback mode still can permit lower conviction trades if scalp context is high, but you can enforce stricter criteria using `MIN_CONVICTION_TO_TRADE` in future updates.
- API endpoints have hardening checks and return safe state even when bot is disabled.
- Add API key / bearer token in front-end in the future for secure operation.

---

### Best practice for deploy

1. Run local tests once dependencies installed:
   ```bash
   python -m pip install -r requirements.txt
   python app.py
   ```
2. Use dashboard config to set lockout and risk: 1 per symbol + 15min cooldown.
3. Confirm in logs: war room rejections, `Signal Lockout`, `Max exposure reached`, `Daily cap`.
4. For feature-hardening, implement post-trade clean-up in `check_positions()`:
   - `active_trades.pop(symbol, None)`
   - `_register_trade_close(symbol)`

---

### Conclusion

This manual section is the “final compile” focused on safety and anti-overtrade controls you requested. Re-run your integration tests and monitor `logs/trades_YYYY-MM-DD.json` to validate lockout behavior in live conditions.

**Logic:**
- **For LONG trades**: Price must be above the 50-period EMA on M30
- **For SHORT trades**: Price must be below the 50-period EMA on M30

**Example:**
- M30 50-EMA = 1.0850
- Signal price = 1.0900
- ✅ LONG allowed (1.0900 > 1.0850)
- ❌ SHORT rejected (price not below EMA)

**Can be disabled:** Toggle `RULE_EMA=false` from Settings

---

### Rule 3: Volume Filter 📈

**Purpose:** Confirm signal strength with volume

**Logic:**
- Current candle volume must be > 1.5x average of past 10 candles
- High volume = confidence in the move

**Example:**
```
Vol average (last 10): 1000 contracts
Vol current: 1600 contracts
1600 > 1500? ✅ YES → Signal valid
1400 > 1500? ❌ NO → Signal rejected
```

**Can be disabled:** Toggle `RULE_VOLUME=false` from Settings

---

### Rule 4: Asian Sweep (PO3 Rule) 🌏

**Purpose:** Ensure FVG aligns with multi-session trading structure

**Logic:**
1. Identify Asia session range (00:00 - 08:00 UTC)
2. Find high/low of that range
3. Require that post-Asia candles swept BEYOND that range

**Example (Bullish):**
```
Asia High: 1.0950
Asia Low: 1.0900

Post-Asia candles must include a bar with:
- High > 1.0950 (confirmed bullish sweep)
```

**Can be disabled:** Toggle `RULE_PO3=false` from Settings

---

### Rule 5: Target Distance & Expectancy

**Minimum Profit Pips:**
- Any signal with TP distance < 50 pips is rejected
- Ensures risk/reward is favorable (at least 1:1 typically)

**Expected R-Multiple:**
- Signal quality is rated by R = reward/risk
- Example: Entry=1.0900, SL=1.0850, TP=1.0950
  - Risk = 50 pips
  - Reward = 50 pips
  - R = 1.0 (1:1 ratio)

---

## Risk Management

### Core Principle: Capital Preservation

The bot implements **strict risk controls** to protect your account:

### 1. **Risk-Percent-Based Sizing**

**Concept:** Never risk more than X% of your account on one trade

**Formula:**
```
Risk Amount = Account Equity × Risk%
Volume = Risk Amount / (Risk Pips × Pip Value)
```

**Example:**
```
Account Equity: $10,000
Risk %: 1%
Risk Amount: $100

Signal SL distance: 50 pips
Pip value: $0.1 per pip (1 lot)
Required Volume: $100 / (50 × 0.1) = 0.2 lots

→ Trade with 0.2 lots, risking exactly $100
```

**Benefits:**
- ✅ Account grows even with small wins
- ✅ Losses are always limited
- ✅ Automatic scaling with account size

---

### 2. **Maximum Exposure Limit**

**Concept:** Never have more than X% of equity at risk across ALL positions

**Example:**
```
Account Equity: $10,000
Max Exposure: 5%
Max Risk at one time: $500

Position 1: Risk $100 (EURUSD) ✅
Position 2: Risk $150 (GBPUSD) ✅
Position 3: Risk $200 (USDJPY) ✅
Position 4: Risk $100 (AUDUSD) ❌ BLOCKED
  → Total would be $550, exceeds $500 limit
```

**Prevents:**
- Over-leverage
- Catastrophic losses
- Margin calls

---

### 3. **No Revenge Trading Cooldown**

**Concept:** After a losing trade, pause ALL trading for 24 hours

**Why?**
Emotional traders risk more after losses to "get even." This bot prevents it automatically.

**Example Timeline:**
```
10:00 AM - Losing trade closes (-$50)
↓ Cooldown activates (24 hours)
10:01 AM - 11:59 PM - New signals BLOCKED
↓ Cooldown expires
10:00 AM (next day) - Trading resumes
```

**Disable:** Set `NO_REVENGE_COOLDOWN_SECONDS=0`

---

### 4. **Trailing Stop Loss@15 Pips**

**Concept:** Once a trade is +15 pips in profit, SL trails behind price

**Example (LONG):**
```
Entry: 1.0900
SL (initial): 1.0850

Price moves to 1.0915 (+15 pips) →
SL moves to 1.0900 (break-even + 2 pips buffer)

Price moves to 1.0930 (+30 pips) →
SL moves to 1.0915

Profit locked in automatically as price rises
```

---

### 5. **Trade Logging & Stats Tracking**

**Every Trade is Recorded:**
- Entry price, stop loss, take profit
- Volume and risk amount
- Profit/loss
- Duration
- Closing reason

**Performance Metrics Calculated:**
```
- Win Rate: % of winning trades
- Average Win: Avg profit on winners
- Average Loss: Avg loss on losers
- Expectancy: (Win% × AvgWin) - (Loss% × AvgLoss)
- R Multiple: Reward / Risk multiple
```

---

## Dashboard Usage Guide

### Dashboard Overview

Access at `http://localhost:5000`

### Section 1: Bot Status & Account

**Status Panel:**
- **Status:** Online/Offline indicator
- **Connected:** MT5 connection status
- **Active Trades:** Number of open positions
- **Balance:** Account balance
- **Equity:** Current account value
- **Daily Profit:** P&L today
- **Floating Drawdown:** Unrealized loss on open positions

### Section 2: Recent Signals Table

Shows signals detected by the bot:

| Column | Meaning |
|--------|---------|
| **Symbol** | Trading pair (EURUSD, etc.) |
| **Nature** | Bullish/Bearish + pattern (Pullback, Retest, Breakout) |
| **Entry** | Entry price if trade executed |
| **Est. Time** | Estimated minutes to reach TP |
| **Status** | ready/blocked/killed/active |
| **Context** | Gap size or other info |

**Click any row** to see detailed modal with:
- Entry, SL, TP levels
- Status reason (if blocked)
- Expected R-multiple
- Probability estimation

### Section 3: Positions Table

Shows currently open positions:

| Column | Meaning |
|--------|---------|
| **Symbol** | Pair name |
| **Type** | BUY or SELL |
| **Volume** | Lot size |
| **Entry** | Entry price |
| **Current** | Current price |
| **P&L** | Profit or loss |

---

### Section 4: Kill Switch

**Global Kill Switch:**
- Turn **OFF** to pause trading on all symbols
- Useful for emergency stops or after big losses

**Per-Symbol Kill Switch:**
- Disable trading on specific pair
- Use when specific pair is choppy or broken

**Example:**
```
Global OFF → No trades any symbol
Select EURUSD + OFF → No EURUSD trades, other pairs OK
```

---

### Section 5: Rule Panel

**Toggle validation rules in real-time:**

| Rule | Effect |
|------|--------|
| **EMA Filter** | Require price above/below 50-EMA |
| **Volume Filter** | Require above-average volume |
| **PO3 / Asian Sweep** | Require Asian session sweep |

Changes apply immediately to new signals.

---

### Section 6: Logic Feed

**Real-time log of bot reasoning:**

Shows:
- ✅ Signals detected
- ❌ Signals rejected with reason
- 📊 Trades entered
- 🎯 Positions closed

Example:
```
10:05:00 EURUSD: FVG detected (Bullish Pullback)
10:05:01 EURUSD: EMA filter passed
10:05:02 EURUSD: Volume filter passed
10:05:03 EURUSD: Asian sweep confirmed
10:05:04 EURUSD: Trade executed BUY 0.05 lots
```

---

### Section 7: Trade Logs

**Access via "Logs" tab**

Shows all events:
1. **Trades section:** Each executed trade with details
2. **Rejections section:** Signals rejected with reasons

**Click any log entry** to see modal with full details

---

### Section 8: Settings

**Configure bot parameters live:**

| Setting | Default | Range | Notes |
|---------|---------|-------|-------|
| Trading Symbols | EURUSD,GBPUSD,... | Any pair | Comma-separated |
| Trade Volume | 0.01 | 0.01-100 | Adjusted by risk% |
| Risk % | 1 | 0.1-5 | % of equity per trade |
| Max Exposure % | 5 | 1-100 | Max risk on all trades |
| Min Profit Pips | 50 | 10-500 | Minimum expected profit |
| MT5 Account | (your ID) | - | For reference |
| MT5 Server | (your server) | - | For reference |

**Changes persist** to `.env` file after "Save Settings"

---

## Dashboard Deep Dive

### Complete Overview

The **Nexus Trading Bot Dashboard** is the nerve center of your automated trading system. It displays real-time account metrics, signal detection, trade execution, and comprehensive statistics. Understanding each section and metric is crucial for effective monitoring and optimization.

**Access:** `http://localhost:5000` (when bot is running)

**Update Frequency:** Every 2 seconds for live data refresh

---

### Dashboard Status Panel (Top Section)

The status panel displays critical account and connection information at a glance:

#### Real-Time Metrics

| Metric | Description | What It Means |
|--------|-------------|--------------|
| **Status** | Online / Offline | Is the trading engine running? |
| **Connected** | Yes / No | Is MT5 connected and responding? |
| **Account** | Your MT5 Account ID | For reference (prevents wrong account trading) |
| **Balance** | Account cash balance | Available funds (excludes open position profit/loss) |
| **Equity** | Current total value | Balance + floating profit/loss on open trades |
| **Daily Profit** | Today's P&L | Profit/loss since start of day (automatically resets at midnight UTC) |
| **Floating Drawdown** | Current unrealized loss | If positive: you're in drawdown from peak |

**Example Reading:**
```
Status: Online ✅
Connected: Yes ✅
Balance: $10,000.00
Equity: $9,850.00 (open trades losing $150)
Daily Profit: +$250.00 (won trades today)
Floating Drawdown: $150.00 (current unrealized loss)
```

#### Understanding Equity vs. Balance

- **Balance** = Your actual cash (fixed)
- **Equity** = Balance + floating P&L on open trades (changes constantly)
- **Margin Used** = Equity × position size / leverage

**Key Insight:** If equity drops 25% below balance, you may face margin call warnings.

---

### All Trading Rules Explained

The bot uses a **multi-layer validation system** to ensure only high-probability trades are executed. Understanding each rule is essential.

#### Rule 1: Fair Value Gap (FVG) Detection ✅

**Purpose:** Identify price imbalances that create high-probability setup

**How It Works:**

A Fair Value Gap is created when price moves rapidly, leaving an untouched zone of price vacuum. Price naturally returns to "fill" these gaps.

**Visual Example (M5 Candles):**
```
Bar[2] (2 bars back):    High=1.0900, Low=1.0890    [Range: 10 pips]
Bar[1] (1 bar back):     High=1.0895, Low=1.0875    [Retrace inside Bar[2]]
Bar[0] (Current):        High=1.0920, Low=1.0905    [Sweeps above Bar[2] high]

Result: BULLISH FVG detected
├─ Entry Price: 1.0900 (top of Bar[2])
├─ Stop Loss: 1.0875 (low of Bar[1])
├─ Gap Size: 10 pips (1.0905 - 1.0895)
└─ Take Profit: 1.0920 (1.0895 + 25 pips for 1:2.5 R:R)
```

**Why It Works:** Price vacuum gets filled by subsequent buyers/sellers rushing to catch the move.

**Detection Frequency:** Scanned every 5 seconds on M5 timeframe for all configured symbols

**Probability:** ~60-70% of identified FVGs result in profitable trades (when combined with other rules)

---

#### Rule 2: EMA Filter (Trend Confirmation) 📊

**Purpose:** Ensure your entry aligns with the main trend direction

**Default:** Enabled ✅ (toggle in Settings)

**How It Works:**
- Calculates **50-period Exponential Moving Average** on **M30 timeframe** (higher timeframe = stronger trend)
- For BUY signals: Current close price must be **ABOVE** 50-EMA
- For SELL signals: Current close price must be **BELOW** 50-EMA

**Technical Details:**
```
EMA = Current_Close × (2 / (50+1)) + EMA_Previous × (1 - (2 / (50+1)))
     = Current_Close × 0.0392 + EMA_Previous × 0.9608
```

**Example Scenario:**
```
Bullish FVG Detected on M5:
├─ Signal Price: 1.0850
├─ M30 50-EMA: 1.0830
├─ Is 1.0850 > 1.0830? YES ✅
└─ RESULT: EMA filter PASSED

vs.

Bullish FVG Detected on M5:
├─ Signal Price: 1.0820
├─ M30 50-EMA: 1.0830
├─ Is 1.0820 > 1.0830? NO ❌
└─ RESULT: EMA filter FAILED (signal rejected)
```

**Why It Works:** Trading WITH the main trend has higher success rate than against it. Avoids low-probability counter-trend entries.

**When to Disable:** During strong breakouts where trend is reversing (rare cases)

---

#### Rule 3: Volume Filter (Strength Confirmation) 📈

**Purpose:** Confirm the move has conviction with above-average trading volume

**Default:** Enabled ✅ (toggle in Settings)

**How It Works:**
- Calculates average volume of **last 10 candles** on M5
- Current candle volume must be **> 1.5× the 10-candle average**
- High volume = price move is supported by genuine buying/selling interest

**Formula:**
```
volume_average_10 = (Vol[9] + Vol[8] + ... + Vol[0]) / 10
current_volume >= volume_average_10 × 1.5  → PASS
```

**Example:**
```
Last 10 candle volumes: [1000, 1100, 950, 1050, 1200, 900, 1150, 1000, 1100, 1050]
Average: 10,500 / 10 = 1,050 contracts
Required threshold: 1,050 × 1.5 = 1,575 contracts

Current candle volume: 1,800 contracts
Is 1,800 >= 1,575? YES ✅ → VOLUME FILTER PASSED
```

**Why It Works:** High-volume moves are harder to reverse. Low-volume moves are often fake-outs.

**When to Disable:** During low-liquidity sessions (Asian hours, weekends) where volume is naturally thin

---

#### Rule 4: PO3 / Asian Sweep Validation 🌏

**Purpose:** Confirm directional bias by checking if price swept the Asia session extremes

**Default:** Enabled ✅ (toggle in Settings)

**Complexity:** Most technical of the three rules

**How It Works:**

**Step 1: Identify Asian Session Range**
```
Asia trading hours: 00:00 - 08:00 UTC
Scan all M30 candles in this time window
asian_high = highest point in range
asian_low = lowest point in range
```

**Step 2: Check for Post-Asia Sweep**
```
For BULLISH signals:
  → Must have a candle AFTER 08:00 UTC that closes ABOVE asian_high
  
For BEARISH signals:
  → Must have a candle AFTER 08:00 UTC that closes BELOW asian_low
```

**Example:**
```
Asian Session (00:00-08:00 UTC):
├─ High: 1.0950
├─ Low: 1.0900
└─ Range: 50 pips

London/NY Session (08:00+ UTC):
├─ Bar 1: 1.0940 (inside range)
├─ Bar 2: 1.0960 ← Breaks above asian_high ✅ BULLISH SWEEP CONFIRMED
└─ FVG bullish signal now valid

vs.

London/NY Session (08:00+ UTC):
├─ Bar 1: 1.0945 (inside range)
├─ Bar 2: 1.0948 (inside range)
├─ Bar 3: 1.0945 (inside range)
└─ Never broke above 1.0950 ❌ NO SWEEP (signal rejected)
```

**Why It Works:** The sweep confirms that professional traders (banks in that session) are supporting the direction, increasing probability of success.

**Technical Name:** "Price of Three" (PO3) = 3 key points: Asian High, Asian Low, Sweep confirmation

**When to Disable:** During choppy consolidation markets where sweeps are false

---

#### Rule 5: Minimum Profit Target Check

**Purpose:** Reject signals with insufficient profit potential

**Default:** 50 pips (configurable in Settings as "Min Profit Pips")

**How It Works:**
- Calculate distance from Entry to TP
- If distance < MIN_PROFIT_PIPS: **signal rejected**
- Ensures risk/reward ratio is favorable

**Formula:**
```
profit_pips = abs(TP - Entry) / pip_size
if profit_pips < MIN_PROFIT_PIPS:
    reject_signal("Profit target too close")
```

**Example:**
```
Signal found:
├─ Entry: 1.0850
├─ TP: 1.0875
├─ Profit: 25 pips
├─ MIN_PROFIT_PIPS setting: 50
└─ Is 25 >= 50? NO ❌ REJECTED

vs.

Signal found:
├─ Entry: 1.0850
├─ TP: 1.0900
├─ Profit: 50 pips
├─ MIN_PROFIT_PIPS setting: 50
└─ Is 50 >= 50? YES ✅ APPROVED
```

**Recommended Settings:**
- **30 pips** = Many signals, lower quality
- **50 pips** = Default, balanced (recommended)
- **75 pips** = Fewer signals, higher quality

**Why It Works:** Small profit targets create unfavorable risk/reward, especially with commission and slippage.

---

### Initial Configuration Values (Reference)

When you first install the bot, these are the **recommended starting values**:

#### Essential Settings

| Setting | Default | Recommended Range | Notes |
|---------|---------|-------------------|-------|
| **TRADING_SYMBOLS** | EURUSD,GBPUSD | Any pair pairs | Start with 2-3 major pairs |
| **TRADE_VOLUME** | 0.01 lots | 0.01-0.1 | Will be adjusted by risk% |
| **RISK_PERCENT** | 1% | 0.5% - 2% | 1% = $100 risk on $10k account |
| **MAX_EXPOSURE_PERCENT** | 5% | 3% - 10% | Never exceed 10% |
| **MIN_PROFIT_PIPS** | 50 pips | 30 - 75 pips | Target minimum profit |
| **NO_REVENGE_COOLDOWN** | 86400 sec | 0 - 604800 sec | 24 hours default (1 day) |

#### Rule Toggle Defaults

| Rule | Default | Recommended |
|------|---------|-------------|
| **RULE_EMA** | true | Keep enabled |
| **RULE_VOLUME** | true | Keep enabled |
| **RULE_PO3** | true | Can disable if too restrictive |

#### MT5 Connection

| Setting | Example |
|---------|---------|
| **MT5_ACCOUNT** | 12345678 |
| **MT5_PASSWORD** | YourPassword |
| **MT5_SERVER** | BrokerServer |

**Warning:** Never commit these to version control! Keep in `.env` only.

---

### Inner Workings: Complete Trading Workflow

Understanding how the bot processes signals from detection to trade execution will help you debug issues and optimize settings.

#### Workflow Diagram (High Level)

```
START TRADING
  ↓
[EVERY 5 SECONDS]
  ├─ SCAN SYMBOLS for FVG signals on M5
  │
  ├─ FOR EACH SIGNAL DETECTED:
  │  │
  │  ├─── [LAYER 1] Validate rules
  │  │    ├─ EMA filter
  │  │    ├─ Volume filter
  │  │    ├─ PO3 sweep
  │  │    └─ Stale data check
  │  │
  │  ├─── [LAYER 2] Check profitability
  │  │    └─ Profit >= MIN_PROFIT_PIPS?
  │  │
  │  ├─── [LAYER 3] Check trading conditions
  │  │    ├─ In revenge cooldown?
  │  │    ├─ Exposure limit exceeded?
  │  │    └─ Symbol/global kill switch on?
  │  │
  │  └─── IF ALL PASS → [LAYER 4] EXECUTE TRADE
  │       ├─ Calculate dynamic volume
  │       ├─ Place BUY/SELL order on MT5
  │       ├─ Set SL and TP automatically
  │       └─ Log trade details
  │
  ├─ MANAGE OPEN POSITIONS
  │  ├─ Check for closed trades
  │  ├─ Apply trailing stop (15+ pips profit)
  │  └─ Update floating P&L
  │
  └─ UPDATE DASHBOARD DATA
     ├─ Refresh equity/balance
     ├─ Update signal table
     ├─ Refresh position table
     └─ Calculate statistics
```

#### Detailed Step-by-Step Execution

**Step 1: Signal Detection (M5 Scan)**

```python
For each symbol in TRADING_SYMBOLS:
  1. Fetch last 4 M5 candles [Bar[3], Bar[2], Bar[1], Bar[0]]
  
  2. Check BULLISH FVG:
     if Bar[2].low > Bar[1].high:  # Gap exists above Bar[1]
        entry = Bar[1].high
        sl = Bar[2].low
        tp = Bar[0].high + (gap_size × 2)
        signal = {"type": "BULLISH", "entry": entry, "sl": sl, "tp": tp}
  
  3. Check BEARISH FVG:
     if Bar[2].high < Bar[1].low:  # Gap exists below Bar[1]
        entry = Bar[1].low
        sl = Bar[2].high
        tp = Bar[0].low - (gap_size × 2)
        signal = {"type": "BEARISH", "entry": entry, "sl": sl, "tp": tp}
  
  4. Timestamp signal (age check later)
```

**Step 2: Rule Validation**

```python
if RULE_EMA:
    ema50_M30 = calculate_ema_50_period(M30_data)
    if signal["type"] == "BULLISH":
        if close_price <= ema50_M30:
            reject(reason="EMA filter not met")
    else:
        if close_price >= ema50_M30:
            reject(reason="EMA filter not met")

if RULE_VOLUME:
    vol_avg_10 = average(last_10_candles_volume)
    if current_volume < vol_avg_10 * 1.5:
        reject(reason="Volume filter not met")

if RULE_PO3:
    asian_range = get_asia_session_range()
    post_asia = get_post_asia_bars()
    if not sweep_detected_in(post_asia, asian_range):
        reject(reason="No Asian sweep detected")

if timestamp_difference > 5_minutes:
    reject(reason="Signal stale")
```

**Step 3: Profitability Check**

```python
profit_distance = abs(signal["tp"] - signal["entry"])
profit_pips = profit_distance / pip_size

if profit_pips < MIN_PROFIT_PIPS:
    reject(reason=f"Profit {profit_pips}p < min {MIN_PROFIT_PIPS}p")
```

**Step 4: Trading Conditions Check**

```python
# Check revenge cooldown
if last_trade_was_loss and now < last_loss_time + 24_hours:
    reject(reason="Revenge trading cooldown active")

# Check max exposure
current_risk = sum(risk_amount for all_open_positions)
max_risk = account_equity * MAX_EXPOSURE_PERCENT
if current_risk >= max_risk:
    reject(reason="Max exposure limit reached")

# Check kill switches
if global_kill_switch == ON:
    reject(reason="Global kill switch enabled")
if symbol_kill_switch[signal.symbol] == ON:
    reject(reason="Symbol kill switch enabled")
```

**Step 5: Dynamic Volume Calculation**

```python
risk_amount = account_equity * RISK_PERCENT / 100
stop_pips = abs(signal["entry"] - signal["sl"]) / pip_size
pip_value = symbol_tick_value  # e.g., 10 for EURUSD 1 lot

volume = risk_amount / (stop_pips * pip_value)

# Respect broker constraints
volume = round(volume / lot_step) * lot_step
volume = max(min_lot, min(max_lot, volume))
```

**Example Calculation:**
```
Account equity: $10,000
Risk percent: 1%
Risk amount: $10,000 × 0.01 = $100

Entry: 1.0850
SL: 1.0820
Stop pips: (1.0850 - 1.0820) / 0.0001 = 300 pips
Pip value (EURUSD, 1 lot): $10

Volume = $100 / (30 pips × $10) = $100 / $300 = 0.33 lots

Result: Trade with 0.33 lots, risking exactly $100
```

**Step 6: Order Placement on MT5**

```python
if signal["type"] == "BULLISH":
    order = MT5.buy_market_order(
        symbol=signal.symbol,
        volume=volume,
        price=signal["entry"],
        sl=signal["sl"],
        tp=signal["tp"]
    )
else:
    order = MT5.sell_market_order(
        symbol=signal.symbol,
        volume=volume,
        price=signal["entry"],
        sl=signal["sl"],
        tp=signal["tp"]
    )

# Log trade
trade_log.append({
    "timestamp": now,
    "symbol": signal.symbol,
    "action": "BUY" or "SELL",
    "volume": volume,
    "entry": signal.entry,
    "sl": signal.sl,
    "tp": signal.tp,
    "risk_amount": risk_amount,
    "r_multiple": reward / risk
})

# Dashboard updates
active_trades[signal.symbol] = trade_details
```

**Step 7: Position Management (Running)**

```python
# Check for closed trades
for each closed_position:
    profit = closed_position.profit
    duration = closed_position.close_time - closed_position.open_time
    
    if profit < 0:  # Losing trade
        cooldown_end = now + 24_hours
        log("Revenge trading cooldown started")
    
    # Add to statistics
    trade_log.append({...closed_position...})

# Apply trailing stop (every loop)
for each open_position:
    if open_position.profit >= 15_pips:
        new_sl = current_price - 15_pips (for long)
        if new_sl > current_sl:
            MT5.modify_position(open_position, new_sl=new_sl)
```

**Step 8: Dashboard Refresh**

```python
dashboard_data = {
    "equity": MT5.account_info().equity,
    "balance": MT5.account_info().balance,
    "daily_profit": current_equity - start_equity,
    "open_positions": count(active positions),
    "signal_count": len(recent_signals),
    "favorable_signals": count(signals that passed all rules),
    "statistics": {
        "win_rate": wins / total_trades,
        "avg_win": sum_wins / wins,
        "avg_loss": sum_losses / losses,
        "expectancy": (win_rate * avg_win) - (loss_rate * abs(avg_loss))
    }
}
```

---

### Dashboard Sections Detailed Explanation

#### Section 1: Status Panel (Top)

Already covered above - shows connection, account, and daily metrics.

#### Section 2: Recent Signals Table

**Shows:** Last 20 FVGs detected (passed or failed)

| Column | Shows | What to Look For |
|--------|-------|-----------------|
| **Symbol** | Trading pair | EURUSD, GBPUSD, etc. |
| **Nature** | Pattern type | Pullback, Retest, or Breakout |
| **Entry** | Entry price | Where trade would execute |
| **Est. Time** | Minutes to TP | Based on average bar range |
| **Status** | ready/blocked/killed | ready = passed all checks |
| **Context** | Gap size | Size of imbalance in pips |

**Tip:** Click any row to see detailed modal with:
- Entry, SL, TP calculations
- Full rule validation breakdown
- Expected R-multiple
- Probability estimation

#### Section 3: Favorable Signals

**Shows:** Only signals that passed ALL validation rules and are ready for execution

**Highest Quality Signals** - focus on these for analysis

#### Section 4: Positions Table

**Shows:** Currently open trades

| Column | Information |
|--------|-------------|
| **Symbol** | Pair |
| **Type** | BUY or SELL |
| **Volume** | Lot size |
| **Entry** | Entry price |
| **Current** | Current market price |
| **P&L** | Profit/loss in dollars |
| **SL** | Current stop loss |
| **TP** | Take profit target |

**Real-time Updates:** Refreshes every 2 seconds

#### Section 5: Statistics Panel

**Performance Metrics:**

| Metric | Calculation | Healthy Value |
|--------|-----------|---------------|
| **Win Rate** | Wins / Total Trades | >55% |
| **Average Win** | Sum of profits / win count | Positive |
| **Average Loss** | Sum of losses / loss count | As small as possible |
| **Expectancy** | (Win% × AvgWin) - (Loss% × AvgLoss) | Positive |
| **Avg R-Multiple** | Average reward/risk ratio | >1.5 |

---

---

## Step-by-Step Trading Workflow

### Phase 1: Launch

```
1. Start bot: run.bat (or python app.py)
   
2. Wait for connection:
   - MetaTrader5 bridge initializes
   - Symbol data loads
   - Dashboard ready
   
3. Open dashboard: http://localhost:5000
   
4. Verify status:
   ✅ "Connected: Yes"
   ✅ Account equity displays
```

---

### Phase 2: Configure (First Time Only)

```
1. Go to Settings tab
   
2. Enter Trading Symbols:
   - EURUSD, GBPUSD, USDJPY (conservative)
   - Add more pairs to increase signal frequency
   
3. Set Risk %:
   - 1% (recommended for beginners)
   - 0.5% (very conservative)
   - 2% (experienced traders)
   
4. Set Max Exposure %:
   - 5% (recommended)
   - Prevents over-leverage
   
5. Set Min Profit Pips:
   - 50 (default, most reliable)
   - 30 (more signals, lower quality)
   - 75 (highest quality, fewer signals)
   
6. Click "Save Settings"
   
7. Verify in Logic Feed: Settings updated
```

---

### Phase 3: Start Trading

```
1. Dashboard tab → Click "START" button
   
2. Confirm in modal:
   - Rules you want enabled
   - Risk settings
   - Symbols confirmed
   
3. Wait for confirmation:
   ✅ "Bot started!"
   ✅ Status changes to "Online"
   
4. Scan begins:
   - Bot now scans all symbols every 5 seconds
   - Logic Feed shows progress
```

---

### Phase 4: Monitor (Ongoing)

```
✅ EVERY 2 SECONDS:
   - Update account stats
   - Refresh signal table
   - Check for new trades
   
📊 ACTIVELY WATCH:
   - Logic Feed for rejections
   - Recent Signals for quality
   - Positions for P&L
   
⚠️ IF NEEDED:
   - Disable underperforming symbol
   - Pause with Global Kill Switch
   - Adjust rules from Rule Panel
```

---

### Phase 5: Analyze & Optimize

```
DAILY:
   - Review closed trades in Logs
   - Check Win Rate & Expectancy
   - Note rejection patterns
   
WEEKLY:
   - Export all logs
   - Calculate total expectancy
   - Compare settings performance
   
MONTHLY:
   - Analyze which symbols performed best
   - Which rules helped most
   - Adjust MIN_PROFIT_PIPS if needed
```

---

### Phase 6: Stop Trading

```
1. Click "STOP" button

2. Wait for confirmation:
   ✅ "Bot stopped"
   ✅ No new signals accepted
   
3. All open trades close:
   - At market or TP/SL as set
   - Final P&L recorded in logs
   
4. Dashboard goes to idle state
```

---

## Advanced Settings

### Environment Variables (in `.env`)

#### Performance Tuning

```
# Scan interval (seconds) - Lower = more CPU but faster signals
SCAN_INTERVAL=5

# Bar history for analysis
HISTORY_BARS=100

# Cache timeout (seconds)
CACHE_TIMEOUT=60
```

#### Notification (Not yet implemented)

```
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx
# Sends alerts for every trade

DISCORD_WEBHOOK=xxx
# Posts to Discord channel
```

#### Logging

```
LOG_LEVEL=INFO
# INFO = standard, DEBUG = verbose, WARNING = errors only
```

---

### API Endpoints (For Advanced Users)

Access bot data programmatically:

```
GET  /api/bot/status
GET  /api/positions
GET  /api/signals
GET  /api/logs
GET  /api/stats
GET  /api/config

POST /api/bot/start
POST /api/bot/stop
POST /api/bot/rules
POST /api/config
```

Example:
```bash
curl http://localhost:5000/api/bot/status
```

---

## Monitoring & Performance

### Key Performance Indicators (KPIs)

**Profitability:**
- Total P&L (should be positive)
- Win Rate (target: 55%+)
- Expectancy (should be positive)

**Risk:**
- Max Drawdown (typically <10%)
- Average Loss per trade
- Maximum loss streak

**Efficiency:**
- Winning trades %
- Average R multiple
- Recovery factor

### Healthy Metrics Example

```
After 30 trades:
✅ Win Rate: 60%
✅ Avg Win: $150
✅ Avg Loss: $100
✅ Expectancy: $40/trade (+2.7% per trade avg)
✅ Max Drawdown: 5%
```

### Warning Signs

```
⚠️ Win Rate <40% → Rules too lenient
⚠️ Expectancy negative → Losing money
⚠️ Drawdown >15% → System broken or bad luck
⚠️ No signals in hours → Symbol or internet issue
```

---

## Troubleshooting

### Problem: "Bot not running"

**Diagnosis:**
1. Check terminal for error message
2. Verify Python installed: `python --version`
3. Check Flask is running on port 5000

**Solution:**
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Try manual start
python app.py
```

---

### Problem: "No signals generated"

**Diagnosis:**
1. Check Logic Feed for rejection reasons
2. Verify symbols are correct
3. Check rules are toggled ON

**Solutions:**
```
❌ "Stale data" → MT5 not running properly
✅ Solution: Restart MetaTrader5

❌ "EMA filter not met" → Price not aligned with trend
✅ Solution: Disable RULE_EMA or wait for better trend

❌ "TP too close" → Not enough profit potential
✅ Solution: Lower MIN_PROFIT_PIPS to 30
```

---

### Problem: "Connection failed to MT5"

**Diagnosis:**
1. MetaTrader5 not running?
2. Account logged out?
3. Wrong credentials?

**Solution:**
```
1. Open MetaTrader5
2. Log in with your account
3. Keep it running (don't minimize)
4. Restart bot with: python app.py
```

---

### Problem: "All trades are losing money"

**Diagnosis:**
1. Rules too loose → Entering low-quality signals
2. Market conditions changed
3. Symbol pair is sideways (choppy)

**Solution:**
```
1. Increase MIN_PROFIT_PIPS to 75
2. Enable all rules (EMA, Volume, PO3)
3. Decrease trading symbols (focus on EURUSD)
4. Stop trading and analyze logs
```

---

### Problem: "Dashboard not accessible"

**Diagnosis:**
1. Flask crashed?
2. Port 5000 in use?
3. Firewall blocking?

**Solution:**
```bash
# Check if Flask running
netstat -an | findstr 5000

# Kill process on port 5000 and restart
taskkill /PID <process_id> /F

# Or change port in app.py:
# app.run(port=8080)
```

---

### Problem: "Trades not closing"

**Diagnosis:**
1. TP/SL levels not set correctly?
2. Symbol disconnected?
3. MT5 issue?

**Solution:**
1. Check Positions table → est. Take Profit value
2. Verify SL > 0 and TP differs from Entry
3. Manually close in MT5 if needed
4. Check with broker if there's an issue

---

## FAQ

### Q1: Can I trade multiple pairs at once?

**A:** Yes! Add them to `TRADING_SYMBOLS`:
```
TRADING_SYMBOLS=EURUSD,GBPUSD,USDJPY,AUDUSD
```

Bot will manage positions across ALL independently with risk limits.

---

### Q2: What's the minimum account size?

**A:** Depends on broker's minimum lot size:
- Most brokers: 0.01 lot = $100 margin for EURUSD
- $1,000 account → Can trade comfortably
- $500 account → Possible but tight

Recommended: **$5,000+ for safety**

---

### Q3: Can I leave the bot running 24/7?

**A:** Almost! 

**Keep running:**
- Monday 00:00 UTC → Friday 21:00 UTC (forex market hours)
- EURUSD: Most liquid then

**Safe to pause:**
- Friday evening - Sunday evening (low liquidity)
- During major economic news (high volatility)
- Weekends (market closed)

---

### Q4: What's the best time to trade?

**A:** Overlapping sessions have highest volume:

```
🌍 Asia-London: 08:00-09:00 UTC
🌍 London-NY: 13:00-17:00 UTC (BEST)
🌍 NY only: 13:00-21:00 UTC
```

Bot handles this automatically, but more signals during these times.

---

### Q5: Should I risk 1% or 2% per trade?

**A:** 

**Conservative (Safe):**
- 0.5% per trade
- Survive 20 losses in a row
- Slow but steady growth

**Moderate (Recommended):**
- 1% per trade (default)
- Survive 10 losses in a row
- Balanced growth

**Aggressive (Experienced)**
- 2% per trade
- Survive 5 losses in a row
- Fast growth, high volatility

Start with 1%, adjust based on comfort level.

---

### Q6: Can I manually trade while bot is running?

**A:** **YES**, but be careful:

✅ Safe:
- Manual trades on different symbols
- Manual trades outside bot's symbols

❌ Risky:
- Manual trades on same symbol as bot
- Bot might enter another trade, creating conflicts
- Better to disable that symbol in bot

---

### Q7: What if I lose money?

**A:** This is **NORMAL** for any trading system.

**Healthy scenario:**
- Win ~60% of trades
- But average win > average loss
- Positive expectancy over time
- Some losing days/weeks

**Unhealthy signs:**
- Lose >5% account in one day
- Expectancy is negative
- Win rate <40%

If unhealthy: **STOP trading**, analyze logs, adjust rules.

---

### Q8: How do I backup my logs?

**A:** Logs saved automatically in `/logs` folder:

```
logs/
  ├── trades_2026-03-19.json
  ├── trades_2026-03-18.json
  └── trades_2026-03-17.json
```

Backup this folder regularly to analyze performance.

---

### Q9: Can I modify the code?

**A:** Yes! Advanced users can:

✅ Edit `engine.py` → Change trading logic
✅ Edit `technical_analysis.py` → Modify FVG detection
✅ Edit `bible_logic.py` → Change validation rules
✅ Edit `app.py` → Add new API endpoints

⚠️ Always backup original before modifying!

---

### Q10: What's the difference between "Favorable" and "Recent" signals?

**A:**

**Recent Signals:**
- All FVG signals detected
- May not pass all validations
- Lower quality

**Favorable Signals:**
- Only passed all rule checks
- Big enough profit target (>50 pips)
- Ready-to-execute quality
- Shows execution status (ready/blocked/active)

Use "Favorable" for actual trading decisions.

---

## Conclusion

The **Nexus Trading Bot** is a powerful tool for automated FVG trading with strict risk management.

**Remember:**
1. ✅ Never risk more than you can afford to lose
2. ✅ Start with small position sizes
3. ✅ Monitor the bot regularly
4. ✅ Let the rules work (don't interfere)
5. ✅ Adjust based on data, not emotion

**For support:**
- Check Troubleshooting section
- Review bot logs in dashboard
- Analyze trade history

**Happy trading! 🚀**

---

**Last Updated:** March 2026  
**Version:** 1.0  
**Status:** Production Ready
