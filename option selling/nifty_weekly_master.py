"""
Nifty Weekly Master Strategy — PRECISION VERSION
================================================
Paper Trading / Production Ready Logic:
  - Wed: Double BWB Carry + Record Wed Close
  - Mon: True Weekend Gap Check (>0.5% skip/wait)
  - Mon: ADX Trending vs Ranging Filter
  - Mon: 50pt ITM Breach Hard Exit for Straddles
  - Tue: Expiry IC (OTM6/OTM10) — Ride to 3PM

Risk: Global Weekly Circuit Breaker (3x Avg Prem)
Loop: 300s (5min)
"""

import os
import json
import time
import csv
import math
import re
from datetime import datetime, date
from openalgo import api

# ============================================================
# 1. PARAMETERS & CONFIG
# ============================================================
API_KEY          = "ff1d258f2c5d48bf87292b3f055b39772ca76f4afe69ff7c7c5fa121a32334d8"
HOST             = "http://127.0.0.1:5000"

UNDERLYING       = "NIFTY"
EXCHANGE_INDEX   = "NSE_INDEX"
EXCHANGE_DERIV   = "NFO"
PRODUCT          = "NRML"       # 'NRML' for Carry Forward Positions
LOT_SIZE         = 65
STRIKE_STEP      = 50

# ADX Thresholds
ADX_PERIOD       = 14
ADX_TRENDING     = 24.95
ADX_RANGING      = 20
GAP_THRESHOLD    = 0.5  # 0.5% Weekend Gap

WEEKLY_SL_MULT   = 3.0
AVG_WEEKLY_PREM  = 500 * LOT_SIZE
DYNAMIC_CAPITAL  = 200000  # Capital used for scaling
RISK_PER_WEEK    = 0.02      # 2% Risk per week of Capital
WEEKLY_LIMIT     = DYNAMIC_CAPITAL * RISK_PER_WEEK # Dynamic Limit: 4k for 2L capital

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
STATE_FILE       = os.path.join(SCRIPT_DIR, "nifty_master_state.json")
TRADE_LOG        = os.path.join(SCRIPT_DIR, "nifty_master_trades.csv")
AUDIT_LOG        = os.path.join(SCRIPT_DIR, "nifty_master_audit.csv")

# ============================================================
# 2. TECHNICAL INDICATORS (WILDER'S ADX)
# ============================================================
def calculate_adx(client, period=14):
    """Manual calculation of Wilder's ADX to match charting standards without external libs"""
    try:
        from datetime import timedelta
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=30)
        
        # Use 1h history (Trend Detection Standard)
        hist = client.history(symbol=UNDERLYING, exchange=EXCHANGE_INDEX, interval="1h", 
                             start_date=start_dt.strftime("%Y-%m-%d"), 
                             end_date=end_dt.strftime("%Y-%m-%d"))
        
        if hasattr(hist, "to_dict"): # Handle Pandas DataFrame
            candles = hist.to_dict("records")
        else:
            candles = hist if isinstance(hist, list) else hist.get("data", [])
        
        if len(candles) < (period * 2 + 5): return 0.0
        
        tr, plus_dm, minus_dm = [], [], []
        for i in range(1, len(candles)):
            prev, curr = candles[i-1], candles[i]
            # True Range
            tr.append(max(curr['high'] - curr['low'], abs(curr['high'] - prev['close']), abs(curr['low'] - prev['close'])))
            # Directional Movement
            up = curr['high'] - prev['high']
            down = prev['low'] - curr['low']
            plus_dm.append(up if up > down and up > 0 else 0)
            minus_dm.append(down if down > up and down > 0 else 0)

        def wilder_smooth(data, n):
            # First value is the sum of the first n elements
            res = [sum(data[:n])]
            # Subsequent values use Wilder's Smoothing formula: Sum = PrevSum - (PrevSum/n) + CurrVal
            for val in data[n:]:
                res.append(res[-1] - (res[-1] / n) + val)
            return res

        # 1. Smooth TR, +DM, -DM
        atr_s = wilder_smooth(tr, period)
        pdm_s = wilder_smooth(plus_dm, period)
        mdm_s = wilder_smooth(minus_dm, period)
        
        # 2. Calculate +DI and -DI
        pdi = [100 * (p / t) if t != 0 else 0 for p, t in zip(pdm_s, atr_s)]
        mdi = [100 * (m / t) if t != 0 else 0 for m, t in zip(mdm_s, atr_s)]
        
        # 3. Calculate DX
        dx = [100 * abs(p - m) / (p + m) if (p + m) != 0 else 0 for p, m in zip(pdi, mdi)]
        
        # 4. Final ADX is the average (sum/period) of the smoothed DX
        adx_s = wilder_smooth(dx, period)
        return round(adx_s[-1] / period, 2)

    except Exception as e:
        print(f"[ADX Error] {e}")
        return 0.0

def calculate_max_pain(chain_data):
    """Calculate Max Pain strike — where total intrinsic value of all options is minimized."""
    strikes = []
    for item in chain_data:
        strike = item.get("strike", 0)
        if strike <= 0: continue
        ce_oi = item.get("ce", {}).get("oi", 0) or item.get("ce", {}).get("volume", 0)
        pe_oi = item.get("pe", {}).get("oi", 0) or item.get("pe", {}).get("volume", 0)
        strikes.append({"strike": strike, "ce_oi": ce_oi, "pe_oi": pe_oi})
    if not strikes: return 0
    min_pain = float('inf')
    max_pain_strike = 0
    for test in strikes:
        pain = 0
        for s in strikes:
            if test["strike"] > s["strike"]:
                pain += (test["strike"] - s["strike"]) * s["ce_oi"]
            if test["strike"] < s["strike"]:
                pain += (s["strike"] - test["strike"]) * s["pe_oi"]
        if pain < min_pain:
            min_pain = pain
            max_pain_strike = test["strike"]
    return max_pain_strike

def analyze_option_chain(client, expiry):
    """Unified option chain analysis: PCR, OI distribution, Max Pain, key levels."""
    result = {"pcr": 1.0, "oi_data": [], "key_levels": {}, "max_pain": 0}
    try:
        res = client.optionchain(underlying=UNDERLYING, exchange=EXCHANGE_DERIV, expiry_date=expiry, strike_count=12)
        chain_data = res.get("chain", [])
        total_ce_oi = sum(item.get("ce", {}).get("oi", 0) for item in chain_data)
        total_pe_oi = sum(item.get("pe", {}).get("oi", 0) for item in chain_data)
        use_volume = (total_ce_oi == 0 and total_pe_oi == 0)
        if use_volume:
            total_ce_oi = sum(item.get("ce", {}).get("volume", 0) for item in chain_data)
            total_pe_oi = sum(item.get("pe", {}).get("volume", 0) for item in chain_data)
        result["pcr"] = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

        oi_field = "volume" if use_volume else "oi"
        oi_data = []
        for item in chain_data:
            strike = item.get("strike", 0)
            ce_oi = item.get("ce", {}).get(oi_field, 0)
            pe_oi = item.get("pe", {}).get(oi_field, 0)
            oi_data.append({"strike": strike, "ce_oi": ce_oi, "pe_oi": pe_oi, "total_oi": ce_oi + pe_oi})
        result["oi_data"] = oi_data

        result["max_pain"] = calculate_max_pain(chain_data)

        max_ce = max(oi_data, key=lambda x: x["ce_oi"], default={"strike": 0, "ce_oi": 0})
        max_pe = max(oi_data, key=lambda x: x["pe_oi"], default={"strike": 0, "pe_oi": 0})
        result["key_levels"] = {
            "max_pain": result["max_pain"],
            "ce_wall": max_ce["strike"], "ce_wall_oi": max_ce["ce_oi"],
            "pe_wall": max_pe["strike"], "pe_wall_oi": max_pe["pe_oi"],
        }
    except Exception as e:
        print(f"[Chain Analysis Error] {e}")
    return result

def calculate_ivr(client, state, vix_ltp):
    """
    Calculate IVR based on dynamic trailing 365-day VIX range and current India VIX.
    Caches historical boundaries in the state JSON once per day to optimize API overhead.
    """
    try:
        # 1. Maintain VIX historical min and max for trailing 365 days
        today_str = str(date.today())
        if state.get("vix_history_date") != today_str or not state.get("vix_min") or not state.get("vix_max"):
            from datetime import timedelta
            end_dt = date.today()
            start_dt = end_dt - timedelta(days=365)
            
            print(f"[IVR] Fetching trailing 365-day INDIAVIX history for baseline range...")
            hist = client.history(
                symbol="INDIAVIX", exchange="NSE_INDEX", interval="1h",
                start_date=start_dt.strftime("%Y-%m-%d"),
                end_date=end_dt.strftime("%Y-%m-%d")
            )
            
            if hasattr(hist, "to_dict"):
                candles = hist.to_dict("records")
            else:
                candles = hist if isinstance(hist, list) else hist.get("data", [])
            
            if candles:
                closes = [float(c.get('close', c.get('ltp', 0))) for c in candles if c.get('close') or c.get('ltp')]
                closes = [c for c in closes if c > 0]
                if closes:
                    state["vix_min"] = min(closes)
                    state["vix_max"] = max(closes)
                    state["vix_history_date"] = today_str
                    print(f"[IVR] Updated historical VIX bounds: Min={state['vix_min']:.2f}, Max={state['vix_max']:.2f}")
            
        vix_min = state.get("vix_min", 10.0)
        vix_max = state.get("vix_max", 30.0)

        # 2. Calculate dynamic VIX IV Rank (apples-to-apples)
        if vix_max > vix_min:
            ivr = (vix_ltp - vix_min) / (vix_max - vix_min) * 100
            ivr = max(0.0, min(100.0, ivr))
        else:
            ivr = 50.0

        print(f"[IVR] Current VIX: {vix_ltp:.2f} | Range: [{vix_min:.2f} - {vix_max:.2f}] | Dynamic IVR: {ivr:.2f}%")
        return round(ivr, 2)
        
    except Exception as e:
        print(f"[IVR Calculation Error] {e}")
        return 25.0


# ============================================================
# 2b. DECISION AUDIT & TRADE JOURNAL
# ============================================================
def log_decision(state, slot_name, action, reason, gates=None):
    """Log a strategy decision with gate pass/fail results."""
    if "decision_log" not in state:
        state["decision_log"] = []
    entry = {
        "time": datetime.now().strftime("%H:%M"),
        "day": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][datetime.now().weekday()],
        "slot": slot_name,
        "action": action,
        "reason": reason,
        "gates": gates or {}
    }
    state["decision_log"].append(entry)
    state["decision_log"] = state["decision_log"][-30:] # Keep more for UI
    
    # PERMANENT LOGGING
    try:
        file_exists = os.path.exists(AUDIT_LOG)
        with open(AUDIT_LOG, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["timestamp","day","slot","action","reason","gates"])
            writer.writerow([
                datetime.now().isoformat(),
                entry["day"], slot_name, action, reason,
                json.dumps(gates or {})
            ])
    except: pass

def log_trade(slot_name, trade_type, action, legs, premium, state, exit_reason=""):
    """Append trade entry/exit to CSV journal."""
    try:
        file_exists = os.path.exists(TRADE_LOG)
        with open(TRADE_LOG, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp","day","slot","type","action","premium",
                    "pnl","vix","adx","pcr","ivr","nifty_ltp","exit_reason","legs"
                ])
            md = state.get("market_data", {})
            writer.writerow([
                datetime.now().isoformat(),
                ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][datetime.now().weekday()],
                slot_name, trade_type, action, round(premium, 2),
                "" if action == "ENTRY" else round(premium, 0),
                md.get("vix",0), md.get("adx",0), md.get("pcr",0),
                md.get("ivr",0), md.get("nifty_ltp",0), exit_reason,
                json.dumps([{"s":l["symbol"],"a":l["action"],"q":l["qty"]} for l in legs])
            ])
    except Exception as e:
        print(f"[Trade Log Error] {e}")

# ============================================================
# 2c. BLACK-SCHOLES GREEKS
# ============================================================
def _norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def _norm_pdf(x):
    return math.exp(-x**2 / 2) / math.sqrt(2 * math.pi)

def bs_greeks(spot, strike, tte_days, iv_pct, r=0.065, is_call=True):
    """Black-Scholes Greeks for a single option leg."""
    if tte_days <= 0 or iv_pct <= 0 or spot <= 0 or strike <= 0:
        return {"delta": 0, "theta": 0, "vega": 0}
    t = tte_days / 365.0
    sigma = iv_pct / 100.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (r + sigma**2 / 2) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    gamma = _norm_pdf(d1) / (spot * sigma * sqrt_t)
    vega = spot * _norm_pdf(d1) * sqrt_t / 100
    if is_call:
        delta = _norm_cdf(d1)
        theta = (-(spot * _norm_pdf(d1) * sigma) / (2 * sqrt_t) - r * strike * math.exp(-r*t) * _norm_cdf(d2)) / 365
    else:
        delta = _norm_cdf(d1) - 1
        theta = (-(spot * _norm_pdf(d1) * sigma) / (2 * sqrt_t) + r * strike * math.exp(-r*t) * _norm_cdf(-d2)) / 365
    return {"delta": round(delta, 4), "theta": round(theta, 2), "vega": round(vega, 2)}

def calculate_position_greeks(slot, nifty_ltp, vix, days_to_expiry):
    """Aggregate Greeks for all legs of an active position."""
    if not slot.get("active") or not slot.get("legs"):
        return {"delta": 0, "theta": 0, "vega": 0}
    total = {"delta": 0.0, "theta": 0.0, "vega": 0.0}
    for leg in slot["legs"]:
        symbol = leg["symbol"]
        qty = leg["qty"]
        is_call = symbol.endswith("CE")
        # Extract strike number from symbol tail (e.g. ...24650CE → 24650)
        # Extract strike number using regex (handles variable expiry length)
        import re
        strike_match = re.search(r'(\d+)(?:CE|PE)$', symbol)
        if not strike_match: continue
        try:
            strike = int(strike_match.group(1))
        except ValueError:
            continue
        g = bs_greeks(nifty_ltp, strike, days_to_expiry, vix, is_call=is_call)
        mult = qty if leg["action"] == "BUY" else -qty
        total["delta"] += g["delta"] * mult
        total["theta"] += g["theta"] * mult
        total["vega"]  += g["vega"]  * mult
    return {k: round(v, 2) for k, v in total.items()}

# ============================================================
# 3. STATE MANAGEMENT
# ============================================================
def empty_slot():
    return {
        "active": False, 
        "legs": [], 
        "premium_collected": 0.0, 
        "strike": 0, 
        "type": "",
        "entry_time": "",
        "intraday_only": False
    }

def load_state():
    week_id = date.today().strftime("%Y-%W")
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                s = json.load(f)
                if s.get("current_week") == week_id: return s
        except: pass
    return {
        "current_week": week_id,
        "weekly_pnl": 0.0,
        "week_blocked": False,
        "wednesday_close": 0.0,
        "monday_close": 0.0,
        "morning_pcr": 0.0,
        "morning_pcr_date": "",
        "weekly_limit": 0,
        "adjustment_signals": [],
        "market_data": {"vix": 0, "ivr": 0, "adx": 0, "pcr": 1.0, "nifty_ltp": 0},
        "carry_trade": empty_slot(),
        "monday_trade": empty_slot(),
        "tuesday_trade": empty_slot(),
        "last_eval": ""
    }

def save_state(state):
    state["last_eval"] = datetime.now().strftime("%H:%M:%S")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ============================================================
# 4. ORDER & RISK TOOLS
# ============================================================
client = api(api_key=API_KEY, host=HOST)
print(f"[DEBUG] Client initialized with Host: {HOST}")
print(f"[DEBUG] API Key (first 4): {API_KEY[:4]}...")

def batch_get_quotes(client, symbols):
    """Fetch multiple quotes using SDK-compliant list of dicts"""
    if not symbols: return {}
    try:
        instruments = [{"symbol": s, "exchange": EXCHANGE_DERIV} for s in symbols]
        res = client.multiquotes(symbols=instruments)
        results = res.get("results", [])
        
        quotes = {}
        for item in results:
            sym = item.get("symbol")
            # Try both possible locations for LTP in SDK response
            ltp = item.get("ltp") or item.get("data", {}).get("ltp", 0)
            quotes[sym] = float(ltp)
        return quotes
    except Exception as e:
        print(f"[Batch Quote Error] {e}")
        return {}

def calculate_premium_collected(client, legs):
    """Calculate net premium (credit/debit) for a set of legs using Batch Quotes"""
    if not legs: return 0.0
    symbols = [l["symbol"] for l in legs]
    price_map = batch_get_quotes(client, symbols)
    total = 0.0
    for leg in legs:
        ltp = price_map.get(leg["symbol"], 0)
        if leg["action"] == "SELL":
            total += ltp * leg["qty"]
        else:
            total -= ltp * leg["qty"]
    return total

def execute_basket(legs, tag, client):
    """Execute multiple legs in one Batch call"""
    if not legs: return 0.0
    try:
        # Build orders for basket
        orders = []
        for leg in legs:
            orders.append({
                "symbol": leg["symbol"],
                "action": leg["action"],
                "exchange": EXCHANGE_DERIV,
                "pricetype": "MARKET",
                "product": PRODUCT,
                "quantity": leg["qty"]
            })
        
        # OpenAlgo SDK: client.basketorder(strategy, orders)
        res = client.basketorder(strategy=tag, orders=orders)
        print(f"[{tag}] Basket executed. Status: {res.get('status')}")
        
        # Calculate premium from the execution results or current quotes
        return calculate_premium_collected(client, legs)
    except Exception as e:
        print(f"[{tag} Basket Error] {e}")
        return 0.0

def exit_slot(slot, tag, client, state=None, exit_reason="SCHEDULED"):
    if not slot["active"]: return
    # Log trade exit before clearing
    if state:
        exit_prem = calculate_premium_collected(client, slot["legs"])
        pnl = slot["premium_collected"] - exit_prem
        log_trade(tag.lower(), slot.get("type",""), "EXIT", slot["legs"], pnl, state, exit_reason)
        log_decision(state, tag.lower(), "EXITED", f"{exit_reason} | PnL Rs.{pnl:.0f}")
    # Flip actions for square-off
    exit_legs = [{"symbol": l["symbol"], "qty": l["qty"], "action": "SELL" if l["action"]=="BUY" else "BUY"} for l in slot["legs"]]
    execute_basket(exit_legs, tag, client)
    slot["active"] = False
    slot["legs"] = []
    slot["premium_collected"] = 0.0

def get_expiry():
    try:
        # Must pass instrumenttype for options
        e = client.expiry(symbol=UNDERLYING, exchange=EXCHANGE_DERIV, instrumenttype="options")
        # Returns list of dates like "28-APR-26", we take first and strip hyphens to get "28APR26"
        data = e if isinstance(e, list) else e.get("data", [])
        if data:
            return data[0].replace("-", "")
        return None
    except: return None

# ============================================================
# 5. SYMBOL BUILDER
# ============================================================
def build_sym(expiry, strike, opt_type):
    """Build option symbol. Format: NIFTY15APR2524500CE"""
    return f"{UNDERLYING}{expiry}{strike}{opt_type}"

def atm_from_ltp(ltp):
    return round(ltp / STRIKE_STEP) * STRIKE_STEP

# ============================================================
# 6. RISK MONITOR — Premium-based TP/SL + ITM Breach
# ============================================================
# Risk params per slot
# tp_pct = exit when profit >= tp_pct * premium_collected
# sl_mult = exit when loss >= sl_mult * premium_collected
RISK_PARAMS = {
    "carry_trade":  {"tp_pct": 0.35, "sl_mult": 1.5},
    "monday_trade": {"tp_pct": 0.40, "sl_mult": 1.2},
    "tuesday_trade": {"tp_pct": 1.0,  "sl_mult": 2.0}, # catastrophic target / 2x SL
}

def calculate_rolling_signals(slot, nifty_ltp, client):
    """Identify if a leg should be rolled due to decay or testing"""
    if not slot["active"] or not slot["legs"]: return []
    
    signals = []
    symbols = [l["symbol"] for l in slot["legs"]]
    quotes = batch_get_quotes(client, symbols)
    
    for leg in slot["legs"]:
        symbol = leg["symbol"]
        ltp = quotes.get(symbol, 0)
        
        # Safety: Derive option_type if missing
        opt_type = leg.get("option_type")
        if not opt_type:
            opt_type = "CE" if symbol.endswith("CE") else "PE"

        # 1. Profit Roll (Decay)
        if leg["action"] == "SELL" and ltp < 5.0 and ltp > 0:
            signals.append({
                "symbol": symbol,
                "type": "ROLL_DECAY",
                "side": opt_type,
                "msg": f"Profit Locked on {opt_type}. Roll to collect more premium."
            })
            
        # 2. Defensive Check (Testing)
        strike_match = re.search(r'(\d+)(?:CE|PE)$', symbol)
        if strike_match:
            strike = int(strike_match.group(1))
            dist = abs(nifty_ltp - strike)
            if dist < 100:
                signals.append({
                    "symbol": symbol,
                    "type": "TESTED",
                    "side": opt_type,
                    "msg": f"{opt_type} Strike {strike} being tested. Consider defensive rolling."
                })
            
    return signals

def check_premium_exit(slot, slot_name, client):
    """Check if current PnL has hit TP or SL using Batch Quotes."""
    if not slot["active"] or slot.get("premium_collected", 0) <= 0:
        return False
    if slot_name not in RISK_PARAMS:
        return False

    collected = slot["premium_collected"]
    
    # --- OPTIMIZED: Fetch all leg prices in one call ---
    symbols = [l["symbol"] for l in slot["legs"]]
    price_map = batch_get_quotes(client, symbols)
    
    current_cost = 0.0
    for leg in slot["legs"]:
        leg_ltp = price_map.get(leg["symbol"], 0)
        
        # --- SAFETY: Skip if data is missing or zero (prevents Fake TP) ---
        if leg_ltp <= 0:
            print(f"[{slot_name.upper()}] Data lag: {leg['symbol']} LTP is {leg_ltp}. Skipping risk check.")
            return False
            
        if leg["action"] == "SELL":
            current_cost += leg_ltp * leg["qty"]
        else:
            current_cost -= leg_ltp * leg["qty"]

    pnl = collected - current_cost
    params = RISK_PARAMS[slot_name]

    # --- TP CHECK REMOVED: User prefers manual exit for profits ---

    if pnl <= -(params["sl_mult"] * collected):
        print(f"[{slot_name.upper()}] STOP LOSS HIT: PnL Rs.{pnl:.0f} <= -{params['sl_mult']}x of Rs.{collected:.0f}")
        exit_slot(slot, slot_name.upper(), client, state=_risk_state, exit_reason="SL_HIT")
        return True

    print(f"[{slot_name.upper()} RISK] Collected: Rs.{collected:.0f} | Current Cost: Rs.{current_cost:.0f} | PnL: Rs.{pnl:.0f}")
    return False

# Module-level ref so check_premium_exit can pass state to exit_slot
_risk_state = None

def monitor_risk(state, nifty_ltp, client):
    """Full risk monitor: weekly CB → ITM breach → premium TP/SL"""
    global _risk_state
    _risk_state = state
    if state["week_blocked"]:
        return

    # --- Weekly Circuit Breaker ---
    if state["weekly_pnl"] < -(WEEKLY_SL_MULT * AVG_WEEKLY_PREM):
        print(f"[CRITICAL] WEEKLY CB BURNT. PnL: Rs.{state['weekly_pnl']:.0f}. SQUARING OFF ALL.")
        state["week_blocked"] = True
        for k in ["carry_trade", "monday_trade", "tuesday_trade"]:
            exit_slot(state[k], k.upper(), client, state=state, exit_reason="WEEKLY_CB")
        return

    # --- ITM Breach Rule for all active slots ---
    for k in ["carry_trade", "monday_trade", "tuesday_trade"]:
        slot = state[k]
        if not slot["active"] or slot["strike"] <= 0:
            continue
        strike = slot["strike"]
        breach = False
        if slot["type"] in ("BUTTERFLY", "STRADDLE"):
            # ATM short: breach if spot moves ±50 from ATM
            breach = nifty_ltp > (strike + 50) or nifty_ltp < (strike - 50)
        elif slot["type"] in ("CONDOR", "BATMAN"):
            # OTM shorts: breach if spot reaches the short leg strike
            off = slot.get("short_offset", 400) # Fallback to 400
            breach = nifty_ltp > (strike + off) or nifty_ltp < (strike - off)
        elif slot["type"] == "RATIO_CALL":
            # Call ratio: only upside risk
            off = slot.get("short_offset", 350)
            breach = nifty_ltp > (strike + off)
        elif slot["type"] == "RATIO_PUT":
            # Put ratio: only downside risk
            off = slot.get("short_offset", 350)
            breach = nifty_ltp < (strike - off)
        if breach:
            print(f"[{k.upper()}] ITM BREACH! Strike={strike}, Spot={nifty_ltp}. HARD EXIT.")
            exit_slot(slot, k.upper(), client, state=state, exit_reason="ITM_BREACH")

    # --- Premium-based TP/SL for carry and monday ---
    for k in ["carry_trade", "monday_trade"]:
        check_premium_exit(state[k], k, client)

    # --- Tuesday: just log using Batch Quotes ---
    if state["tuesday_trade"]["active"]:
        collected = state["tuesday_trade"].get("premium_collected", 0)
        if collected > 0:
            symbols = [l["symbol"] for l in state["tuesday_trade"]["legs"]]
            price_map = batch_get_quotes(client, symbols)
            current_cost = sum(
                price_map.get(l["symbol"], 0) * l["qty"] * (1 if l["action"]=="SELL" else -1)
                for l in state["tuesday_trade"]["legs"]
            )
            pnl = collected - current_cost
            print(f"[TUESDAY] Riding expiry. PnL: Rs.{pnl:.0f} (no auto exit)")

# ============================================================
# 7. STRATEGY DEPLOYMENT FUNCTIONS
# ============================================================

def deploy_wednesday_carry(state, ltp, vix, ivr, adx, pcr, expiry, client):
    """
    Wednesday Carry: Adaptive Regime-Based Strategy Selection.
    """
    gates = {
        "vix_gate": {"passed": vix >= 13, "value": round(vix,1), "threshold": ">=13"},
        "ivr": {"value": round(ivr,0)},
        "adx": {"value": round(adx,1)},
        "pcr": {"value": round(pcr,2)},
    }
    if vix < 13:
        log_decision(state, "carry_trade", "SKIPPED", f"VIX {vix:.1f} < 13 — no premium edge", gates)
        return

    morning_pcr = state.get("morning_pcr", pcr)
    morning_spot = state.get("morning_spot", ltp)
    pcr_shift = pcr - morning_pcr
    spot_shift = ltp - morning_spot

    gates["pcr_shift"] = {"value": round(pcr_shift,2)}
    gates["spot_shift"] = {"value": round(spot_shift,0)}

    atm = atm_from_ltp(ltp)
    t_str = datetime.now().strftime("%H:%M")
    is_eod_window = "15:15" <= t_str <= "15:30"

    print(f"[WED] Regime Detection: ADX={adx:.1f} | IVR={ivr:.0f} | Spot Shift={spot_shift:+.0f} | PCR Shift={pcr_shift:+.2f} | Window={is_eod_window}")


    # --- REGIME 1 & 2: Ranging (Low ADX) - ONLY EOD ---
    if adx < 22:
        if not is_eod_window:
            return # Skip ranging trades until EOD
        if ivr >= 40:
            log_decision(state, "carry_trade", "DEPLOYED", "REGIME 1: Ranging + High IV → Batman IC (OTM5)", gates)
            _deploy_batman(state, atm, expiry, client)
        elif ivr >= 30:
            log_decision(state, "carry_trade", "DEPLOYED", "REGIME 2: Ranging + Med IV → Wide IC (OTM6)", gates)
            _deploy_wide_ic(state, atm, expiry, client)
        else:
            log_decision(state, "carry_trade", "SKIPPED", "Ranging but IVR too low (<30)", gates)

    # --- REGIME 1.5: Grey Zone (Moderate ADX) - ONLY EOD ---
    elif 22 <= adx < 25:
        if not is_eod_window:
            return # Skip grey zone trades until EOD
        log_decision(state, "carry_trade", "DEPLOYED", "REGIME 1.5: Grey Zone (Moderate Trend) → Ultra-Wide IC", gates)
        _deploy_grey_zone_ic(state, atm, expiry, client)

    # --- REGIME 3 & 4: Trending (High ADX) ---
    elif adx >= 25:
        # 1. Standard Confirmation (Trigger anytime)
        if spot_shift >= 50 and pcr_shift > 0.15:
            log_decision(state, "carry_trade", "DEPLOYED", "Bullish Momentum -> Capped CALL Ratio Butterfly", gates)
            _deploy_call_ratio(state, atm, expiry, client)
        elif spot_shift <= -50 and pcr_shift < -0.15:
            log_decision(state, "carry_trade", "DEPLOYED", "Bearish Momentum -> Capped PUT Ratio Butterfly", gates)
            _deploy_put_ratio(state, atm, expiry, client)
        
        # 2. EOD Fallback (Trigger ONLY at 15:15 if no trade yet)
        elif is_eod_window:
            if spot_shift >= 150:
                log_decision(state, "carry_trade", "DEPLOYED", "EOD BULLISH SKEW: Extreme Spot Shift", gates)
                _deploy_skewed_ic(state, atm, expiry, client, bias="BULL")
            elif spot_shift <= -150:
                log_decision(state, "carry_trade", "DEPLOYED", "EOD BEARISH SKEW: Extreme Spot Shift", gates)
                _deploy_skewed_ic(state, atm, expiry, client, bias="BEAR")
            else:
                # Fallback to a Standard Wide IC if the move isn't extreme
                log_decision(state, "carry_trade", "DEPLOYED", "EOD NEUTRAL: Trending but Spot Move < 150", gates)
                _deploy_wide_ic(state, atm, expiry, client)
        
        else:
            log_decision(state, "carry_trade", "SKIPPED", "Trending mid-day: Waiting for Dual Confirmation or EOD Window", gates)


def _deploy_skewed_ic(state, atm, expiry, client, bias="BULL"):
    """
    Skewed Iron Condor for Trending EOD fallback:
    BULL Bias: Sell OTM5 PE / Buy OTM9 PE | Sell OTM7 CE / Buy OTM11 CE
    BEAR Bias: Sell OTM5 CE / Buy OTM9 CE | Sell OTM7 PE / Buy OTM11 PE
    """
    if bias == "BULL":
        p_short, p_hedge = "OTM5", "OTM9"
        c_short, c_hedge = "OTM7", "OTM11"
        s_off = 7 * STRIKE_STEP # Store the wider side for breach monitoring
    else:
        c_short, c_hedge = "OTM5", "OTM9"
        p_short, p_hedge = "OTM7", "OTM11"
        s_off = 7 * STRIKE_STEP

    multi_legs = [
        {"offset": p_short, "option_type": "PE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": p_hedge, "option_type": "PE", "action": "BUY",  "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": c_short, "option_type": "CE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": c_hedge, "option_type": "CE", "action": "BUY",  "quantity": LOT_SIZE, "product": PRODUCT},
    ]
    
    res = client.optionsmultiorder(strategy=f"WED_SKEW_{bias}", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": s_off, "type": "SKEWED_IC",
            "bias": bias, "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", f"SKEW_IC_{bias}", "ENTRY", final_legs, premium, state)
        print(f"[WED] Skewed {bias} IC deployed. Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Skewed IC Failed: {res}")


def _deploy_batman(state, atm, expiry, client):
    """Batman Strategy: Short IC with OTM5 shorts and OTM10 hedges (base 1 lot)."""
    multi_legs = [
        {"offset": "OTM5", "option_type": "PE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM10", "option_type": "PE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM5", "option_type": "CE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM10", "option_type": "CE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
    ]
    res = client.optionsmultiorder(strategy="WED_BATMAN", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        for l in final_legs: print(f"  -> {l['action']} {l['qty']}x {l['symbol']}")
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": 5 * STRIKE_STEP, "type": "BATMAN",
            "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", "BATMAN", "ENTRY", final_legs, premium, state)
        print(f"[WED] Batman deployed. Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Batman Failed: {res}")


def _deploy_wide_ic(state, atm, expiry, client):
    """Wide Iron Condor: OTM6 shorts, OTM10 hedges (base 1 lot)."""
    multi_legs = [
        {"offset": "OTM6", "option_type": "PE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM10", "option_type": "PE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM6", "option_type": "CE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM10", "option_type": "CE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
    ]
    res = client.optionsmultiorder(strategy="WED_IC", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        for l in final_legs: print(f"  -> {l['action']} {l['qty']}x {l['symbol']}")
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": 6 * STRIKE_STEP, "type": "CONDOR",
            "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", "CONDOR", "ENTRY", final_legs, premium, state)
        print(f"[WED] Wide IC deployed. Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Wide IC Failed: {res}")


def _deploy_grey_zone_ic(state, atm, expiry, client):
    """Grey Zone Iron Condor: OTM7 shorts, OTM12 hedges (base 1 lot). Ultra Wide."""
    multi_legs = [
        {"offset": "OTM7", "option_type": "PE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM12", "option_type": "PE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM7", "option_type": "CE", "action": "SELL", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM12", "option_type": "CE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
    ]
    res = client.optionsmultiorder(strategy="WED_GREY", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": 7 * STRIKE_STEP, "type": "CONDOR_GREY",
            "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", "CONDOR_GREY", "ENTRY", final_legs, premium, state)
        print(f"[WED] Grey Zone IC deployed (OTM7). Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Grey Zone IC Failed: {res}")


def _deploy_call_ratio(state, atm, expiry, client):
    """Call Ratio Spread: Buy 1x OTM3 CE, Sell 2x OTM7 CE, Buy 1x OTM15 CE (base 1 lot). Net credit structure."""
    multi_legs = [
        {"offset": "OTM3", "option_type": "CE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM7", "option_type": "CE", "action": "SELL", "quantity": LOT_SIZE * 2, "product": PRODUCT},
        {"offset": "OTM15", "option_type": "CE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
    ]
    res = client.optionsmultiorder(strategy="WED_BEAR", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        for l in final_legs: print(f"  -> {l['action']} {l['qty']}x {l['symbol']}")
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": 7 * STRIKE_STEP, "type": "RATIO_CALL",
            "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", "RATIO_CALL", "ENTRY", final_legs, premium, state)
        print(f"[WED] Call Ratio deployed. Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Call Ratio Failed: {res}")


def _deploy_put_ratio(state, atm, expiry, client):
    """Put Ratio Spread: Buy 1x OTM3 PE, Sell 2x OTM7 PE, Buy 1x OTM15 PE (base 1 lot). Net credit structure."""
    multi_legs = [
        {"offset": "OTM3", "option_type": "PE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT},
        {"offset": "OTM7", "option_type": "PE", "action": "SELL", "quantity": LOT_SIZE * 2, "product": PRODUCT},
        {"offset": "OTM15", "option_type": "PE", "action": "BUY", "quantity": LOT_SIZE, "product": PRODUCT}
    ]
    res = client.optionsmultiorder(strategy="WED_BULL", underlying=UNDERLYING,
                                   exchange=EXCHANGE_INDEX, expiry_date=expiry, legs=multi_legs)
    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", LOT_SIZE)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        for l in final_legs: print(f"  -> {l['action']} {l['qty']}x {l['symbol']}")
        premium = calculate_premium_collected(client, final_legs)
        state["carry_trade"] = {
            "active": True, "legs": final_legs, "premium_collected": premium,
            "strike": atm, "short_offset": 7 * STRIKE_STEP, "type": "RATIO_PUT",
            "entry_time": datetime.now().isoformat()
        }
        log_trade("carry_trade", "RATIO_PUT", "ENTRY", final_legs, premium, state)
        print(f"[WED] Put Ratio deployed. Prem=Rs.{premium:.0f}")
    else:
        print(f"[WED] Put Ratio Failed: {res}")


def _deploy_monday_adaptive(state, ltp, vix, ivr, expiry, client, mode="STANDARD"):
    """
    Monday Adaptive IC: Adjusts range based on market regime.
    STANDARD: OTM8 Shorts | OTM12 Hedges
    ULTRA_WIDE: OTM10 Shorts | OTM14 Hedges
    """
    # --- Gate checks with audit ---
    adx = state["market_data"]["adx"]
    gates = {
        "vix_gate": {"passed": vix >= 13, "value": round(vix,1), "threshold": ">=13"},
        "ivr_gate": {"passed": ivr >= 30, "value": round(ivr,0), "threshold": ">=30"},
        "adx": {"value": round(adx,1)},
        "mode": mode,
    }
    if vix < 13:
        log_decision(state, "monday_trade", "SKIPPED", f"VIX {vix:.1f} < 13 — too calm", gates)
        return
    qty = LOT_SIZE
    if ivr < 30:
        log_decision(state, "monday_trade", "SKIPPED", f"IVR {ivr:.0f} < 30 — low vol", gates)
        return
    
    intraday_only = vix > 18 or adx > 25
    if intraday_only:
        print("[MON] Volatility high. Flagging for INTRADAY EXIT at 15:15.")
    if mode == "ULTRA_WIDE" and intraday_only:
        qty = LOT_SIZE * 2
        print(f"[MON] ROI BOOST: Ultra-Wide Intraday detected. Using 2x size: {qty} qty.")
    else:
        print(f"[MON] IVR {ivr:.0f} is healthy. Trading base size: {qty} qty.")

    carry = state["carry_trade"]
    carry_pnl = 0
    if carry["active"] and carry.get("premium_collected", 0) > 0:
        symbols = [l["symbol"] for l in carry["legs"]]
        price_map = batch_get_quotes(client, symbols)
        carry_cost = sum(
            price_map.get(l["symbol"], 0) * l["qty"] * (1 if l["action"]=="SELL" else -1)
            for l in carry["legs"]
        )
        carry_pnl = carry["premium_collected"] - carry_cost
        if carry_pnl < 0:
            gates["carry_pnl_gate"] = {"passed": False, "value": round(carry_pnl,0)}
            log_decision(state, "monday_trade", "SKIPPED", f"Carry in loss Rs.{carry_pnl:.0f}", gates)
            return
    gates["carry_pnl_gate"] = {"passed": True, "value": round(carry_pnl,0)}

    atm = atm_from_ltp(ltp)
    
    # Strike Selection based on Mode & Boost
    if mode == "STANDARD":
        short_offset = 8
        hedge_offset = 12
    else: # ULTRA_WIDE
        # If intraday only, we bring selling strikes closer (OTM8) for better Theta
        short_offset = 8 if intraday_only else 10
        hedge_offset = 14
    
    multi_legs = [
        {"offset": f"OTM{short_offset}", "option_type": "CE", "action": "SELL", "quantity": qty, "product": PRODUCT},
        {"offset": f"OTM{short_offset}", "option_type": "PE", "action": "SELL", "quantity": qty, "product": PRODUCT},
        {"offset": f"OTM{hedge_offset}", "option_type": "CE", "action": "BUY", "quantity": qty, "product": PRODUCT},
        {"offset": f"OTM{hedge_offset}", "option_type": "PE", "action": "BUY", "quantity": qty, "product": PRODUCT},
    ]

    res = client.optionsmultiorder(strategy=f"MON_{mode}", underlying=UNDERLYING, exchange=EXCHANGE_INDEX, 
                                  expiry_date=expiry, legs=multi_legs)

    if res.get("status") == "success":
        final_legs = []
        for r in res.get("results", []):
            sym = r["symbol"]
            final_legs.append({
                "symbol": sym,
                "qty": r.get("quantity", r.get("qty", qty)),
                "action": r["action"],
                "option_type": "CE" if sym.endswith("CE") else "PE"
            })
        premium = calculate_premium_collected(client, final_legs)
        log_trade("monday_trade", f"CONDOR_{mode}", "ENTRY", final_legs, premium, state)
        log_decision(state, "monday_trade", "DEPLOYED", f"{mode} IC deployed | Prem=Rs.{premium:.0f}", gates)
        state["monday_trade"] = {
            "active": True,
            "legs": final_legs,
            "premium_collected": premium,
            "strike": atm,
            "short_offset": short_offset * STRIKE_STEP,
            "type": f"CONDOR_{mode}",
            "entry_time": datetime.now().isoformat(),
            "intraday_only": intraday_only
        }
        print(f"[MON] {mode} IC deployed | Prem=Rs.{premium:.0f}")

    else:
        print(f"[MON] {mode} Failed: {res}")


def find_strike_by_premium(client, expiry, opt_type, target_min, target_max, start_offset=1):
    """
    Scans strikes to find one within the target premium range.
    Scans from OTM1 out to OTM15.
    Returns (strike_offset, premium, symbol)
    """
    ltp_res = client.quotes(exchange=EXCHANGE_INDEX, symbol=UNDERLYING)
    ltp = float(ltp_res.get("data", {}).get("ltp", 0))
    if ltp == 0: return None
    
    atm = atm_from_ltp(ltp)
    
    # Scan from OTM1 out to OTM15 to find the first strike that fits the premium band
    for off in range(1, 16):
        strike = atm + (off * STRIKE_STEP) if opt_type == "CE" else atm - (off * STRIKE_STEP)
        sym = build_sym(expiry, strike, opt_type)
        q = batch_get_quotes(client, [sym])
        price = q.get(sym, 0)
        
        if target_min <= price <= target_max:
            return off, price, sym
            
    return None

def deploy_tuesday_ic(state, ltp, vix, gap_pct, expiry, client):
    """
    Tuesday (Expiry): Smart Adaptive Entry.
    Starts looking at OTM6, moves closer if premiums are < 10 Rs.
    Target: 10-25 Rs per leg.
    """
    if vix < 11 or vix > 22:
        print(f"[TUE] VIX {vix:.1f} out of safe range (11-22). Skipping expiry play.")
        return
    if gap_pct > 1.0:
        print(f"[TUE] Tuesday gap {gap_pct:.2f}% > 1.0%. Too risky. Skipping.")
        return

    print(f"[TUE] Starting Adaptive Entry Search...")
    
    # Find Best CE
    ce_data = find_strike_by_premium(client, expiry, "CE", 10, 25, start_offset=6)
    # Find Best PE
    pe_data = find_strike_by_premium(client, expiry, "PE", 10, 25, start_offset=6)
    
    if not ce_data or not pe_data:
        print(f"[TUE] Could not find both sides with 10-25 Rs premium. CE={ce_data}, PE={pe_data}")
        return

    ce_off, ce_p, ce_sym = ce_data
    pe_off, pe_p, pe_sym = pe_data
    
    print(f"[TUE] Selected Strikes: CE {ce_sym} (@{ce_p}), PE {pe_sym} (@{pe_p})")

    # Build Hedges (fixed 2 strikes away from shorts)
    ce_hedge_sym = build_sym(expiry, int(''.join(filter(str.isdigit, ce_sym[-7:]))) + (2 * STRIKE_STEP), "CE")
    pe_hedge_sym = build_sym(expiry, int(''.join(filter(str.isdigit, pe_sym[-7:]))) - (2 * STRIKE_STEP), "PE")

    legs_to_execute = [
        {"symbol": ce_sym, "action": "SELL", "qty": LOT_SIZE},
        {"symbol": ce_hedge_sym, "action": "BUY", "qty": LOT_SIZE},
        {"symbol": pe_sym, "action": "SELL", "qty": LOT_SIZE},
        {"symbol": pe_hedge_sym, "action": "BUY", "qty": LOT_SIZE},
    ]

    # Execute via Basket
    execute_basket(legs_to_execute, "TUESDAY_ENTRY", client)
    
    # Calculate precise entry premiums for state
    final_quotes = batch_get_quotes(client, [ce_sym, ce_hedge_sym, pe_sym, pe_hedge_sym])
    
    final_legs = []
    total_credit = 0
    for l in legs_to_execute:
        entry_p = final_quotes.get(l["symbol"], 0)
        final_legs.append({
            "symbol": l["symbol"],
            "qty": l["qty"],
            "action": l["action"],
            "entry_price": entry_p,
            "option_type": "CE" if l["symbol"].endswith("CE") else "PE"
        })
        total_credit += entry_p * l["qty"] * (1 if l["action"]=="SELL" else -1)

    state["tuesday_trade"] = {
        "active": True,
        "legs": final_legs,
        "premium_collected": total_credit,
        "strike": atm_from_ltp(ltp),
        "type": "CONDOR",
        "entry_time": datetime.now().isoformat()
    }
    log_trade("tuesday_trade", "SMART_IC", "ENTRY", final_legs, total_credit, state)
    print(f"[TUE] Smart IC Deployed. Total Credit: Rs.{total_credit:.0f}")

def adjust_tuesday_expiry(state, ltp, client, expiry):
    """
    Tuesday Rolling Logic (Active All Day):
    - Profit Roll: If a short leg decays > 80%, roll closer.
    - Defensive Roll: If a short leg spikes > 3x entry, roll further away.
    - Both rolls target the 10-18 Rs premium band.
    """
    slot = state["tuesday_trade"]
    if not slot["active"]: return

    # 1. Fetch current prices for all legs
    symbols = [l["symbol"] for l in slot["legs"]]
    quotes = batch_get_quotes(client, symbols)
    
    ce_short = next((l for l in slot["legs"] if l["action"] == "SELL" and l["option_type"] == "CE"), None)
    pe_short = next((l for l in slot["legs"] if l["action"] == "SELL" and l["option_type"] == "PE"), None)
    
    to_roll = [] # List of sides to roll: "CE", "PE"

    for leg in [ce_short, pe_short]:
        if not leg: continue
        curr_p = quotes.get(leg["symbol"], 0)
        entry_p = leg.get("entry_price", 0)
        if entry_p <= 0 or curr_p <= 0: continue

        decay = (entry_p - curr_p) / entry_p
        spike = curr_p / entry_p
        side = leg["option_type"]

        if decay >= 0.80:
            print(f"[TUE] {side} Profit Roll: Decayed 80% ({curr_p} vs {entry_p})")
            to_roll.append(side)
        elif spike >= 3.0:
            print(f"[TUE] {side} Defensive Roll: Spiked 3x ({curr_p} vs {entry_p})")
            to_roll.append(side)

    if not to_roll: return

    # 2. Execute Rolls
    for side in to_roll:
        # A. Close existing side
        side_legs = [l for l in slot["legs"] if l["option_type"] == side]
        exit_legs = [{"symbol": l["symbol"], "qty": l["qty"], "action": "SELL" if l["action"]=="BUY" else "BUY"} for l in side_legs]
        execute_basket(exit_legs, f"TUE_ADJ_{side}", client)
        
        # Remove closed legs from state
        slot["legs"] = [l for l in slot["legs"] if l["option_type"] != side]
        
        # B. Find New Strike (10-18 Rs band)
        new_data = find_strike_by_premium(client, expiry, side, 10, 18)
        
        if new_data:
            off, new_p, new_sym = new_data
            new_strike = int(''.join(filter(str.isdigit, new_sym[-7:])))
            new_hedge_strike = new_strike + (2 * STRIKE_STEP) if side == "CE" else new_strike - (2 * STRIKE_STEP)
            new_hedge_sym = build_sym(expiry, new_hedge_strike, side)
            
            new_legs = [
                {"symbol": new_sym, "action": "SELL", "qty": LOT_SIZE},
                {"symbol": new_hedge_sym, "action": "BUY", "qty": LOT_SIZE}
            ]
            execute_basket(new_legs, f"TUE_ROLL_{side}", client)
            
            # Update state with new legs
            new_quotes = batch_get_quotes(client, [new_sym, new_hedge_sym])
            for nl in new_legs:
                ep = new_quotes.get(nl["symbol"], 0)
                slot["legs"].append({
                    "symbol": nl["symbol"], "qty": nl["qty"], "action": nl["action"],
                    "entry_price": ep, "option_type": side
                })
            print(f"[TUE] Successfully rolled {side} to {new_sym} (@{new_p})")
        else:
            print(f"[TUE] Roll Failed: No {side} strike in 10-18 Rs range. Side remains closed.")


# ============================================================
# 8. MAIN ENGINE LOOP
# ============================================================
def run():
    print("=== NIFTY WEEKLY MASTER — PRECISION ENGINE v2 ===")
    print(f"    State : {STATE_FILE}")
    print(f"    Loop  : 300s (5min)")
    print(f"    Lot   : {LOT_SIZE} qty")

    while True:
        try:
            state = load_state()
            now = datetime.now()
            t_str = now.strftime("%H:%M")
            day = now.weekday()  # 0=Mon, 1=Tue, 3=Thu, 4=Fri

            # Skip outside market hours (09:15 - 15:30)
            if not ("09:15" <= t_str <= "15:30"):
                save_state(state)
                time.sleep(300)
                continue

            # --- Fetch market data (OpenAlgo Standard Format) ---
            res_nifty = client.quotes(exchange=EXCHANGE_INDEX, symbol="NIFTY")
            ltp = float(res_nifty.get("data", {}).get("ltp", 0))
            
            res_vix = client.quotes(exchange=EXCHANGE_INDEX, symbol="INDIAVIX")
            vix = float(res_vix.get("data", {}).get("ltp", 15))
            
            expiry = get_expiry()

            if not expiry or ltp == 0:
                print(f"[{t_str}] Missing data (LTP={ltp}, expiry={expiry}). Skipping.")
                save_state(state)
                time.sleep(300)
                continue

            ivr = calculate_ivr(client, state, vix)

            # Store market snapshot in state for dashboard
            adx = calculate_adx(client)
            chain = analyze_option_chain(client, expiry)
            pcr = chain["pcr"]

            # Record morning stats on first eval of the day (for intraday shift calc)
            if state.get("morning_pcr_date") != str(date.today()):
                state["morning_pcr"] = pcr
                state["morning_spot"] = ltp
                state["morning_pcr_date"] = str(date.today())

            state["market_data"] = {"vix": vix, "ivr": ivr, "adx": adx, "pcr": pcr, "nifty_ltp": ltp}
            state["oi_data"] = chain["oi_data"]
            state["key_levels"] = chain["key_levels"]

            # Compute days to expiry for Greeks
            try:
                exp_str = expiry  # e.g. "05MAY26"
                exp_date = datetime.strptime(exp_str, "%d%b%y").date()
                dte = max((exp_date - date.today()).days, 0)
            except Exception:
                dte = 2
            # Dynamic Limit Update
            state["weekly_limit"] = WEEKLY_LIMIT
            
            all_signals = []
            for slot_key in ["carry_trade", "monday_trade", "tuesday_trade"]:
                slot = state[slot_key]
                slot["greeks"] = calculate_position_greeks(slot, ltp, vix, dte)
                
                # Check for rolling signals
                if slot.get("active"):
                    slot_signals = calculate_rolling_signals(slot, ltp, client)
                    for s in slot_signals:
                        s["slot"] = slot_key
                    all_signals.extend(slot_signals)
                    
                # Live PnL
                if slot.get("active") and slot.get("premium_collected", 0) > 0:
                    syms = [l["symbol"] for l in slot["legs"]]
                    pm = batch_get_quotes(client, syms)
                    cur = sum(pm.get(l["symbol"],0) * l["qty"] * (1 if l["action"]=="SELL" else -1) for l in slot["legs"])
                    slot["live_pnl"] = round(slot["premium_collected"] - cur, 0)
                else:
                    slot["live_pnl"] = 0
            
            state["adjustment_signals"] = all_signals

            print(f"[{t_str}] Day={day} | Nifty={ltp} | VIX={vix:.1f} | IVR={ivr:.0f} | ADX={adx:.1f} | PCR={pcr:.2f}")

            # Hard block — no actions if week is blown
            if state["week_blocked"]:
                print(f"[{t_str}] WEEK BLOCKED. Waiting.")
                save_state(state)
                time.sleep(300)
                continue

            # ============================
            # MONDAY — ADAPTIVE IC
            # ============================
            if day == 0:
                # IC entry at 10:00 (let gap settle)
                if "10:00" <= t_str <= "11:15":
                    if not state["monday_trade"]["active"]:
                        # Auto-Fetch Friday Close and Monday Open from API
                        res_data = client.quotes(exchange=EXCHANGE_INDEX, symbol=UNDERLYING)
                        fri_close = state.get("friday_close", 0)
                        if fri_close <= 0:
                            print("[MON] Friday close missing in state. Fetching from API...")
                            fri_close = float(res_data.get("data", {}).get("prev_close", ltp))
                        
                        open_price = float(res_data.get("data", {}).get("open", ltp))
                        gap_pct = abs(open_price - fri_close) / fri_close * 100
                        intraday_pct = abs(ltp - open_price) / open_price * 100
                        
                        print(f"[MON] Weekend Gap: {gap_pct:.2f}% | Intraday Move: {intraday_pct:.2f}% | ADX: {adx:.1f}")
                        
                        # Adaptive Range Selection based on True Weekend Gap
                        gap_gates = {
                            "gap_gate": {"passed": gap_pct <= 0.7, "value": round(gap_pct,2), "threshold": "<=0.7%"},
                            "adx_gate": {"passed": adx <= 24, "value": round(adx,1), "threshold": "<=24"},
                        }
                        if gap_pct > 0.7 or adx > 24:
                            log_decision(state, "monday_trade", "SKIPPED", f"Extreme: Gap={gap_pct:.2f}%, ADX={adx:.1f}", gap_gates)
                        elif gap_pct > 0.3 or adx > 18:
                            log_decision(state, "monday_trade", "EVALUATING", f"Nervous Monday → ULTRA_WIDE", gap_gates)
                            _deploy_monday_adaptive(state, ltp, vix, ivr, expiry, client, "ULTRA_WIDE")
                        else:
                            log_decision(state, "monday_trade", "EVALUATING", f"Peaceful Monday → STANDARD", gap_gates)
                            _deploy_monday_adaptive(state, ltp, vix, ivr, expiry, client, "STANDARD")

                # Intraday exit removed to allow Risk Monitor to handle profit/loss
                # if t_str >= "15:15" and state["monday_trade"]["active"] and state["monday_trade"].get("intraday_only"):
                #     exit_slot(state["monday_trade"], "MONDAY", client, state=state, exit_reason="INTRADAY_EXIT")
                
                if "15:20" <= t_str <= "15:25":
                    state["monday_close"] = ltp

            # ============================
            # TUESDAY — EXPIRY DAY
            # ============================
            if day == 1:
                # 1. Adaptive Entry Window (09:20 - 12:00)
                if "09:20" <= t_str <= "12:00" and not state["tuesday_trade"]["active"]:
                    mon_close = state.get("monday_close", ltp)
                    if mon_close <= 0: mon_close = ltp
                    gap_pct = abs(ltp - mon_close) / mon_close * 100
                    deploy_tuesday_ic(state, ltp, vix, gap_pct, expiry, client)

                # 2. Rolling Adjustments (Active All Day until 15:00)
                if t_str < "15:00" and state["tuesday_trade"]["active"]:
                    adjust_tuesday_expiry(state, ltp, client, expiry)

                # 3. Final Expiry Exit (15:10)
                if t_str >= "15:10" and state["tuesday_trade"]["active"]:
                    exit_slot(state["tuesday_trade"], "TUESDAY", client, state=state, exit_reason="EXPIRY_EXIT")


            # ==========================================
            # WED/THU/FRI — CARRY ENTRY (RESCUE)
            # ==========================================
            if day in [2, 3, 4]:
                # Ratio Spreads trigger ANYTIME. ICs trigger between 15:15 - 15:30.
                if not state["carry_trade"]["active"]:
                    if day == 2: state["wednesday_close"] = ltp 
                    deploy_wednesday_carry(state, ltp, vix, ivr, adx, pcr, expiry, client)

            # ============================
            # FRIDAY — EXIT CARRY & RECORD ANCHOR
            # ============================
            if day == 4:
                # Friday morning scheduled exit removed to prevent premature closing of Thursday/Wednesday carry.
                # if "09:20" <= t_str <= "09:35":
                #     if state["carry_trade"]["active"]:
                #         print("[FRI] Closing Wednesday Carry to capture 2-night decay.")
                #         exit_slot(state["carry_trade"], "CARRY", client, state=state, exit_reason="SCHEDULED_EXIT")
                
                # Record the "Anchor" for Monday's Weekend Gap
                if "15:20" <= t_str <= "15:26":
                    state["friday_close"] = ltp
                    print(f"[FRI] Friday Close Recorded: {ltp}. Used for Monday Gap analysis.")

            # === Risk Monitor (runs every cycle) ===
            monitor_risk(state, ltp, client)

            save_state(state)
            time.sleep(300)

        except Exception as e:
            print(f"[ERROR] Engine loop error: {e}")
            time.sleep(60)  # shorter retry on error


if __name__ == "__main__":
    run()
