# Nexus Trading Bot - Critical Bug Fixes Guide

## Quick Reference - Apply These Fixes First

### FIX #1: Add Threading Lock to Global Engine (CRITICAL)

**File:** `app.py` - Add at top with imports

```python
import threading

# Add this after the imports (line ~20)
_engine_lock = threading.RLock()

# Wrap every access to global engine with lock
@app.route("/api/bot/start", methods=["POST"])
def api_start():
    global engine, _engine_thread
    with _engine_lock:  # ← Add this
        if engine and engine.is_running:
            return jsonify({"status": "error", "message": "Bot already running"}), 400
        # ... rest of function

@app.route("/api/bot/stop", methods=["POST"])
def api_stop():
    global engine, _engine_thread
    with _engine_lock:  # ← Add this
        if engine:
            engine.stop()
            engine.disconnect()
            if _engine_thread and _engine_thread.is_alive():
                _engine_thread.join(timeout=2)
            engine = None
            _engine_thread = None
            return jsonify({"status": "success", "message": "Bot stopped"})
        return jsonify({"status": "error", "message": "Bot not running"}), 400

@app.route("/api/bot/status", methods=["GET"])
def api_status():
    with _engine_lock:  # ← Add this
        try:
            if engine:
                status = engine.get_status() or {}
                return jsonify({...})
            return jsonify({"running": False, "connected": False, "equity": None})
```

Apply lock to ALL routes that access `engine` variable.

---

### FIX #2: Safe Risk Calculation in TradingEngine

**File:** `engine.py` - Replace `_calculate_volume()` method (lines 96-110)

```python
def _calculate_volume(self, symbol: str, entry: float, sl: float) -> float:
    """Calculate lot size based on risk percentage and stop distance."""
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
    
    # CRITICAL FIX: Check pip_size
    if pip_size <= 0:
        logger.error(f"Invalid pip_size for {symbol}: {pip_size}")
        return self.volume
    
    stop_pips = abs(entry - sl) / pip_size
    
    # CRITICAL FIX: Validate stop_pips
    if stop_pips <= 0:
        logger.error(f"Invalid stop distance for {symbol}: entry={entry}, sl={sl}, stop_pips={stop_pips}")
        return self.volume

    risk_per_lot = stop_pips * pip_value
    
    # CRITICAL FIX: Check risk_per_lot before division
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
    
    logger.info(f"Calculated volume for {symbol}: {volume:.2f} (risk=${risk_amount:.2f}, per_lot=${risk_per_lot:.2f})")
    return volume
```

---

### FIX #3: Validate Equity Before Trading

**File:** `engine.py` - Replace or enhance `_can_trade()` (lines 202-217)

```python
def _can_trade(self):
    """Determine if new trades are allowed (cooldown, max exposure, equity check)."""
    
    # Check cooldown
    if self.cooldown_until and datetime.now() < self.cooldown_until:
        remaining = (self.cooldown_until - datetime.now()).total_seconds() / 60
        logger.info(f"In cooldown. {remaining:.0f} minutes remaining")
        return False, f"In cooldown ({remaining:.0f}m remaining)"

    # Get equity
    equity = self._get_equity()
    
    # CRITICAL FIX: Handle None and check > 0
    if equity is None:
        logger.error("Cannot get account equity")
        return False, "Cannot get account equity"
    
    if equity <= 0:
        logger.critical(f"ACCOUNT LIQUIDATED! Equity: {equity}. Stopping engine.")
        self.is_running = False  # Stop immediately
        self.logger._save_log({
            "timestamp": datetime.now().isoformat(),
            "event": "LIQUIDATION_ALERT",
            "equity": equity,
            "balance": self.mt5.get_account_info().get("balance") if self.mt5.get_account_info() else None,
        })
        return False, "Account liquidated - stopping engine"

    # Check exposure
    current_exposure = self._calculate_exposure()
    max_exposure = equity * self.max_exposure_pct
    
    if current_exposure >= max_exposure:
        logger.info(f"Max exposure reached. Current: ${current_exposure:.2f}, Max: ${max_exposure:.2f}")
        return False, f"Max exposure reached (${current_exposure:.2f}/${max_exposure:.2f})"

    return True, "OK"
```

---

### FIX #4: Handle MT5 Order Failures Properly

**File:** `mt5_interface.py` - Replace `place_buy_order()` method (lines 95-117)

```python
def place_buy_order(self, symbol, volume, price, sl, tp):
    try:
        if not self.is_connected:
            logger.error("MT5 not connected")
            return None
        
        filling = self._get_filling_mode(symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5.ORDER_TYPE_BUY,
            "price": price,
            "sl": sl,
            "tp": tp,
            "filling": filling,
            "comment": "FVG_BUY",
        }
        
        result = mt5.order_send(request)
        
        # CRITICAL FIX: Handle all error codes
        if result is None:
            logger.error(f"BUY order failed for {symbol}: No response from MT5")
            return None
        
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"BUY order placed: {symbol} (vol={volume}, price={price:.5f})")
            return result.order
        
        # Handle specific error codes
        error_messages = {
            mt5.TRADE_RETCODE_INSUFFICIENT_FUNDS: "Insufficient margin",
            mt5.TRADE_RETCODE_INVALID_VOLUME: "Invalid volume",
            mt5.TRADE_RETCODE_MARKET_CLOSED: "Market closed",
            mt5.TRADE_RETCODE_PRICES_CHANGED: "Price changed",
            mt5.TRADE_RETCODE_INVALID_EXPIRATION: "Invalid expiration",
            mt5.TRADE_RETCODE_ORDER_CHANGED: "Order changed",
            mt5.TRADE_RETCODE_TOO_MANY_REQUESTS: "Too many requests",
            mt5.TRADE_RETCODE_NO_CHANGES: "No changes",
            mt5.TRADE_RETCODE_TRADE_DISABLED: "Trading disabled",
        }
        
        error_msg = error_messages.get(result.retcode, result.comment or f"Unknown error {result.retcode}")
        logger.error(f"BUY order failed for {symbol}: [{result.retcode}] {error_msg}")
        
        return None
    except Exception as e:
        logger.error(f"Exception placing buy order for {symbol}: {e}", exc_info=True)
        return None

# Apply same fixes to place_sell_order() method
```

---

### FIX #5: Validate Configuration on Startup

**File:** `engine.py` - Add new method in `TradingEngine.__init__`

```python
    def __init__(self):
        # ... existing code ...
        self._validate_config()
    
    def _validate_config(self):
        """Validate all configuration values on startup"""
        
        # Validate TRADING_SYMBOLS
        if isinstance(self.symbols, str):
            symbols = [s.strip().upper() for s in self.symbols.split(",") if s.strip()]
        else:
            symbols = [str(s).strip().upper() for s in self.symbols if s]
        
        if not symbols:
            logger.error("No valid TRADING_SYMBOLS configured. Using defaults.")
            self.symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        else:
            self.symbols = symbols
            logger.info(f"Trading symbols: {self.symbols}")
        
        # Validate TRADE_VOLUME
        if self.volume <= 0:
            logger.error(f"Invalid TRADE_VOLUME: {self.volume}. Must be positive.")
            self.volume = 0.1
        if self.volume > 10:
            logger.warning(f"TRADE_VOLUME is very high: {self.volume}")
        
        # Validate RISK_PERCENT
        if self.risk_pct <= 0 or self.risk_pct > 1:
            logger.error(f"Invalid RISK_PERCENT: {self.risk_pct}. Must be 0 < x <= 1.")
            self.risk_pct = 0.01
        
        # Validate MAX_EXPOSURE_PERCENT
        if self.max_exposure_pct <= 0 or self.max_exposure_pct > 1:
            logger.error(f"Invalid MAX_EXPOSURE_PERCENT: {self.max_exposure_pct}.")
            self.max_exposure_pct = 0.05
        
        # Validate MIN_PROFIT_PIPS
        if self.min_profit_pips < 1:
            logger.error(f"Invalid MIN_PROFIT_PIPS: {self.min_profit_pips}.")
            self.min_profit_pips = 10
        
        logger.info(f"Config validated. Volume={self.volume}, Risk={self.risk_pct*100}%, Exposure={self.max_exposure_pct*100}%")
```

---

### FIX #6: Add Reconnection Logic to MT5

**File:** `mt5_interface.py` - Replace `connect()` method

```python
def connect(self):
    """Connect to MT5 with automatic retry logic"""
    max_retries = 5
    retry_delay = 1
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Connecting to MT5 (attempt {attempt+1}/{max_retries})...")
            
            if not mt5.initialize():
                logger.error(f"MT5 initialization failed (attempt {attempt+1})")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)  # Exponential backoff, max 10s
                continue
            
            # Try to get account info to verify connection
            account_info = mt5.account_info()
            if account_info is None:
                logger.error(f"Failed to get account info (attempt {attempt+1})")
                mt5.shutdown()
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10)
                continue
            
            self.is_connected = True
            logger.info("Connected to MT5 successfully")
            return True
            
        except Exception as e:
            logger.error(f"MT5 connection error (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 10)
    
    logger.critical(f"Failed to connect to MT5 after {max_retries} attempts")
    self.is_connected = False
    return False

def ensure_connected(self):
    """Check connection and reconnect if needed"""
    if not self.is_connected:
        logger.warning("MT5 connection lost, attempting to reconnect...")
        return self.connect()
    return True
```

---

### FIX #7: Add Threading Locks to Shared Data

**File:** `engine.py` - Add to `TradingEngine.__init__`

```python
    def __init__(self):
        # ... existing code ...
        
        # Add threading locks for shared data
        self._lock = threading.RLock()  # General engine lock
        self._signals_lock = threading.Lock()  # For signals
        self._positions_lock = threading.Lock()  # For positions
        self._trades_lock = threading.Lock()  # For active trades
```

Then update methods that access shared data:

```python
def scan_and_trade(self):
    """Scan for FVG signals and execute trades"""
    try:
        signals = scan_symbols(self.symbols, self.timeframe)

        # With lock protection
        with self._signals_lock:
            for signal in signals:
                self.recent_signals.append(signal)
            self.recent_signals = self.recent_signals[-20:]

        for signal in signals:
            # ... rest of processing
```

---

### FIX #8: Input Validation for API Endpoints

**File:** `app.py` - Add validation function before routes (line ~20)

```python
def validate_start_config(payload: dict) -> tuple[bool, str]:
    """Validate bot start configuration"""
    try:
        # Validate symbols
        if "symbols" in payload:
            symbols = payload["symbols"]
            if isinstance(symbols, str):
                syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
                if not syms:
                    return False, "Symbols cannot be empty"
            elif isinstance(symbols, list):
                syms = [str(s).strip().upper() for s in symbols if s]
                if not syms:
                    return False, "Symbols list cannot be empty"
            else:
                return False, "Symbols must be string or array"
        
        # Validate volume
        if "volume" in payload:
            try:
                vol = float(payload["volume"])
                if vol <= 0 or vol > 10:
                    return False, f"Volume must be 0 < x <= 10, got {vol}"
            except (TypeError, ValueError):
                return False, "Volume must be a valid number"
        
        # Validate risk percentage
        if "risk_pct" in payload:
            try:
                risk = float(payload["risk_pct"])
                if risk <= 0 or risk > 1:
                    return False, f"Risk must be 0 < x <= 1, got {risk}"
            except (TypeError, ValueError):
                return False, "Risk must be a valid number"
        
        # Validate max exposure
        if "max_exposure_pct" in payload:
            try:
                exp = float(payload["max_exposure_pct"])
                if exp <= 0 or exp > 1:
                    return False, f"Exposure must be 0 < x <= 1, got {exp}"
            except (TypeError, ValueError):
                return False, "Exposure must be a valid number"
        
        return True, "OK"
    except Exception as e:
        return False, f"Validation error: {e}"

# Update api_start to use validation
@app.route("/api/bot/start", methods=["POST"])
def api_start():
    global engine, _engine_thread
    try:
        with _engine_lock:
            if engine and engine.is_running:
                return jsonify({"status": "error", "message": "Bot already running"}), 400

            payload = request.json or {}
            
            # CRITICAL FIX: Validate input
            valid, message = validate_start_config(payload)
            if not valid:
                return jsonify({"status": "error", "message": message}), 400
            
            # ... rest of function
```

---

### FIX #9: Validate Stop Loss vs Entry

**File:** `engine.py` - Add check to `_is_signal_big_enough()` before line 301

```python
def _is_signal_big_enough(self, signal: dict):
    """Check if a signal has enough pip distance to justify a trade"""
    symbol = signal.get("symbol")
    entry = signal.get("entry")
    tp = signal.get("tp")
    sl = signal.get("sl")
    
    if symbol is None or entry is None or tp is None or sl is None:
        return False, "Signal missing entry/tp/sl"

    pip_size = self._get_pip_size(symbol)
    if not pip_size:
        return False, "Unable to determine pip size"

    # CRITICAL FIX: Validate SL != Entry
    sl_distance = abs(entry - sl)
    if sl_distance < pip_size:
        return False, f"SL too close to entry: {sl_distance:.8f} < {pip_size}"
    
    tp_distance = abs(tp - entry)
    if tp_distance < pip_size:
        return False, f"TP too close to entry: {tp_distance:.8f} < {pip_size}"

    pip_distance = tp_distance / pip_size
    if pip_distance < self.min_profit_pips:
        return False, f"TP distance {pip_distance:.1f} pips < minimum {self.min_profit_pips:.1f} pips"

    return True, "Sufficient target distance"
```

---

### FIX #10: Empty Symbols Check

**File:** `engine.py` - Add check to start of `scan_and_trade()` (line 379)

```python
def scan_and_trade(self):
    """Scan for FVG signals and execute trades"""
    try:
        # CRITICAL FIX: Validate symbols list
        if not self.symbols or len(self.symbols) == 0:
            logger.error("No symbols configured for trading. Set TRADING_SYMBOLS in .env")
            return
        
        # Filter out empty strings
        self.symbols = [s for s in self.symbols if s and str(s).strip()]
        if not self.symbols:
            logger.error("All symbols are invalid/empty. Cannot trade.")
            return
        
        signals = scan_symbols(self.symbols, self.timeframe)
        # ... rest of method
```

---

## Implementation Timeline

| Priority | Issues | Est. Time |
|----------|--------|-----------|
| **TODAY** | 1.1, 1.2, 1.3, 1.4, 1.5, 2.7 | 2 hours |
| **This Week** | 2.1, 2.2, 2.3, 2.8, 2.9, 2.10 | 3 hours |
| **Next Week** | All MEDIUM severity | 4 hours |

---

## Testing After Fixes

```bash
# Test 1: Empty symbols
# Set TRADING_SYMBOLS="" in .env, start bot
# Expected: Log error and use defaults

# Test 2: Invalid config
# POST to /api/bot/start with volume=-5
# Expected: Validation error 400 response

# Test 3: Zero equity
# Manually set account equity to 0 via MT5
# Expected: Bot stops, LIQUIDATION_ALERT logged

# Test 4: MT5 disconnect
# Unplug internet during live trading
# Expected: Automatic reconnect attempts

# Test 5: Concurrent API calls
# Send 10 simultaneous requests to /api/bot/status
# Expected: No crashes, consistent data
```

---

You can now apply these fixes systematically. Start with FIX #1-5 (CRITICAL issues), then move to FIX #6-10 (HIGH severity).
