# Advanced Trading Features - Implementation Guide

This document describes the three advanced trading features now available in Nexus Trading Bot:
1. **Set and Forget** (Pending Orders)
2. **Smart Watchlist** (Conditional Execution)
3. **Future Trade Manager**

---

## 1. Set and Forget - Pending Orders

### Overview
Instead of the bot waiting for price to hit a zone while running, the bot calculates Trade Bible zones and immediately places **Limit Orders** on MT5. Once placed, the broker handles the order execution even when the bot is offline.

### How It Works

#### Process Flow
1. **Bot scans** M30 chart for fresh FVGs (Fair Value Gaps)
2. **Identifies zones** with high probability (calculated via Risk-Reward ratio and EMA alignment)
3. **Places BUY_LIMIT or SELL_LIMIT** orders at the calculated entry price
4. **Includes pre-calculated** Stop Loss (below the sweep) and 1:3 Take Profit
5. **Broker executes** when price hits the entry level

#### Key Components

**PendingOrderManager** (`pending_order_manager.py`)
- `identify_high_probability_zones()` - Scans M30 for FVG zones
- `place_pending_order()` - Places BUY_LIMIT or SELL_LIMIT on MT5
- `scan_and_place_pending_orders()` - Automated zone scanning and order placement
- `monitor_pending_orders()` - Tracks status of pending orders
- `cancel_pending_order()` - Cancels a pending order

### Configuration

Enable/disable via environment variable:
```env
FEATURE_PENDING_ORDERS=true
```

Or control via API:
```bash
POST /api/features
{
  "pending_orders": true
}
```

### Usage

#### Automatic (runs automatically in the bot loop)
The bot will periodically scan symbols and place pending orders:
- Scans every 5 seconds (configurable)
- Only places one order per symbol
- Respects kill switches and risk limits

#### Manual Trigger
```bash
POST /api/pending-orders/place
{
  "symbols": "EURUSD,GBPUSD,USDJPY"
}
```

#### Monitor Pending Orders
```bash
GET /api/pending-orders
```

Response:
```json
{
  "status": "success",
  "data": [
    {
      "symbol": "EURUSD",
      "ticket": 123456,
      "action": "BUY",
      "entry": 1.0850,
      "sl": 1.0820,
      "tp": 1.0940,
      "volume": 0.15,
      "zone_type": "BULLISH_FVG",
      "placed_at": "2026-03-19T10:30:00"
    }
  ]
}
```

#### Cancel Pending Order
```bash
DELETE /api/pending-orders/EURUSD
```

### Probability Scoring

Each zone receives a probability score (0.0 to 1.0) based on:
- **Risk-Reward Ratio** (60% weight): Ideal at 1:3
- **EMA Alignment** (40% weight): Price above/below 50-EMA on M30

Only the highest probability zone is selected for order placement.

### Stop Loss & Take Profit Rules
- **Stop Loss**: Always below the sweep candle low (for BUY) or above high (for SELL)
- **Take Profit**: Calculated as 1:3 Risk-Reward ratio
  - TP = Entry + (3 × Risk Distance)

---

## 2. Smart Watchlist - Multi-Phase Execution

### Overview
The bot monitors multiple symbols and "primes" them for future trades only when specific conditions are met across three phases. This prevents premature entries and ensures frequency matching.

### Three-Phase Logic

#### Phase 1: Asian High Sweep Detection
**Duration**: Ongoing until sweep detected

```
Condition: Price breaks above the Asian session high (00:00-08:00 UTC)
Action: Detect sweep zone and move to Phase 2
```

- Fetches last 12 hours of M30 data
- Identifies the highest high during Asian session
- Triggers when current price closes above this level

#### Phase 2: Change of Character (mBOS)
**Duration**: Once Phase 1 complete, until mBOS detected

```
Condition: Market breaks the pullback structure (reversal candle)
Action: Identify mBOS level and move to Phase 3
```

- Scans for Lower High (bearish mBOS) or Higher Low (bullish mBOS)
- Detects a significant shift in market structure
- Records the exact mBOS level for reference

#### Phase 3: Return to Extreme FVG
**Duration**: Once Phase 2 complete, until price returns to zone

```
Condition: Price returns to the fresh "Extreme" FVG level
Action: Execute trade immediately
```

- Scans for the freshest, most powerful FVG after mBOS
- Monitors price for return to the FVG level
- Triggers execution when price re-enters the zone

### How It Works

```
Example: EURUSD
├─ Phase 1: Asian sweep detected at 1.0900 ✓
├─ Phase 2: mBOS detected at 1.0885 ✓
└─ Phase 3: Waiting for price return to extreme FVG at 1.0878
   └─ Price returns to zone → EXECUTE BUY_LIMIT at 1.0878
```

### Key Components

**ConditionalWatchlistManager** (`conditional_watchlist_manager.py`)
- `initialize_watchlist()` - Start monitoring symbols at Phase 1
- `phase1_detect_asian_sweep()` - Detect Asian High sweep
- `phase2_detect_mbos()` - Detect Change of Character
- `phase3_detect_extreme_fvg_return()` - Trigger execution
- `process_watchlist()` - Main processing loop (advances phases)
- `place_conditional_order()` - Execute the trade
- `reset_symbol()` - Reset to Phase 1 after trade

### Configuration

Enable/disable via environment variable:
```env
FEATURE_CONDITIONAL_WATCHLIST=true
```

Or control via API:
```bash
POST /api/features
{
  "conditional_watchlist": true
}
```

### Usage

#### Initialize Watchlist
```bash
POST /api/watchlist/initialize
{
  "symbols": "EURUSD,GBPUSD,USDJPY"
}
```

#### Monitor Watchlist Progress
```bash
GET /api/watchlist
```

Response:
```json
{
  "status": "success",
  "data": {
    "watchlist": [
      {
        "symbol": "EURUSD",
        "phase": 2,
        "sweep_detected": true,
        "mBOS_detected": false,
        "ready_for_execution": false,
        "phase1_started": "2026-03-19T10:00:00"
      }
    ],
    "ready_for_execution": [
      {
        "symbol": "GBPUSD",
        "extreme_fvg": {
          "entry": 1.2780,
          "sl": 1.2750,
          "tp": 1.2870
        },
        "mBOS_level": 1.2775,
        "asian_high": 1.2850
      }
    ]
  }
}
```

#### Reset Symbol (back to Phase 1)
```bash
POST /api/watchlist/EURUSD/reset
```

---

## 3. Future Trade Manager

### Overview
Automatically identifies high-probability zones and manages the complete trade lifecycle with pre-calculated risk management.

### Automatic Trade Management

The Future Trade Manager combines both Set and Forget and Smart Watchlist:

1. **Zone Identification**: Scans M30 for FVGs
2. **Probability Scoring**: Ranks zones by R:R ratio and EMA alignment
3. **Order Placement**: Places BUY_LIMIT or SELL_LIMIT automatically
4. **Risk Sizing**: Calculates volume based on account equity and risk %
5. **Stop Loss**: Always using sweep level (below low for BUY, above high for SELL)
6. **Take Profit**: 1:3 Risk-Reward ratio

### Execution Flow

```
Scan M30 Charts
    ↓
Identify All FVGs
    ↓
Score Zones (Probability)
    ↓
Select Best Zone
    ↓
Calculate Volume (Risk %)
    ↓
Place Limit Order with SL & TP
    ↓
Monitor until filled/cancelled
```

### Pre-Calculated Parameters

For each order placed:

**Stop Loss Calculation**
```python
BUY:  SL = Sweep Candle Low
SELL: SL = Sweep Candle High
```

**Take Profit Calculation**
```python
Risk Distance = Entry - SL (absolute value)
TP = Entry + (Risk Distance × 3)  # For BUY
TP = Entry - (Risk Distance × 3)  # For SELL
```

**Volume Calculation**
```python
Risk Amount = Account Equity × Risk %
Stop Pips = abs(Entry - SL) / Pip Size
Volume = Risk Amount / (Stop Pips × Pip Value)
```

### Risk Management Features

1. **Max Exposure**: Prevents multiple trades exceeding max exposure %
2. **Revenge Cooldown**: 24-hour cooldown after losing trade
3. **Kill Switches**: Disable trading per-symbol or globally
4. **Rule Filters**: EMA, Volume, and PO3/Asian sweep filters

---

## Integration with Existing Bot

### TradingEngine Updates

The engine now includes:

```python
# New managers
self.pending_order_manager = PendingOrderManager(self.mt5)
self.conditional_watchlist_manager = ConditionalWatchlistManager(self.mt5)

# Feature toggles
self.features = {
    "pending_orders": True,
    "conditional_watchlist": True,
}

# In start() loop:
- _manage_pending_orders()          # Set and Forget
- _manage_conditional_watchlist()   # Smart Watchlist
```

### MT5Interface Updates

New methods added:

```python
place_buy_limit_order()    # Place BUY_LIMIT order
place_sell_limit_order()   # Place SELL_LIMIT order
get_pending_orders()       # Retrieve all pending orders
cancel_order()             # Cancel pending order
_order_type_to_string()    # Helper for order type conversion
```

---

## Dashboard Integration

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/pending-orders` | GET | Get pending orders summary |
| `/api/pending-orders/place` | POST | Manually place pending orders |
| `/api/pending-orders/<symbol>` | DELETE | Cancel pending order |
| `/api/watchlist` | GET | Get watchlist phase status |
| `/api/watchlist/initialize` | POST | Initialize watchlist |
| `/api/watchlist/<symbol>/reset` | POST | Reset symbol to Phase 1 |
| `/api/features` | GET/POST | Get/set feature toggles |

### Example Dashboard Usage

```javascript
// Turn on pending orders
await fetch('/api/features', {
  method: 'POST',
  body: JSON.stringify({ pending_orders: true })
});

// Get pending orders
const resp = await fetch('/api/pending-orders');
const pending = await resp.json();

// Initialize watchlist
await fetch('/api/watchlist/initialize', {
  method: 'POST',
  body: JSON.stringify({ symbols: ['EURUSD', 'GBPUSD'] })
});

// Monitor watchlist
const watchlist = await fetch('/api/watchlist');
```

---

## Best Practices

### Set and Forget (Pending Orders)
1. ✅ Use with longer timeframes (M30 or higher)
2. ✅ Allow bot to monitor zones before shutdown
3. ✅ Check pending orders after market gaps
4. ✅ Cancel stale orders before weekends
5. ❌ Don't place too many orders (1 per symbol recommended)

### Smart Watchlist
1. ✅ Use for high-conviction setups
2. ✅ Allow time for all phases to complete
3. ✅ Monitor critical support/resistance levels
4. ✅ Use with Asian/London session pairs
5. ❌ Don't rush Phase 2 (market must show structure break)

### Risk Management
1. ✅ Start with 0.5-1% risk per trade
2. ✅ Set max exposure to 5-10% of equity
3. ✅ Use daily loss limits
4. ✅ Monitor floating drawdown
5. ❌ Never exceed 3% risk per single trade

---

## Troubleshooting

### Pending Orders Not Placing
- Check if `FEATURE_PENDING_ORDERS=true`
- Verify symbols exist on MT5
- Check account equity (must be > 0)
- Verify Kill Switch is not active
- Check logs for specific error messages

### Watchlist Not Advancing Phases
- Ensure sufficient M30 historical data
- Check if Asian session high is being detected
- Verify mBOS conditions are being met
- Monitor logs for phase detection events

### Orders Cancelled Unexpectedly
- May be due to insufficient margin
- Check account margin level
- Verify Stop Loss/Take Profit prices are valid
- Review MT5 rejection reasons in logs

---

## Performance Optimization

### For High-Volume Trading
```env
FEATURE_PENDING_ORDERS=true
FEATURE_CONDITIONAL_WATCHLIST=false
MAX_PENDING_ORDERS=5
```

### For Conservative Trading
```env
FEATURE_PENDING_ORDERS=false
FEATURE_CONDITIONAL_WATCHLIST=true
RISK_PERCENT=0.5
```

### For Balanced Approach
```env
FEATURE_PENDING_ORDERS=true
FEATURE_CONDITIONAL_WATCHLIST=true
MAX_PENDING_ORDERS=3
RISK_PERCENT=1.0
```

---

## Example Scenarios

### Scenario 1: EURUSD Set and Forget
```
1. Bot scans at 14:00 UTC
2. Finds Bullish FVG at 1.0850 (probability: 0.85)
3. Places BUY_LIMIT at 1.0850
   - SL: 1.0820 (sweep low)
   - TP: 1.0940 (1:3 ratio)
4. Bot stops running
5. Price hits 1.0850 at 18:30 UTC
6. Broker executes order automatically
7. Trade profit: 90 pips
```

### Scenario 2: GBPUSD Smart Watchlist
```
Phase 1 (07:00 UTC): Asian high at 1.2850
  → Price crosses 1.2850
  → Move to Phase 2
  
Phase 2 (08:30 UTC): mBOS detected
  → Lower high at 1.2800
  → Structure break confirmed
  → Move to Phase 3
  
Phase 3 (10:15 UTC): Return to FVG
  → Price returns to 1.2780
  → Extreme FVG zone
  → Execute SELL_LIMIT
  → SL: 1.2810, TP: 1.2690
  → Trade complete
```

---

## Summary Table

| Feature | Trigger | Entry Method | When Bot Can Shutdown | Best Use |
|---------|---------|--------------|----------------------|----------|
| **Set & Forget** | Fresh FVG on M30 | BUY/SELL_LIMIT | Immediately after | Quick setup, passive trading |
| **Smart Watchlist** | Multi-phase conditions | BUY/SELL_LIMIT | After Phase 3 | High-conviction zones, precision |
| **Regular Mode** | FVG on M5 | BUY/SELL (market) | Never | Active monitoring required |

---

**For questions or issues, check the logs at `logs/` folder or review the implementation in:**
- `pending_order_manager.py`
- `conditional_watchlist_manager.py`
- `engine.py` (Integration)
- `mt5_interface.py` (Order placement)
