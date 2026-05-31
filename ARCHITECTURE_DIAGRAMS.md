# Architecture & Flow Diagrams

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       NEXUS TRADING BOT                          │
│                    Advanced Features Edition                      │
└──────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────┐
│                     TradingEngine (engine.py)                       │
│  Core orchestrator - runs main 5-second loop                       │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ┌─────────────────────┐  ┌──────────────────┐  ┌─────────────┐  │
│  │  scan_and_trade()   │  │ _manage_pending_ │  │ check_      │  │
│  │                     │  │ orders()         │  │ positions() │  │
│  │ (Existing - M5)     │  │ (NEW)            │  │ (Existing)  │  │
│  │                     │  │                  │  │             │  │
│  │ • FVG detection     │  │ Places LIMIT     │  │ • Monitors  │  │
│  │ • Signal validation │  │ orders on M30    │  │   active    │  │
│  │ • Market entries    │  │ • High prob zones│  │   trades    │  │
│  │                     │  │ • Automated      │  │ • Trailing  │  │
│  └─────────────────────┘  │   placement      │  │   stops     │  │
│                            └──────────────────┘  └─────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────┐         │
│  │ _manage_conditional_watchlist()                      │ (NEW)   │
│  │                                                       │         │
│  │ Multi-phase monitoring and execution                 │         │
│  │ • Phase 1: Asian High Sweep                          │         │
│  │ • Phase 2: Change of Character (mBOS)               │         │
│  │ • Phase 3: Return to Extreme FVG                     │         │
│  └──────────────────────────────────────────────────────┘         │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
         ↓                    ↓                         ↓
    ┌────────────┐  ┌──────────────────┐  ┌────────────────┐
    │   MT5      │  │ PendingOrder     │  │ Conditional    │
    │ Interface  │  │ Manager          │  │ Watchlist      │
    │            │  │ (NEW)            │  │ Manager (NEW)  │
    │ • Connect  │  │                  │  │                │
    │ • Orders   │  │ • High prob      │  │ • Phase 1      │
    │ • Positions│  │   zones          │  │ • Phase 2      │
    │ • Limit    │  │ • Place LIMIT    │  │ • Phase 3      │
    │   orders   │  │   orders         │  │ • Orchestrate  │
    │ • Position │  │ • Monitor        │  │   execution    │
    │   mgmt     │  │ • Cancel         │  │                │
    └────────────┘  └──────────────────┘  └────────────────┘
         ↓                    ↓                         ↓
    ┌────────────────────────────────────────────────────┐
    │         MetaTrader 5 (Broker)                      │
    │                                                    │
    │  • Execute DEAL (Market) orders                    │
    │  • Execute BUY_LIMIT orders                        │
    │  • Execute SELL_LIMIT orders                       │
    │  • Update positions in real-time                   │
    │  • Manage SL/TP automatically                      │
    └────────────────────────────────────────────────────┘
```

---

## Feature 1: Set & Forget - Execution Flow

```
PENDING ORDER MANAGER - Set & Forget
=====================================

┌─────────────────────────────────────────────────────────┐
│ START: identify_high_probability_zones()                │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ FETCH: Last 10 M30 bars for symbol                      │
│ DATA: High, Low, Close for each bar                     │
└─────────────────────────────────────────────────────────┘
              ↓┌──────────────────────────────────────────┐
              │ DETECT: BULLISH FVG                      │
              │ Condition: Low[2 bars] > High[1 bar]     │
              │                                          │
              │ Entry: High of 1-bar candle              │
              │ SL: Low of sweep candle                  │
              │ TP: Entry + (Gap Size × 3)               │
              └──────────────────────────────────────────┘
              ├──────────────────────────────────────────┐
              │ DETECT: BEARISH FVG                      │
              │ Condition: High[2 bars] < Low[1 bar]     │
              │                                          │
              │ Entry: Low of 1-bar candle               │
              │ SL: High of sweep candle                 │
              │ TP: Entry - (Gap Size × 3)               │
              └──────────────────────────────────────────┘
              ↓
         [Multiple Zones Found]
              ↓
┌─────────────────────────────────────────────────────────┐
│ SCORE: Probability for each zone                        │
│                                                         │
│ Probability = 0.6 × RR_Score + 0.4 × EMA_Score        │
│                                                         │
│ RR_Score: Risk-Reward ratio (ideal: 1:3)               │
│ EMA_Score: Price alignment to 50-EMA on M30            │
│                                                         │
│ Select: HIGHEST probability zone                        │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ FORMAT: Order request for MT5                           │
│                                                         │
│ Order Type:  BUY_LIMIT or SELL_LIMIT                    │
│ Action:      TRADE_ACTION_PENDING                       │
│ Symbol:      EURUSD (etc)                               │
│ Volume:      Calculate based on risk%                   │
│ Price:       FVG Entry level                            │
│ SL:          Sweep level (calculated)                   │
│ TP:          Entry ± (Gap × 3)                          │
│ Comment:     PENDING_BUY_LIMIT_FVG                      │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ PLACE: Pending order on MT5                             │
│                                                         │
│ Response: Order ticket (e.g., 123456)                   │
│ Status: PENDING                                         │
│ Bot: Can now stop running                               │
└─────────────────────────────────────────────────────────┘
              ↓
      [Bot Stops Running]
              ↓
┌─────────────────────────────────────────────────────────┐
│ MONITOR: MT5 executes order                             │
│                                                         │
│ When price hits entry level → ORDER FILLED              │
│                                                         │
│ Trade is now OPEN with SL & TP already set              │
│ MT5 manages the exit automatically                      │
└─────────────────────────────────────────────────────────┘
              ↓
         [Trade Complete]
```

---

## Feature 2: Smart Watchlist - Phase Progression

```
CONDITIONAL WATCHLIST MANAGER - Smart Execution
================================================

┌─────────────────────────────────────────────────────────┐
│ PHASE 1: Asian High Sweep                               │
│ ────────────────────────────────────────────────────────│
│                                                         │
│ • Scan last 12 hours of M30 data                        │
│ • Find highest HIGH during 00:00-08:00 UTC              │
│ • Store: asian_high, asian_low                          │
│                                                         │
│ TRIGGER: Current price > Asian High                     │
│                                                         │
│ ┌──────────────────────────────────────────────────┐   │
│ │ Example:                                          │   │
│ │ Asian High: 1.0900                               │   │
│ │ Current: 1.0895 → Still waiting                  │   │
│ │ Current: 1.0905 → SWEEP DETECTED ✓ → Phase 2    │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ Time: Immediate (once per day per symbol)              │
└─────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 2: Change of Character (mBOS)                      │
│ ────────────────────────────────────────────────────────│
│                                                         │
│ • Analyze market structure after sweep                  │
│ • Look for: Lower High (bearish) or Higher Low (bullish)│
│ • Confirm: Significant structure shift                  │
│                                                         │
│ TRIGGER: Price reversal detected                        │
│                                                         │
│ ┌──────────────────────────────────────────────────┐   │
│ │ Example:                                          │   │
│ │ Prev High: 1.0905                                │   │
│ │ Current High: 1.0897 → Lower High detected ✓     │   │
│ │ mBOS_level: 1.0897 → STRUCTURE CONFIRMED ✓       │   │
│ │                    → Phase 3                      │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ Time: 1-4 hours after Phase 1                           │
└─────────────────────────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────────────────┐
│ PHASE 3: Return to Extreme FVG                          │
│ ────────────────────────────────────────────────────────│
│                                                         │
│ • Identify freshest FVG after mBOS                      │
│ • Most powerful, recently formed gap                    │
│ • Calculate: FVG zone (±50% of gap size)                │
│                                                         │
│ TRIGGER: Price reenters FVG zone                        │
│                                                         │
│ ┌──────────────────────────────────────────────────┐   │
│ │ Example:                                          │   │
│ │ Extreme FVG: Entry 1.0878                         │   │
│ │ Current: 1.0890 → Waiting for return             │   │
│ │ Current: 1.0879 → IN FVG ZONE ✓ → EXECUTE ✓      │   │
│ │                                                   │   │
│ │ TRADE PLACED:                                     │   │
│ │ Type: SELL_LIMIT at 1.0878                        │   │
│ │ SL: 1.0908 (above sweep high)                     │   │
│ │ TP: 1.0768 (1:3 ratio)                            │   │
│ │ Volume: Calculate based on risk%                  │   │
│ └──────────────────────────────────────────────────┘   │
│                                                         │
│ Time: 2-6 hours after Phase 2                           │
└─────────────────────────────────────────────────────────┘
         ↓
   [Trade Complete]
         ↓
   [Symbol Reset to Phase 1]
```

---

## Feature 3: Future Trade Manager - Risk Calculation

```
RISK MANAGEMENT - Pre-Calculated SL/TP
=======================================

INPUT: FVG Signal
  Symbol: EURUSD
  Type: BULLISH
  Entry: 1.0850
  Sweep Low: 1.0820
  Current Equity: $10,000
  Risk %: 1%

┌─────────────────────────────────────────────────────────┐
│ STEP 1: Calculate Risk Amount                           │
│                                                         │
│ Risk Amount = Equity × Risk %                           │
│ Risk Amount = $10,000 × 1% = $100                       │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 2: Determine Stop Loss (Already Identified)        │
│                                                         │
│ For BUY:  SL = Sweep Candle Low = 1.0820               │
│ For SELL: SL = Sweep Candle High                        │
│                                                         │
│ SL = 1.0820                                             │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 3: Calculate Stop Distance in Pips                 │
│                                                         │
│ Pip Size = 0.0001 (for 5-digit symbols)                 │
│ Stop Pips = (Entry - SL) / Pip Size                     │
│ Stop Pips = (1.0850 - 1.0820) / 0.0001                 │
│ Stop Pips = 0.0030 / 0.0001 = 30 pips                   │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 4: Calculate Pip Value Per Lot                     │
│                                                         │
│ 1 pip = $10 per 1.0 lot (EURUSD standard)               │
│ Total Risk $100 / ($10 per pip) = 10 pips × 1.0 lot     │
│ Hmm, need to recalculate for volume...                   │
│                                                         │
│ Alternative: Risk Amount / (Stop Pips × Pip Value)      │
│ = $100 / (30 pips × $10 per pip per lot)                │
│ = $100 / $300 per lot                                   │
│ = 0.33 lots = 0.33 standard lots                        │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 5: Round to Valid Lot Size                         │
│                                                         │
│ Min Lot: 0.01                                           │
│ Max Lot: 100                                            │
│ Step: 0.01                                              │
│                                                         │
│ Volume = 0.33 → Round down to 0.33 lots                 │
│                                                         │
│ Final Volume: 0.33 lots (actual risk: ~$100)            │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│ STEP 6: Calculate Take Profit (1:3 Risk-Reward)         │
│                                                         │
│ Risk Distance = abs(Entry - SL)                         │
│ Risk Distance = abs(1.0850 - 1.0820) = 0.0030           │
│                                                         │
│ For BUY:  TP = Entry + (Risk Distance × 3)              │
│ TP = 1.0850 + (0.0030 × 3)                              │
│ TP = 1.0850 + 0.0090                                    │
│ TP = 1.0940                                             │
│                                                         │
│ Risk: 30 pips ($100)                                    │
│ Reward: 90 pips ($300)                                  │
│ R:R Ratio: 1:3 ✓ IDEAL                                  │
└─────────────────────────────────────────────────────────┘
              ↓
OUTPUT: Order to Place
  ├─ Symbol: EURUSD
  ├─ Type: BUY_LIMIT
  ├─ Entry: 1.0850
  ├─ SL: 1.0820 (30 pips risk)
  ├─ TP: 1.0940 (90 pips reward)
  ├─ Volume: 0.33 lots
  ├─ Risk Amount: ~$100
  ├─ Reward Amount: ~$300
  └─ R:R Ratio: 1:3 ✓
```

---

## Integration - All Features Together

```
MAIN TRADING LOOP (5-second cycle)
==================================

START
  ↓
┌─────────────────────────────┐
│ scan_and_trade()            │  (Existing)
│                             │
│ M5 Timeframe Scanning       │
│ • Detect FVG signals        │
│ • Validate vs rules         │
│ • Execute market orders     │
└─────────────────────────────┘
  ↓
┌─────────────────────────────┐
│ _manage_pending_orders()    │  (NEW)
│                             │
│ M30 Timeframe Setup         │
│ • Identify high-prob zones  │
│ • Place LIMIT orders        │
│ • Monitor existing orders   │
└─────────────────────────────┘
  ↓
┌─────────────────────────────┐
│ _manage_conditional_        │  (NEW)
│ watchlist()                 │
│                             │
│ Multi-Phase Monitoring      │
│ • Phase 1: Asian sweep      │
│ • Phase 2: Structure break  │
│ • Phase 3: FVG return       │
│ • Execute when ready        │
└─────────────────────────────┘
  ↓
┌─────────────────────────────┐
│ check_positions()           │  (Existing)
│                             │
│ Trade Management            │
│ • Monitor P&L               │
│ • Trailing stops            │
│ • Close on targets          │
└─────────────────────────────┘
  ↓
Sleep 5 seconds
  ↓
REPEAT
```

---

## API Flow - Dashboard to Broker

```
USER DASHBOARD
http://localhost:5000
  ↓
  ├─→ GET /api/pending-orders
  │    ↓
  │    [Flask App Handler]
  │    ↓
  │    [TradingEngine.pending_order_manager]
  │    ↓
  │    Returns: Pending order list
  │    ↓
  │    [Dashboard Display]
  │
  ├─→ POST /api/watchlist/initialize
  │    ↓
  │    [Flask App Handler]
  │    ↓
  │    [TradingEngine.conditional_watchlist_manager]
  │    ↓
  │    Initialize phase monitoring
  │    ↓
  │    Returns: Watchlist status
  │    ↓
  │    [Dashboard Display]
  │
  └─→ POST /api/features
       ↓
       [Flask App Handler]
       ↓
       [TradingEngine.features dict]
       ↓
       Toggle features on/off
       ↓
       Returns: Updated status
       ↓
       [Dashboard Display]
```

---

## Data Flow - Zone Detection to Trade Execution

```
M30 PRICE DATA (from MT5)
  └─ 10 bars: High, Low, Close
       ↓
  ┌────────────────────┐
  │ FVG Detection      │
  └────────────────────┘
       ↓
  ┌────────────────────┐
  │ Identify Gaps:     │
  │ • Bullish FVG      │
  │ • Bearish FVG      │
  └────────────────────┘
       ↓
  ┌────────────────────────────┐
  │ Calculate Entry/SL/TP:     │
  │ • Entry: Gap edge          │
  │ • SL: Sweep level          │
  │ • TP: Entry ± (Gap × 3)    │
  └────────────────────────────┘
       ↓
  ┌────────────────────────────┐
  │ Score Probability:         │
  │ • R:R Ratio (60% weight)   │
  │ • EMA Alignment (40%)       │
  │ • Result: 0.0-1.0 score    │
  └────────────────────────────┘
       ↓
  ┌────────────────────┐
  │ Select Best Zone   │
  │ (Highest score)    │
  └────────────────────┘
       ↓
  ┌────────────────────────────────┐
  │ Calculate Volume:              │
  │ • Risk Amount = Equity × Risk% │
  │ • Size = Risk / Stop Distance   │
  └────────────────────────────────┘
       ↓
  ┌────────────────────┐
  │ Format Order:      │
  │ • Type: LIMIT      │
  │ • Action: PENDING  │
  │ • Price: Entry     │
  │ • SL: Calculated   │
  │ • TP: Calculated   │
  └────────────────────┘
       ↓
  ┌────────────────────┐
  │ Send to MT5        │
  │ order_send()       │
  └────────────────────┘
       ↓
  ┌─────────────────────┐
  │ MT5 Response:       │
  │ • Ticket: 123456    │
  │ • Status: PENDING   │
  └─────────────────────┘
       ↓
  [Pending Order Placed]
       ↓
  [Price hits entry → MT5 Executes]
       ↓
  [Trade Open with SL & TP set]
       ↓
  [Trade Complete]
```

---

## State Diagram - Watchlist Symbol Lifecycle

```
WATCHLIST SYMBOL LIFECYCLE
==========================

    ┌───────────────────┐
    │   NOT_WATCHED     │
    └─────────┬─────────┘
              │ initialize_watchlist()
              ↓
    ┌───────────────────────────┐
    │  PHASE 1: ASIAN_SWEEP     │
    │  Status: Waiting          │
    │  • Monitor asian high     │
    └─────────┬─────────────────┘
              │ phase1_detect_asian_sweep()
              │ → sweep_detected = True
              ↓
    ┌───────────────────────────┐
    │  PHASE 2: MBOS_DETECTION  │
    │  Status: Structure break  │
    │  • Monitor mBOS           │
    └─────────┬─────────────────┘
              │ phase2_detect_mbos()
              │ → mBOS_detected = True
              ↓
    ┌───────────────────────────┐
    │  PHASE 3: FVG_RETURN      │
    │  Status: Ready            │
    │  • Monitor FVG zone       │
    └─────────┬─────────────────┘
              │ phase3_detect_extreme_fvg_return()
              │ → ready_for_execution = True
              ↓
    ┌───────────────────────────┐
    │  place_conditional_order()│
    │  Status: Executing        │
    │  • Place limit order      │
    │  • Order ticket created   │
    └─────────┬─────────────────┘
              │
              ├─→ ┌─────────────────────────┐
              │   │ TRADE_COMPLETE          │
              │   │ • Order filled          │
              │   │ • Trade now open        │
              │   │ • SL & TP managed by MT5│
              │   └─────────────────────────┘
              │         │
              │         ↓
              │   [Wait for exit]
              │         │
              │         ↓ check_positions()
              │   ┌─────────────────────────┐
              │   │ TRADE_CLOSED            │
              │   │ • Reached TP            │
              │   │ • Hit SL                │
              │   │ • Manual close          │
              │   └────────┬────────────────┘
              │            │
              └────────────┴─────→ reset_symbol()
                                      │
                                      ↓
                            ┌─────────────────────┐
                            │ BACK TO PHASE 1     │
                            │ Ready for new cycle │
                            └─────────────────────┘
```

---

## Summary: Feature Interaction

```
┌─────────────────────────────────────────────────────────┐
│          NEXUS ADVANCED TRADING SYSTEM                  │
│                                                         │
│  Three Complementary Strategies Running Together        │
└─────────────────────────────────────────────────────────┘

STRATEGY 1: SET & FORGET (Passive)
   └─ Continuous zone scanning
   └─ Automatic order placement
   └─ Works 24/7, even offline
   └─ Ideal for overnight setups

STRATEGY 2: SMART WATCHLIST (Semi-Active)
   └─ Multi-phase monitoring
   └─ Precision entry execution
   └─ Requires market structure
   └─ Ideal for high-conviction trades

STRATEGY 3: EXISTING BOT (Active)
   └─ M5 timeframe scanning
   └─ Real-time entries
   └─ Immediate response to signals
   └─ Ideal for trend following

COMBINED RESULT: Diversified, multi-timeframe,
                 always-active trading system
```

---

These diagrams show the complete architecture, data flows, and feature interactions of the advanced trading system. Use them as reference when understanding how all components work together!
