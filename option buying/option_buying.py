"""
Nifty 5m Triple Confirm Strategy  v5
====================================
Trading Logic (matches Pine Script v6 exactly):
- Base Entry: SMA68 / SMA90 crossover above/below EMA340 with configurable distance filter.
- Smart Re-Entry: Close-based pullback below/above EMA340, trigger on close back across EMA.
- Instrument Traded: Weekly Call (CE) or Put (PE) option closest to Rs. 200.
- Trailing SL:
    * Initial SL fixed at entry_spot ± 0.75% until profit reaches ≥ 0.75%.
    * Once activated, trails on candle high (long) or low (short).
    * Exit triggers on candle LOW breaching SL (long) or candle HIGH (short).
- Timeframe: 5 minutes.
"""

import os
import json
import time
import csv
import math
from datetime import datetime, date, timedelta
from openalgo import api

# ============================================================
# 1. PARAMETERS & CONFIG
# ============================================================
API_KEY          = "ff1d258f2c5d48bf87292b3f055b39772ca76f4afe69ff7c7c5fa121a32334d8"
HOST             = "http://127.0.0.1:5000"

UNDERLYING       = "NIFTY"
EXCHANGE_INDEX   = "NSE_INDEX"
EXCHANGE_DERIV   = "NFO"
PRODUCT          = "NRML"         # Intraday product type
LOT_SIZE         = 65            # Default Nifty lot size
TARGET_PREMIUM   = 200.0         # Target premium to buy (Rs. 200)

# Indicator Lengths
SMA_FAST_LEN     = 68
SMA_SLOW_LEN     = 90
EMA_TREND_LEN    = 340

# Protection/Filters
SL_PCT           = 0.75          # Initial Stop Loss % on Nifty Spot
TRAIL_PCT        = 0.75          # Trailing Stop Loss % on Nifty Spot
DIST_PCT         = 0.0           # 0.0% Spot distance for Base Entry
RE_ENTRY_DIST_PCT= 0.0           # 0.0% Spot distance for Re-Entry

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
STATE_FILE       = os.path.join(SCRIPT_DIR, "nifty_5m_state.json")
TRADE_LOG        = os.path.join(SCRIPT_DIR, "nifty_5m_trades.csv")
AUDIT_LOG        = os.path.join(SCRIPT_DIR, "nifty_5m_audit.csv")

# ============================================================
# 2. STATE MANAGEMENT
# ============================================================
def load_state():
    """Load persistent strategy state from JSON file."""
    today_str = date.today().strftime("%Y-%m-%d")
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                s = json.load(f)
                if s.get("date") == today_str:
                    return s
        except Exception as e:
            print(f"[State Load Error] {e}")
            
    # Default State
    return {
        "date": today_str,
        "bullTrend": False,
        "bearTrend": False,
        "longPullbackReady": False,
        "shortPullbackReady": False,
        "lastSignal": 0,          # 1 = LONG, -1 = SHORT, 0 = RESET
        "oppositeSeen": True,
        "position": {
            "active": False,
            "symbol": "",
            "option_type": "",    # "CE" or "PE"
            "direction": "",      # "LONG" or "SHORT"
            "entry_type": "",     # "BASE_ENTRY" or "SMART_REENTRY"
            "entry_spot": 0.0,
            "entry_option_price": 0.0,
            "entry_time": "",     # ISO timestamp of entry
            "trail_high": 0.0,
            "trail_low": 0.0,
            "stop_loss": 0.0,
            "trail_active": False,  # True once profit >= TRAIL_PCT
            "lot_size": 0
        }
    }

def save_state(state):
    """Save strategy state back to JSON file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"[State Save Error] {e}")

# ============================================================
# 3. JOURNALING & AUDIT LOGS
# ============================================================
def log_decision(state, action, reason, spot, details=None):
    """Log strategy decisions to CSV for auditing."""
    try:
        file_exists = os.path.exists(AUDIT_LOG)
        with open(AUDIT_LOG, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp", "action", "reason", "spot_close", "state_details"])
            
            # Simple state dump for audit trail
            state_dump = {
                "bullTrend": state["bullTrend"], "bearTrend": state["bearTrend"],
                "L_pullback": state["longPullbackReady"], "S_pullback": state["shortPullbackReady"],
                "lastSignal": state["lastSignal"], "oppositeSeen": state["oppositeSeen"],
                "pos_active": state["position"]["active"]
            }
            if details:
                state_dump.update(details)
                
            writer.writerow([
                datetime.now().isoformat(),
                action, reason, round(spot, 2),
                json.dumps(state_dump)
            ])
    except Exception as e:
        print(f"[Audit Log Error] {e}")

def log_trade(action, symbol, option_price, spot, state, exit_reason=""):
    """Log trade execution (Entry / Exit) to CSV journal."""
    try:
        file_exists = os.path.exists(TRADE_LOG)
        with open(TRADE_LOG, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp", "action", "symbol", "option_price", "spot_price",
                    "exit_reason", "pnl_spot", "pnl_option"
                ])
                
            pnl_spot = ""
            pnl_option = ""
            if action == "EXIT":
                pnl_spot = round(spot - state["position"]["entry_spot"], 2)
                pnl_option = round(option_price - state["position"]["entry_option_price"], 2)
                if state["position"]["option_type"] == "PE":
                    pnl_spot = -pnl_spot # Spot PnL inverted for Puts
                
            writer.writerow([
                datetime.now().isoformat(),
                action, symbol, round(option_price, 2), round(spot, 2),
                exit_reason, pnl_spot, pnl_option
            ])
    except Exception as e:
        print(f"[Trade Log Error] {e}")

# ============================================================
# 4. INDICATORS & HISTORY MODULE
# ============================================================
def calculate_sma(prices, length):
    """Standard Simple Moving Average calculation."""
    if len(prices) < length:
        return 0.0
    return sum(prices[-length:]) / length

def calculate_ema(prices, length):
    """Standard Exponential Moving Average matching Pine's ta.ema."""
    if len(prices) < length:
        return 0.0
    
    # Start with SMA as the initial EMA seed
    ema = sum(prices[:length]) / length
    multiplier = 2.0 / (length + 1.0)
    
    # Calculate recursive EMA for the rest of the prices
    for val in prices[length:]:
        ema = (val * multiplier) + (ema * (1.0 - multiplier))
    return ema


def parse_candle_time(c):
    """Parse various timestamp formats from candle data."""
    t_str = c.get("time") or c.get("datetime") or c.get("timestamp")
    if isinstance(t_str, datetime):
        return t_str.replace(tzinfo=None)
    try:
        return datetime.strptime(str(t_str), "%Y-%m-%d %H:%M:%S")
    except:
        try:
            t_str_str = str(t_str)
            if "+" in t_str_str:
                t_str_clean = t_str_str.split("+")[0]
            else:
                t_str_clean = t_str_str
            return datetime.fromisoformat(t_str_clean.replace("Z", ""))
        except Exception as ex:
            print("Error parsing single candle timestamp:", ex)
            return datetime.now()


def fetch_candles(client):
    """
    Fetches historical 5-minute candles of Nifty Spot to calculate indicators.
    Retrieves the past 10 days to guarantee enough candles for the 340-period EMA.
    Filters out the active, live-forming candle at the end.
    """
    try:
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=10)
        
        hist = client.history(
            symbol=UNDERLYING,
            exchange=EXCHANGE_INDEX,
            interval="5m",
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=end_dt.strftime("%Y-%m-%d")
        )
        
        # Convert response to list of dictionaries
        if hasattr(hist, "to_dict"):
            if hasattr(hist, "reset_index"):
                hist = hist.reset_index()
            candles = hist.to_dict("records")
        else:
            candles = hist if isinstance(hist, list) else hist.get("data", [])
            
        if not candles or len(candles) < (EMA_TREND_LEN + 10):
            print(f"[History Error] Insufficient history fetched. Count: {len(candles) if candles else 0}")
            return []
            
        candles = sorted(candles, key=parse_candle_time)
        
        # Filters out the currently forming bar.
        # Completed bars terminate on 5-minute intervals. If the last bar's timestamp is the current 5-min
        # start interval (e.g. at 09:20:05, a bar starting at 09:20:00 is active/forming), we remove it.
        now_dt = datetime.now()
        current_bar_start = now_dt.replace(second=0, microsecond=0)
        current_bar_start = current_bar_start - timedelta(minutes=(current_bar_start.minute % 5))
        
        completed_candles = []
        for c in candles:
            c_time = parse_candle_time(c)
            if c_time < current_bar_start:
                completed_candles.append(c)
                
        return completed_candles
    except Exception as e:
        print(f"[Fetch Candles Error] {e}")
        return []

# ============================================================
# 5. ORDER ROUTING & OPTION SELECTION
# ============================================================
def get_expiry(client):
    """Retrieve nearest option expiry date formatted without hyphens (e.g. '28APR26')."""
    try:
        res = client.expiry(symbol=UNDERLYING, exchange=EXCHANGE_DERIV, instrumenttype="options")
        dates = res if isinstance(res, list) else res.get("data", [])
        if dates:
            return dates[0].replace("-", "")
    except Exception as e:
        print(f"[Expiry Fetch Error] {e}")
    return None

def find_option_near_200(client, expiry, option_type):
    """
    Fetches the 20-strike Nifty option chain and selects the specific option
    whose Last Traded Price (LTP) is closest to Rs. 200.
    """
    try:
        # Fetch option chain centered around ATM
        res = client.optionchain(
            underlying=UNDERLYING,
            exchange=EXCHANGE_INDEX,
            expiry_date=expiry,
            strike_count=20
        )
        
        if res.get("status") != "success":
            print(f"[Option Chain Error] API returned: {res.get('message')}")
            return None, 0.0
            
        chain = res.get("chain", [])
        best_symbol = None
        best_ltp = 0.0
        min_diff = float("inf")
        
        for item in chain:
            opt = item.get(option_type.lower())
            if not opt:
                continue
                
            ltp = float(opt.get("ltp", 0.0))
            sym = opt.get("symbol")
            
            if ltp <= 0 or not sym:
                continue
                
            diff = abs(ltp - TARGET_PREMIUM)
            if diff < min_diff:
                min_diff = diff
                best_symbol = sym
                best_ltp = ltp
                
        return best_symbol, best_ltp
    except Exception as e:
        print(f"[Option Selection Error] {e}")
        return None, 0.0

def fetch_option_quote(client, symbol):
    """Fetches the current LTP of a specific option contract."""
    try:
        res = client.multiquotes(symbols=[{"symbol": symbol, "exchange": EXCHANGE_DERIV}])
        results = res.get("results", [])
        if results:
            item = results[0]
            ltp = item.get("ltp") or item.get("data", {}).get("ltp", 0.0)
            return float(ltp)
    except Exception as e:
        print(f"[Quote Fetch Error] {e}")
    return 0.0

def execute_market_order(client, symbol, action):
    """Submits a single market order via the Basket endpoint."""
    try:
        order = {
            "symbol": symbol,
            "action": action,
            "exchange": EXCHANGE_DERIV,
            "pricetype": "MARKET",
            "product": PRODUCT,
            "quantity": LOT_SIZE
        }
        res = client.basketorder(strategy="5M_TRIPLE_CONFIRM", orders=[order])
        print(f"[ORDER] Sent {action} order for {symbol}. Status: {res.get('status')}")
        return res.get("status") == "success"
    except Exception as e:
        print(f"[Order Execution Error] {e}")
        return False

# ============================================================
# 6. STRATEGY ENGINE & LOGIC
# ============================================================
def evaluate_strategy_rules(client, state):
    """
    Core strategy execution logic:
    - Fetches Nifty Spot candles
    - Calculates indicators (SMA68, SMA90, EMA340)
    - Updates trend state and pullback variables
    - Checks for base entries and smart re-entries
    - Evaluates Spot-based trailing stop-loss (TSL) exits
    """
    # 1. Fetch candles
    candles = fetch_candles(client)
    if not candles:
        print("[Engine] Could not fetch valid candle history. Skipping loop.")
        return
        
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    
    current_close = closes[-1]
    current_high = highs[-1]
    current_low = lows[-1]
    
    # 2. Calculate Indicators
    sma68 = calculate_sma(closes, SMA_FAST_LEN)
    sma90 = calculate_sma(closes, SMA_SLOW_LEN)
    ema340 = calculate_ema(closes, EMA_TREND_LEN)
    
    # Validate calculations
    if sma68 == 0.0 or sma90 == 0.0 or ema340 == 0.0:
        print("[Engine] Indicators still seeding. Waiting.")
        return
        
    # Standard regime triggers
    above_ema = current_close > ema340
    below_ema = current_close < ema340
    
    # Crossovers (checking crossover between the last two completed candles)
    prev_sma68 = calculate_sma(closes[:-1], SMA_FAST_LEN)
    prev_sma90 = calculate_sma(closes[:-1], SMA_SLOW_LEN)
    
    bull_cross = (prev_sma68 <= prev_sma90) and (sma68 > sma90)
    bear_cross = (prev_sma68 >= prev_sma90) and (sma68 < sma90)
    
    # Distance Calculations
    dist_from_ema = abs(current_close - ema340) / ema340 * 100
    valid_distance = dist_from_ema >= DIST_PCT
    re_entry_dist = dist_from_ema >= RE_ENTRY_DIST_PCT
    
    # 3. Update Trend memory
    if bull_cross:
        state["bullTrend"] = True
        state["bearTrend"] = False
    elif bear_cross:
        state["bearTrend"] = True
        state["bullTrend"] = False
        
    # ──────────────────────────────────────────────
    # 4. Debounce & Post-Exit Reset
    #    Pine Script evaluates these BEFORE pullback arming,
    #    so a dip on the exit candle can still arm re-entry.
    # ──────────────────────────────────────────────
    
    # Opposite cross resets debounce
    if state["lastSignal"] == 1 and bear_cross:
        state["oppositeSeen"] = True
    if state["lastSignal"] == -1 and bull_cross:
        state["oppositeSeen"] = True
    
    # 5. Pullback Memory
    # Arm only on candle close strictly below EMA during a bull trend
    if state["bullTrend"] and below_ema:
        state["longPullbackReady"] = True
    # Arm only on candle close strictly above EMA during a bear trend
    if state["bearTrend"] and above_ema:
        state["shortPullbackReady"] = True
        
    # Reset pullback flags on opposite trend crossovers
    if bear_cross:
        state["longPullbackReady"] = False
    if bull_cross:
        state["shortPullbackReady"] = False
        
    print(f"[Engine] Close={current_close:.2f} | SMA68={sma68:.2f} | SMA90={sma90:.2f} | EMA340={ema340:.2f} | Dist={dist_from_ema:.3f}%")
    print(f"[State] BullTrend={state['bullTrend']} | BearTrend={state['bearTrend']} | L_pullback={state['longPullbackReady']} | S_pullback={state['shortPullbackReady']}")
    print(f"[Debounce] lastSignal={state['lastSignal']} | oppositeSeen={state['oppositeSeen']}")
    
    pos = state["position"]
    
    # ==========================================
    # EXIT LOGIC — Spot-based TSL (matches Pine Script exactly)
    #
    # Phase 1: SL stays FIXED at entry_spot ± 0.75%
    # Phase 2: Once profit >= 0.75%, trail activates and
    #          SL ratchets up/down with candle high/low.
    # Exit trigger: candle LOW breaches SL (long) or
    #               candle HIGH breaches SL (short).
    # ==========================================
    if pos["active"]:
        if pos["option_type"] == "CE":  # ── LONG position ──
            # Check if profit reached activation threshold (0.75%)
            profit_pct = (current_high - pos["entry_spot"]) / pos["entry_spot"] * 100
            if not pos.get("trail_active", False) and profit_pct >= TRAIL_PCT:
                pos["trail_active"] = True
                pos["trail_high"] = current_high
            
            if pos.get("trail_active", False):
                # Trail is active: ratchet SL upward
                pos["trail_high"] = max(pos["trail_high"], current_high)
                dynamic_sl = pos["trail_high"] * (1 - TRAIL_PCT / 100)
                pos["stop_loss"] = max(pos["stop_loss"], dynamic_sl)
            else:
                # Trail not yet active: SL stays fixed at entry - 0.75%
                pos["stop_loss"] = pos["entry_spot"] * (1 - SL_PCT / 100)
            
            print(f"[TSL LONG] Entry={pos['entry_spot']:.2f} | TrailActive={pos.get('trail_active', False)} | Peak={pos['trail_high']:.2f} | SL={pos['stop_loss']:.2f} | Low={current_low:.2f} | Close={current_close:.2f}")
            
            # Exit if candle LOW breaches SL (Pine: if low <= stopLoss)
            if current_low <= pos["stop_loss"]:
                if pos["symbol"] == "HISTORICAL_CE":
                    print("[Exit] Historical sync position exited. No real broker order sent.")
                    opt_exit_price = 0.0
                    success = True
                else:
                    opt_exit_price = fetch_option_quote(client, pos["symbol"])
                    success = execute_market_order(client, pos["symbol"], "SELL")
                
                if success:
                    exit_reason = "TRAIL_EXIT" if pos.get("trail_active", False) else "INITIAL_SL_EXIT"
                    log_trade("EXIT", pos["symbol"], opt_exit_price, current_close, state, exit_reason=exit_reason)
                    log_decision(state, "TSL EXIT LONG", f"Candle Low {current_low:.2f} <= SL {pos['stop_loss']:.2f} ({exit_reason})", current_close,
                                 details={"entry_spot": pos["entry_spot"], "entry_type": pos.get("entry_type", ""), "trail_active": pos.get("trail_active", False)})
                    
                    # Reset position & debounce (Pine lines 89-95)
                    _reset_position(pos)
                    state["lastSignal"] = 0
                    state["oppositeSeen"] = True
                    if state["bullTrend"]:
                        state["longPullbackReady"] = False
                        
        elif pos["option_type"] == "PE":  # ── SHORT position ──
            # Check if profit reached activation threshold (0.75%)
            profit_pct = (pos["entry_spot"] - current_low) / pos["entry_spot"] * 100
            if not pos.get("trail_active", False) and profit_pct >= TRAIL_PCT:
                pos["trail_active"] = True
                pos["trail_low"] = current_low
            
            if pos.get("trail_active", False):
                # Trail is active: ratchet SL downward
                pos["trail_low"] = min(pos["trail_low"], current_low)
                dynamic_sl = pos["trail_low"] * (1 + TRAIL_PCT / 100)
                pos["stop_loss"] = min(pos["stop_loss"], dynamic_sl)
            else:
                # Trail not yet active: SL stays fixed at entry + 0.75%
                pos["stop_loss"] = pos["entry_spot"] * (1 + SL_PCT / 100)
            
            print(f"[TSL SHORT] Entry={pos['entry_spot']:.2f} | TrailActive={pos.get('trail_active', False)} | Trough={pos['trail_low']:.2f} | SL={pos['stop_loss']:.2f} | High={current_high:.2f} | Close={current_close:.2f}")
            
            # Exit if candle HIGH breaches SL (Pine: if high >= stopLoss)
            if current_high >= pos["stop_loss"]:
                if pos["symbol"] == "HISTORICAL_PE":
                    print("[Exit] Historical sync position exited. No real broker order sent.")
                    opt_exit_price = 0.0
                    success = True
                else:
                    opt_exit_price = fetch_option_quote(client, pos["symbol"])
                    success = execute_market_order(client, pos["symbol"], "SELL")
                
                if success:
                    exit_reason = "TRAIL_EXIT" if pos.get("trail_active", False) else "INITIAL_SL_EXIT"
                    log_trade("EXIT", pos["symbol"], opt_exit_price, current_close, state, exit_reason=exit_reason)
                    log_decision(state, "TSL EXIT SHORT", f"Candle High {current_high:.2f} >= SL {pos['stop_loss']:.2f} ({exit_reason})", current_close,
                                 details={"entry_spot": pos["entry_spot"], "entry_type": pos.get("entry_type", ""), "trail_active": pos.get("trail_active", False)})
                    
                    # Reset position & debounce (Pine lines 89-95)
                    _reset_position(pos)
                    state["lastSignal"] = 0
                    state["oppositeSeen"] = True
                    if state["bearTrend"]:
                        state["shortPullbackReady"] = False
                        
    # ==========================================
    # ENTRY LOGIC (Only if no active position)
    # ==========================================
    if not pos["active"]:
        expiry = get_expiry(client)
        if not expiry:
            print("[Engine Error] Could not retrieve expiry date. Skipping entry evaluation.")
            return
            
        # A. Evaluate LONG Entry Setup
        long_base = bull_cross and above_ema and valid_distance and (state["lastSignal"] != 1 or state["oppositeSeen"])
        long_reentry = state["bullTrend"] and state["longPullbackReady"] and above_ema and re_entry_dist
        
        if long_base or long_reentry:
            reason = "BASE_ENTRY" if long_base else "SMART_REENTRY"
            print(f"[SIGNAL] Triggered LONG {reason}!")
            
            # Fetch weekly Call option priced closest to Rs. 200
            opt_symbol, opt_price = find_option_near_200(client, expiry, "CE")
            if opt_symbol:
                success = execute_market_order(client, opt_symbol, "BUY")
                if success:
                    # Update State variables (Pine lines 170-174)
                    state["lastSignal"] = 1
                    state["oppositeSeen"] = False
                    state["longPullbackReady"] = False
                    
                    pos["active"] = True
                    pos["symbol"] = opt_symbol
                    pos["option_type"] = "CE"
                    pos["direction"] = "LONG"
                    pos["entry_type"] = reason
                    pos["entry_spot"] = current_close
                    pos["entry_option_price"] = opt_price
                    pos["entry_time"] = datetime.now().isoformat()
                    pos["trail_high"] = current_high
                    pos["trail_low"] = 0.0
                    pos["stop_loss"] = current_close * (1.0 - SL_PCT / 100)
                    pos["trail_active"] = False
                    pos["lot_size"] = LOT_SIZE
                    
                    log_trade("ENTRY", opt_symbol, opt_price, current_close, state)
                    log_decision(state, "ENTRY LONG", f"Triggered by {reason}. Spot={current_close:.2f}, Opt={opt_symbol} (@{opt_price})", current_close)
            else:
                print("[Engine Error] Could not find a suitable Call option closest to Rs. 200.")
                
        # B. Evaluate SHORT Entry Setup
        short_base = bear_cross and below_ema and valid_distance and (state["lastSignal"] != -1 or state["oppositeSeen"])
        short_reentry = state["bearTrend"] and state["shortPullbackReady"] and below_ema and re_entry_dist
        
        if short_base or short_reentry:
            reason = "BASE_ENTRY" if short_base else "SMART_REENTRY"
            print(f"[SIGNAL] Triggered SHORT {reason}!")
            
            # Fetch weekly Put option priced closest to Rs. 200
            opt_symbol, opt_price = find_option_near_200(client, expiry, "PE")
            if opt_symbol:
                success = execute_market_order(client, opt_symbol, "BUY")
                if success:
                    # Update State variables (Pine lines 176-180)
                    state["lastSignal"] = -1
                    state["oppositeSeen"] = False
                    state["shortPullbackReady"] = False
                    
                    pos["active"] = True
                    pos["symbol"] = opt_symbol
                    pos["option_type"] = "PE"
                    pos["direction"] = "SHORT"
                    pos["entry_type"] = reason
                    pos["entry_spot"] = current_close
                    pos["entry_option_price"] = opt_price
                    pos["entry_time"] = datetime.now().isoformat()
                    pos["trail_high"] = 0.0
                    pos["trail_low"] = current_low
                    pos["stop_loss"] = current_close * (1.0 + SL_PCT / 100)
                    pos["trail_active"] = False
                    pos["lot_size"] = LOT_SIZE
                    
                    log_trade("ENTRY", opt_symbol, opt_price, current_close, state)
                    log_decision(state, "ENTRY SHORT", f"Triggered by {reason}. Spot={current_close:.2f}, Opt={opt_symbol} (@{opt_price})", current_close)
            else:
                print("[Engine Error] Could not find a suitable Put option closest to Rs. 200.")

    # Save all updates
    save_state(state)

# ============================================================
# 7. MAIN RUNTIME LOOP
# ============================================================
def _reset_position(pos):
    """Clear all position fields back to defaults after exit."""
    pos["active"] = False
    pos["symbol"] = ""
    pos["option_type"] = ""
    pos["direction"] = ""
    pos["entry_type"] = ""
    pos["entry_spot"] = 0.0
    pos["entry_option_price"] = 0.0
    pos["entry_time"] = ""
    pos["trail_high"] = 0.0
    pos["trail_low"] = 0.0
    pos["stop_loss"] = 0.0
    pos["trail_active"] = False
    pos["lot_size"] = 0


def sync_historical_state(client, state):
    """
    On startup, simulate the strategy logic step-by-step over historical candles
    to warm up the trend flags and detect if we should currently be in an active trade.
    """
    if state["position"]["active"]:
        print("[Startup Sync] Position is already active in local state JSON. Skipping sync.")
        return

    print("[Startup Sync] Starting 5-day historical state synchronization...")
    candles = fetch_candles(client)
    if not candles:
        print("[Startup Sync] No candle history found. Waking up fresh.")
        return

    # Simulate strategy rules step-by-step
    temp_state = {
        "bullTrend": False,
        "bearTrend": False,
        "longPullbackReady": False,
        "shortPullbackReady": False,
        "lastSignal": 0,
        "oppositeSeen": True,
        "position": {
            "active": False,
            "symbol": "",
            "option_type": "",
            "direction": "",
            "entry_type": "",
            "entry_spot": 0.0,
            "entry_option_price": 0.0,
            "entry_time": "",
            "trail_high": 0.0,
            "trail_low": 0.0,
            "stop_loss": 0.0,
            "trail_active": False,
            "lot_size": 0
        }
    }

    closes_all = [float(c["close"]) for c in candles]
    highs_all = [float(c["high"]) for c in candles]
    lows_all = [float(c["low"]) for c in candles]

    for i in range(len(candles)):
        if i < EMA_TREND_LEN:
            continue

        closes = closes_all[:i+1]
        highs = highs_all[:i+1]
        lows = lows_all[:i+1]

        current_close = closes[-1]
        current_high = highs[-1]
        current_low = lows[-1]

        sma68 = calculate_sma(closes, SMA_FAST_LEN)
        sma90 = calculate_sma(closes, SMA_SLOW_LEN)
        ema340 = calculate_ema(closes, EMA_TREND_LEN)

        prev_sma68 = calculate_sma(closes[:-1], SMA_FAST_LEN)
        prev_sma90 = calculate_sma(closes[:-1], SMA_SLOW_LEN)

        bull_cross = (prev_sma68 <= prev_sma90) and (sma68 > sma90)
        bear_cross = (prev_sma68 >= prev_sma90) and (sma68 < sma90)

        above_ema = current_close > ema340
        below_ema = current_close < ema340

        dist_from_ema = abs(current_close - ema340) / ema340 * 100
        valid_distance = dist_from_ema >= DIST_PCT
        re_entry_dist = dist_from_ema >= RE_ENTRY_DIST_PCT

        # 1. Update Trend Memory
        if bull_cross:
            temp_state["bullTrend"] = True
            temp_state["bearTrend"] = False
        elif bear_cross:
            temp_state["bearTrend"] = True
            temp_state["bullTrend"] = False

        # 2. Debounce Opposite Crosses
        if temp_state["lastSignal"] == 1 and bear_cross:
            temp_state["oppositeSeen"] = True
        if temp_state["lastSignal"] == -1 and bull_cross:
            temp_state["oppositeSeen"] = True

        # 3. Pullback Memory
        if temp_state["bullTrend"] and below_ema:
            temp_state["longPullbackReady"] = True
        if temp_state["bearTrend"] and above_ema:
            temp_state["shortPullbackReady"] = True

        if bear_cross:
            temp_state["longPullbackReady"] = False
        if bull_cross:
            temp_state["shortPullbackReady"] = False

        pos = temp_state["position"]

        # 4. Exit Check (SL/TSL)
        if pos["active"]:
            if pos["option_type"] == "CE":
                profit_pct = (current_high - pos["entry_spot"]) / pos["entry_spot"] * 100
                if not pos.get("trail_active") and profit_pct >= TRAIL_PCT:
                    pos["trail_active"] = True
                    pos["trail_high"] = current_high

                if pos.get("trail_active"):
                    pos["trail_high"] = max(pos["trail_high"], current_high)
                    dynamic_sl = pos["trail_high"] * (1 - TRAIL_PCT / 100)
                    pos["stop_loss"] = max(pos["stop_loss"], dynamic_sl)
                else:
                    pos["stop_loss"] = pos["entry_spot"] * (1 - SL_PCT / 100)

                if current_low <= pos["stop_loss"]:
                    _reset_position(pos)
                    temp_state["lastSignal"] = 0
                    temp_state["oppositeSeen"] = True
                    if temp_state["bullTrend"]:
                        temp_state["longPullbackReady"] = False

            elif pos["option_type"] == "PE":
                profit_pct = (pos["entry_spot"] - current_low) / pos["entry_spot"] * 100
                if not pos.get("trail_active") and profit_pct >= TRAIL_PCT:
                    pos["trail_active"] = True
                    pos["trail_low"] = current_low

                if pos.get("trail_active"):
                    pos["trail_low"] = min(pos["trail_low"], current_low)
                    dynamic_sl = pos["trail_low"] * (1 + TRAIL_PCT / 100)
                    pos["stop_loss"] = min(pos["stop_loss"], dynamic_sl)
                else:
                    pos["stop_loss"] = pos["entry_spot"] * (1 + SL_PCT / 100)

                if current_high >= pos["stop_loss"]:
                    _reset_position(pos)
                    temp_state["lastSignal"] = 0
                    temp_state["oppositeSeen"] = True
                    if temp_state["bearTrend"]:
                        temp_state["shortPullbackReady"] = False

        # 5. Entry Check (only if not active)
        if not pos["active"]:
            c = candles[i]
            c_time = parse_candle_time(c)
            in_time_gate = "09:15" <= c_time.strftime("%H:%M") <= "15:30"

            long_base = bull_cross and above_ema and valid_distance and (temp_state["lastSignal"] != 1 or temp_state["oppositeSeen"])
            long_reentry = temp_state["bullTrend"] and temp_state["longPullbackReady"] and above_ema and re_entry_dist

            short_base = bear_cross and below_ema and valid_distance and (temp_state["lastSignal"] != -1 or temp_state["oppositeSeen"])
            short_reentry = temp_state["bearTrend"] and temp_state["shortPullbackReady"] and below_ema and re_entry_dist

            if (long_base or long_reentry) and in_time_gate:
                pos["active"] = True
                pos["symbol"] = "HISTORICAL_CE"
                pos["option_type"] = "CE"
                pos["direction"] = "LONG"
                pos["entry_type"] = "BASE_ENTRY" if long_base else "SMART_REENTRY"
                pos["entry_spot"] = current_close
                pos["entry_option_price"] = TARGET_PREMIUM
                pos["entry_time"] = c_time.isoformat()
                pos["trail_high"] = current_high
                pos["trail_low"] = 0.0
                pos["stop_loss"] = current_close * (1.0 - SL_PCT / 100)
                pos["trail_active"] = False
                pos["lot_size"] = LOT_SIZE

                temp_state["lastSignal"] = 1
                temp_state["oppositeSeen"] = False
                temp_state["longPullbackReady"] = False

            elif (short_base or short_reentry) and in_time_gate:
                pos["active"] = True
                pos["symbol"] = "HISTORICAL_PE"
                pos["option_type"] = "PE"
                pos["direction"] = "SHORT"
                pos["entry_type"] = "BASE_ENTRY" if short_base else "SMART_REENTRY"
                pos["entry_spot"] = current_close
                pos["entry_option_price"] = TARGET_PREMIUM
                pos["entry_time"] = c_time.isoformat()
                pos["trail_high"] = 0.0
                pos["trail_low"] = current_low
                pos["stop_loss"] = current_close * (1.0 + SL_PCT / 100)
                pos["trail_active"] = False
                pos["lot_size"] = LOT_SIZE

                temp_state["lastSignal"] = -1
                temp_state["oppositeSeen"] = False
                temp_state["shortPullbackReady"] = False

    # Sync warmed-up trend memory flags to main state
    state["bullTrend"] = temp_state["bullTrend"]
    state["bearTrend"] = temp_state["bearTrend"]
    state["longPullbackReady"] = temp_state["longPullbackReady"]
    state["shortPullbackReady"] = temp_state["shortPullbackReady"]
    state["lastSignal"] = temp_state["lastSignal"]
    state["oppositeSeen"] = temp_state["oppositeSeen"]

    # Verify if active position is within 5 days limit
    pos = temp_state["position"]
    if pos["active"]:
        entry_dt = datetime.fromisoformat(pos["entry_time"])
        cutoff_dt = datetime.now() - timedelta(days=5)
        if entry_dt >= cutoff_dt:
            state["position"] = pos
            print(f"[Startup Sync] Found active historical {pos['direction']} trade entered on {pos['entry_time']} at Spot {pos['entry_spot']:.2f}.")
            print(f"[Startup Sync] Current Trailing SL: {pos['stop_loss']:.2f}. Syncing state to monitor and wait for exit.")
        else:
            print(f"[Startup Sync] Active historical trade found from {pos['entry_time']} but it is older than 5 days. Skipping position sync.")
    else:
        print("[Startup Sync] No active historical trade found within lookback window. Trend memory warmed up.")

    save_state(state)


def run():
    print("============================================================")
    print(" Nifty 5m Triple Confirm Algorithmic Trading Engine v5      ")
    print("============================================================")
    print(f"  API Host    : {HOST}")
    print(f"  Timeframe   : 5 Minutes (Sync'd to candle boundaries)")
    print(f"  Target Prem : Rs. {TARGET_PREMIUM}")
    print(f"  Spot SL/TSL : {SL_PCT}% / {TRAIL_PCT}% (trail activates at >= {TRAIL_PCT}% profit)")
    print("============================================================")
    
    client = api(api_key=API_KEY, host=HOST)
    state = load_state()
    sync_historical_state(client, state)
    
    # Core loop synced to 5-minute candle boundaries
    while True:
        try:
            now = datetime.now()
            t_str = now.strftime("%H:%M")
            
            # Operational market hours check (09:15 - 15:30)
            if not ("09:15" <= t_str <= "15:30"):
                time.sleep(30)
                continue
                
            # Execute evaluations exactly 2 seconds after every 5-minute candle close
            # e.g., 09:20:02, 09:25:02, etc. (allows broker history data to refresh)
            if (now.minute % 5 == 0) and (0 <= now.second <= 5):
                print(f"\n--- [Candle Close Boundary] {now.strftime('%Y-%m-%d %H:%M:%S')} ---")
                
                # Run core strategy math & state machine
                evaluate_strategy_rules(client, state)
                
                # Sleep past the boundary to prevent duplicate triggers in the same minute
                time.sleep(10)
                
            time.sleep(1)
            
        except Exception as e:
            print(f"[Loop Exception] {e}")
            time.sleep(5)

if __name__ == "__main__":
    run()
