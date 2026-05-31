# Quick Start - Advanced Features

## 🚀 Quick Setup (5 minutes)

### Step 1: Enable Features in `.env`
```env
FEATURE_PENDING_ORDERS=true
FEATURE_CONDITIONAL_WATCHLIST=true
```

### Step 2: Start the Bot
```bash
# Windows
run.bat

# Linux/Mac
./run.sh
```

### Step 3: Access Dashboard
```
http://localhost:5000
```

---

## 📋 Feature Quick Reference

### Set and Forget (Pending Orders)

**What it does**: Places limit orders automatically and lets MT5 execute them.

**Start It**:
```bash
curl -X POST http://localhost:5000/api/pending-orders/place \
  -H "Content-Type: application/json" \
  -d '{"symbols": "EURUSD,GBPUSD"}'
```

**Check Orders**:
```bash
curl http://localhost:5000/api/pending-orders
```

**Cancel Order**:
```bash
curl -X DELETE http://localhost:5000/api/pending-orders/EURUSD
```

---

### Smart Watchlist (Multi-Phase Execution)

**What it does**: Monitors symbols through 3 phases, triggers execution only at the perfect time.

**Initialize**:
```bash
curl -X POST http://localhost:5000/api/watchlist/initialize \
  -H "Content-Type: application/json" \
  -d '{"symbols": "EURUSD,GBPUSD,USDJPY"}'
```

**Monitor Progress**:
```bash
curl http://localhost:5000/api/watchlist
```

**Reset Symbol** (back to Phase 1):
```bash
curl -X POST http://localhost:5000/api/watchlist/EURUSD/reset
```

---

### Toggle Features On/Off

```bash
# Turn on pending orders
curl -X POST http://localhost:5000/api/features \
  -H "Content-Type: application/json" \
  -d '{"pending_orders": true}'

# Turn on watchlist
curl -X POST http://localhost:5000/api/features \
  -H "Content-Type: application/json" \
  -d '{"conditional_watchlist": true}'
```

---

## 🎯 Common Workflows

### Workflow 1: Overnight Trading (Set & Forget)
```
1. Evening: Start bot and enable Pending Orders
2. Bot places limit orders based on M30 FVGs
3. Bot shuts down
4. Overnight: MT5 executes orders automatically
5. Morning: Check results and logs
```

### Workflow 2: Precision Entry (Smart Watchlist)
```
1. Morning: Initialize watchlist with key pairs
2. Monitor first 1-2 hours
3. Phase 1: Wait for Asian sweep
4. Phase 2: Watch for structure break
5. Phase 3: Execute when price returns to FVG
6. Done: Trade exits with 1:3 R:R
```

### Workflow 3: 24/7 Passive Trading (Combination)
```
1. Enable both features
2. Let bot run continuously
3. Pending orders: New setups placed automatically
4. Smart Watchlist: High-conviction trades executed
5. Review: Check logs weekly for performance
```

---

## ⚙️ Configuration Reference

### Environment Variables

```env
# Feature toggles
FEATURE_PENDING_ORDERS=true
FEATURE_CONDITIONAL_WATCHLIST=true

# Existing settings (still apply)
TRADING_SYMBOLS=EURUSD,GBPUSD,USDJPY,AUDUSD
TRADE_VOLUME=0.1
RISK_PERCENT=1.0
MAX_EXPOSURE_PERCENT=5
MIN_PROFIT_PIPS=10
NO_REVENGE_COOLDOWN_SECONDS=86400
```

### Risk Settings (Recommended)

**Conservative** (Good for beginners):
```env
RISK_PERCENT=0.5
MAX_EXPOSURE_PERCENT=3
MIN_PROFIT_PIPS=20
```

**Moderate** (Balanced approach):
```env
RISK_PERCENT=1.0
MAX_EXPOSURE_PERCENT=5
MIN_PROFIT_PIPS=10
```

**Aggressive** (Experienced traders):
```env
RISK_PERCENT=2.0
MAX_EXPOSURE_PERCENT=10
MIN_PROFIT_PIPS=5
```

---

## 📊 Monitoring in Real-Time

### Dashboard Features

1. **Status Section**: Shows bot running/connected status
2. **Account Section**: Equity, balance, margin
3. **Positions**: Open trades with profit/loss
4. **Signals**: Recent FVG signals detected
5. **Pending Orders Tab**: Shows all pending orders (NEW)
6. **Watchlist Tab**: Shows multi-phase status (NEW)
7. **Features Tab**: Toggle pending orders & watchlist (NEW)

---

## 🔍 Troubleshooting Quick Fixes

### Pending Orders Not Placing
- ✅ Check: Bot is running (`/api/bot/status`)
- ✅ Check: Feature is enabled (`/api/features`)
- ✅ Check: Symbols exist on MT5
- ✅ Check: Account has equity
- ✅ Check: Kill switch is not active

### Watchlist Not Moving Phases
- ✅ Check: Watchlist initialized (`/api/watchlist`)
- ✅ Check: Enough M30 historical data
- ✅ Check: Bot running continuously
- ✅ Check: Trading hours overlap your strategy

### Orders Filling Unexpectedly
- ✅ Check: MT5 spread (might be wider than expected)
- ✅ Check: Account margin level (must be > 100%)
- ✅ Check: Order SL/TP prices valid for the symbol

---

## 📈 Performance Tips

### For Best Results with Set & Forget
- Use M30 timeframe (already built-in)
- Place 1-2 orders per symbol
- Check positions daily
- Cancel stale orders before weekends
- Use tight risk management (0.5-1%)

### For Best Results with Smart Watchlist
- Use high-conviction pairs only
- Allow 2-4 hours for all phases
- Monitor during active trading hours
- Use 50-EMA filter enabled
- Start with 3-5 symbols

---

## 📝 Logging and Analysis

### View Recent Logs
```bash
# In app dashboard: Go to "Logs" tab
# Or check files:
ls logs/
cat logs/trades_2026-03-19.json
```

### Sample Pending Order Log Entry
```json
{
  "symbol": "EURUSD",
  "ticket": 123456,
  "action": "BUY",
  "entry": 1.0850,
  "sl": 1.0820,
  "tp": 1.0940,
  "status": "PENDING",
  "placed_at": "2026-03-19T14:30:00"
}
```

### Sample Watchlist Log Entry
```json
{
  "symbol": "GBPUSD",
  "phase": 3,
  "phase1_started": "2026-03-19T08:00:00",
  "sweep_detected": true,
  "mBOS_detected": true,
  "ready_for_execution": true
}
```

---

## 🎓 Learning Path

### Beginner
1. Start with Set & Forget only
2. Place 2-3 pending orders manually
3. Let them execute over 24 hours
4. Review results and logs
5. Adjust risk settings if needed

### Intermediate
1. Enable Smart Watchlist
2. Monitor one pair through all phases
3. Watch how price behaves in each phase
4. Understand the multi-phase logic
5. Combine both features together

### Advanced
1. Run both features simultaneously
2. Optimize probability scoring
3. Adjust EMA and volume thresholds
4. Create custom watchlists
5. Analyze correlation between features

---

## ✅ Checklist Before Going Live

- [ ] `.env` file configured with credentials
- [ ] MT5 account connected and tested
- [ ] Risk settings reviewed and appropriate
- [ ] Kill switches verified working
- [ ] Dashboard accessible (`http://localhost:5000`)
- [ ] First pending order placed successfully
- [ ] Watchlist initialized with symbols
- [ ] Logs being recorded properly
- [ ] Account backed up or tested on demo account first
- [ ] Dashboard monitored for first 1 hour

---

## 🚨 Important Reminders

⚠️ **Always:**
- Start on demo account first
- Use kill switches (disable trading if issues occur)
- Monitor for first hour after enabling new features
- Review logs daily
- Keep account equity > $1,000 minimum

⚠️ **Never:**
- Risk more than 2% per trade
- Disable all risk filters
- Leave bot unmonitored for weeks
- Trade illiquid symbols (use major pairs)
- Exceed max exposure limits

---

## 📞 Support & Debugging

### Check Bot Status
```bash
curl http://localhost:5000/api/bot/status
```

### View Current Config
```bash
curl http://localhost:5000/api/config
```

### Get Feature Status
```bash
curl http://localhost:5000/api/features
```

### View All Logs
```bash
curl http://localhost:5000/api/logs
```

---

## 🎉 You're Ready!

Start the bot and watch it:
1. **Scan** for high-probability zones
2. **Place** pending orders automatically
3. **Monitor** multi-phase watchlist progression
4. **Execute** trades with pre-calculated risk management
5. **Deliver** consistent, rules-based trading

---

**Questions?** Check [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) for detailed documentation.
