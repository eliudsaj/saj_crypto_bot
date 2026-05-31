# Nexus Trading Bot - Bug Fix Checklist

## CRITICAL FIXES (Must apply immediately - Risk of financial loss)

### CRITICAL-1: Division by Zero in Volume Calculation
- **File:** `engine.py` - `_calculate_volume()` method
- **Impact:** Bot crashes when calculating trade volume
- **Status:** ☐ NOT STARTED
- **Fix Time:** 15 minutes
- **How:** Add check: `if risk_per_lot <= 0: return self.volume`
- **Test:** Try to place trade with stop loss = entry price

### CRITICAL-2: Global Engine Race Condition
- **File:** `app.py` - Global `engine` variable
- **Impact:** Dashboard and trading engine race conditions, data corruption, crashes
- **Status:** ☐ NOT STARTED
- **Fix Time:** 30 minutes
- **How:** Add `_engine_lock = threading.RLock()` and wrap all engine access
- **Test:** Send 10 simultaneous API requests to `/api/bot/status` while bot running

### CRITICAL-3: Null Risk Value in Trade Closure
- **File:** `engine.py` - `check_positions()` method
- **Impact:** Risk calculations fail silently, crashes possible
- **Status:** ☐ NOT STARTED
- **Fix Time:** 10 minutes
- **How:** Always initialize `risk` to 0.0 if None instead of None
- **Test:** Close a position and check if risk logs correctly

### CRITICAL-4: MT5 Order Errors Not Handled
- **File:** `mt5_interface.py` - `place_buy_order()` and `place_sell_order()`
- **Impact:** Orders silently fail - insufficient margin, market closed, etc. go undetected
- **Status:** ☐ NOT STARTED
- **Fix Time:** 20 minutes
- **How:** Check result.retcode for error codes (INSUFFICIENT_FUNDS, MARKET_CLOSED, etc.)
- **Test:** Try placing impossibly large order on real account

### CRITICAL-5: No MT5 Reconnection Logic
- **File:** `mt5_interface.py` - `connect()` method
- **Impact:** If MT5 disconnects, bot stops trading without warning
- **Status:** ☐ NOT STARTED
- **Fix Time:** 45 minutes
- **How:** Add retry loop with exponential backoff (max 5 attempts)
- **Test:** Disconnect from internet during trading, verify bot reconnects

---

## HIGH SEVERITY FIXES (Fix within 1 week)

### HIGH-1: Empty Symbols List
- **File:** `engine.py` - `scan_and_trade()` method
- **Impact:** Bot runs but trades nothing, user unaware
- **Status:** ☐ NOT STARTED
- **Fix:** Check `if not self.symbols` at start of scan_and_trade()

### HIGH-2: Stop Loss Equals Entry Price
- **File:** `engine.py` - `_is_signal_big_enough()` method
- **Impact:** Invalid signals accepted, trades fail
- **Status:** ☐ NOT STARTED
- **Fix:** Add: `if abs(entry - sl) < pip_size: return False, "SL too close"`

### HIGH-3: Account Equity Goes to Zero
- **File:** `engine.py` - `_can_trade()` method
- **Impact:** Bot enters liquidated account into crashed state
- **Status:** ☐ NOT STARTED
- **Fix:** Add check and force stop: `if equity <= 0: self.stop()`

### HIGH-4: Float Comparison Without Epsilon
- **File:** `bible_logic.py` - EMA filter logic
- **Impact:** False rejections on edge cases (microscopically close prices)
- **Status:** ☐ NOT STARTED
- **Fix:** Use `EPSILON = 1e-6` instead of `<=` and `>=`

### HIGH-5: Array Indexing Edge Case
- **File:** `bible_logic.py` - Volume filter calculation
- **Impact:** Potential index errors on sparse data
- **Status:** ☐ NOT STARTED
- **Fix:** Ensure enough bars before slicing

### HIGH-6: Pending Orders - No Symbol Validation
- **File:** `pending_order_manager.py` - `identify_high_probability_zones()`
- **Impact:** Pending orders fail silently for typo'd symbols
- **Status:** ☐ NOT STARTED
- **Fix:** Validate symbol exists: `if mt5.symbol_info(symbol) is None`

### HIGH-7: Configuration Not Validated
- **File:** `engine.py` - `__init__` method
- **Impact:** Invalid config crashes bot at runtime
- **Status:** ☐ NOT STARTED
- **Fix:** Add `_validate_config()` method with bounds checking

### HIGH-8: MT5 Password in Plain Text
- **File:** `mt5_interface.py` - Credentials storage
- **Impact:** Account credentials can be stolen from .env file
- **Status:** ☐ NOT STARTED
- **Fix:** Store password in OS keyring instead of .env

### HIGH-9: Revenge Trading Cooldown Bypass
- **File:** `engine.py` - `_can_trade()` and restart logic
- **Impact:** User can restart bot immediately after loss to bypass cooldown
- **Status:** ☐ NOT STARTED
- **Fix:** Persist cooldown to file, check on startup

### HIGH-10: API Input Not Validated
- **File:** `app.py` - All POST endpoints
- **Impact:** Attacker can crash bot via API with malicious input
- **Status:** ☐ NOT STARTED
- **Fix:** Add `validate_start_config()` function for all inputs

---

## MEDIUM SEVERITY FIXES (Fix within 2 weeks)

### MEDIUM-1: Null Symbol Info Crashes
- **File:** `technical_analysis.py` - pip calculation
- **Status:** ☐ NOT STARTED
- **Impact:** Wrong pip sizes for exotic pairs

### MEDIUM-2: Concurrent Signal Duplication
- **File:** `engine.py` - `recent_signals` access
- **Status:** ☐ NOT STARTED
- **Impact:** Dashboard shows corrupted signal data

### MEDIUM-3: Incomplete Candle Data
- **File:** `bible_logic.py` - Data validation
- **Status:** ☐ NOT STARTED
- **Impact:** Calculations fail silently with NaN

### MEDIUM-4: Timezone Edge Cases
- **File:** `bible_logic.py` - Asian session logic
- **Status:** ☐ NOT STARTED
- **Impact:** Incorrect sweep detection around 8 AM UTC

### MEDIUM-5: Trade Journal Memory Leak
- **File:** `engine.py` - `trade_journal` grows unbounded
- **Status:** ☐ NOT STARTED
- **Impact:** Bot crashes after weeks of trading

### MEDIUM-6: Rejection Logs Memory Leak
- **File:** `engine.py` - `rejection_logs` limited to 50
- **Status:** ☐ NOT STARTED
- **Impact:** Lost rejection history

### MEDIUM-7: Watchlist Phase Stuck
- **File:** `conditional_watchlist_manager.py` - Phase logic
- **Status:** ☐ NOT STARTED
- **Impact:** Symbols stuck in Phase 1, never execute

### MEDIUM-8: Wrong FVG Priority
- **File:** `conditional_watchlist_manager.py` - Extreme FVG selection
- **Status:** ☐ NOT STARTED
- **Impact:** Trades on weak signals

### MEDIUM-9: Invalid Filling Mode
- **File:** `mt5_interface.py` - `_get_filling_mode()`
- **Status:** ☐ NOT STARTED
- **Impact:** Orders fail on unsupported brokers

### MEDIUM-10: Kill Switch Ambiguous
- **File:** `engine.py` - Kill switch logic
- **Status:** ☐ NOT STARTED
- **Impact:** Can't properly control trade disabling

---

## ACTION ITEMS BY PRIORITY

### 🔴 DO RIGHT NOW (Today)
```
1. [ ] Read SECURITY_AUDIT_REPORT.md completely
2. [ ] Test for Issue #1 (division by zero):
        - Set volume/risk to trigger edge case
        - Verify bot crashes
3. [ ] Apply FIX #1 to app.py (add threading lock)
4. [ ] Apply FIX #2 to engine.py (_calculate_volume safety)
5. [ ] Apply FIX #3 to engine.py (_can_trade equity check)
6. [ ] Restart bot and verify no immediate crashes
```

### 🟠 THIS WEEK
```
7. [ ] Apply FIX #4 to mt5_interface.py (error handling)
8. [ ] Apply FIX #5 to mt5_interface.py (reconnection)
9. [ ] Apply FIX #6 to engine.py (config validation)
10. [ ] Apply FIX #7 to app.py (input validation)
11. [ ] Test each fix with edge cases
12. [ ] Review rejection logs for patterns
13. [ ] Monitor bot for 24 hours after fixes
```

### 🟡 THIS MONTH
```
14. [ ] Apply all MEDIUM severity fixes
15. [ ] Add automated tests for edge cases
16. [ ] Set up monitoring/alerting
17. [ ] Document configuration best practices
18. [ ] Review and optimize risk management
```

---

## TESTING CHECKLIST

For each fix applied, run these tests:

### Edge Case Tests
- [ ] Empty symbols list
- [ ] Symbol with negative volume config
- [ ] Entry = SL price
- [ ] Zero account equity
- [ ] Invalid MT5 credentials
- [ ] MT5 disconnection mid-trade
- [ ] Concurrent API requests
- [ ] Restart during cooldown
- [ ] Missing .env values
- [ ] Large negative config values

### Regression Tests
- [ ] Normal FVG detection works
- [ ] Orders place correctly
- [ ] Positions tracked correctly
- [ ] Dashboard updates in real-time
- [ ] Trade journal populated
- [ ] Rejection logs working
- [ ] Pending orders feature works
- [ ] Watchlist feature works

### Stress Tests
- [ ] 10 concurrent /api/bot/status requests
- [ ] Rapid bot start/stop cycles
- [ ] High volume of rejections (1000+/cycle)
- [ ] Long uptime (24+ hours) for memory leaks
- [ ] Multiple fast symbol additions/removals

---

## MONITORING CHECKLIST

After deploying fixes, monitor:

- [ ] Log file growth (check for recursive logging)
- [ ] Memory usage over time (check for leaks)
- [ ] CPU usage (check for infinite loops)
- [ ] MT5 connection stability
- [ ] Trade execution success rate
- [ ] Rejection rate by symbol
- [ ] API response times
- [ ] Dashboard responsiveness

---

## DEPLOYMENT STRATEGY

### Phase 1: Critical Fixes (Today)
1. Apply CRITICAL-1 through CRITICAL-5
2. Test locally with demo account
3. Deploy to live with monitoring
4. Watch for 2 hours

### Phase 2: High Priority Fixes (This Week)  
1. Apply HIGH-1 through HIGH-10
2. Test on demo/small account
3. Deploy with monitoring
4. Monitor for 24 hours

### Phase 3: Medium Fixes (This Month)
1. Apply MEDIUM-1 through MEDIUM-10
2. Full regression testing
3. Deploy to production
4. Enable monitoring/alerts

---

## ROLLBACK PLAN

If any fix causes issues:
1. Stop the bot immediately
2. Check logs for the issue
3. Skip that specific fix
4. Revert to last working version
5. Report the specific issue

---

## Questions Before Deployment?

Review:
- Have you read SECURITY_AUDIT_REPORT.md?
- Have you applied fixes to a test branch first?
- Have you tested with edge cases?
- Do you have backup account credentials?
- Do you understand each fix before applying?

---

**Last Updated:** March 19, 2026
**Total Issues Found:** 47 (5 CRITICAL, 10 HIGH, 10 MEDIUM, 22 LOW)
**Estimated Fix Time:** 8-10 hours total
