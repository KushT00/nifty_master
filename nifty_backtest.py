import pandas as pd
import numpy as np
from math import log, sqrt, exp, erf
from datetime import datetime, date, time, timedelta

# ============================================================
# CONSTANTS & PARAMETERS (Matching pinescript_nifty.txt)
# ============================================================
SMA_FAST_LEN     = 68
SMA_SLOW_LEN     = 90
EMA_TREND_LEN    = 340

SL_PCT           = 0.75          # Stop Loss %
TRAIL_PCT        = 0.75          # Trailing Stop Loss %
DIST_PCT         = 0.0           # Set to 0.0 per your settings
RE_ENTRY_DIST_PCT= 0.0         # Set to 0.0 per your settings

# Option simulation parameters
IV               = 0.14          # Implied Volatility (14% annualized - typical Nifty)
RISK_FREE_RATE   = 0.07          # Risk-free rate (7% - approx RBI repo)
TARGET_PREMIUM   = 200.0         # Target option premium Rs. 200
STRIKE_INTERVAL  = 50            # Nifty strike interval
LOT_SIZE         = 65            # Nifty lot size

# Date ranges
ANALYSIS_START_DATE = "2026-03-28"
ANALYSIS_END_DATE   = "2026-04-28"

# ============================================================
# BLACK-SCHOLES OPTION PRICING (Pure math, no scipy needed)
# ============================================================
def norm_cdf(x):
    """Standard normal CDF using built-in math.erf."""
    return (1.0 + erf(x / sqrt(2.0))) / 2.0

def bs_d1(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    return (log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt(T))

def bs_d2(S, K, T, r, sigma):
    if T <= 0:
        return 0.0
    return bs_d1(S, K, T, r, sigma) - sigma * sqrt(T)

def bs_call(S, K, T, r, sigma):
    """Black-Scholes Call option price."""
    if T <= 0:
        return max(0.0, S - K)  # Intrinsic at expiry
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return S * norm_cdf(d1) - K * exp(-r * T) * norm_cdf(d2)

def bs_put(S, K, T, r, sigma):
    """Black-Scholes Put option price."""
    if T <= 0:
        return max(0.0, K - S)  # Intrinsic at expiry
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    return K * exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)

def bs_call_delta(S, K, T, r, sigma):
    if T <= 0:
        return 1.0 if S > K else 0.0
    return norm_cdf(bs_d1(S, K, T, r, sigma))

def bs_put_delta(S, K, T, r, sigma):
    if T <= 0:
        return -1.0 if S < K else 0.0
    return norm_cdf(bs_d1(S, K, T, r, sigma)) - 1.0

def bs_theta_call(S, K, T, r, sigma):
    """Daily theta for a call (negative = decay per day)."""
    if T <= 0:
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    pdf_d1 = exp(-0.5 * d1 ** 2) / sqrt(2 * 3.141592653589793)
    theta = (-(S * pdf_d1 * sigma) / (2 * sqrt(T))
             - r * K * exp(-r * T) * norm_cdf(d2))
    return theta / 365.0  # Per day

def bs_theta_put(S, K, T, r, sigma):
    """Daily theta for a put (negative = decay per day)."""
    if T <= 0:
        return 0.0
    d1 = bs_d1(S, K, T, r, sigma)
    d2 = bs_d2(S, K, T, r, sigma)
    pdf_d1 = exp(-0.5 * d1 ** 2) / sqrt(2 * 3.141592653589793)
    theta = (-(S * pdf_d1 * sigma) / (2 * sqrt(T))
             + r * K * exp(-r * T) * norm_cdf(-d2))
    return theta / 365.0  # Per day

# ============================================================
# EXPIRY & STRIKE SELECTION
# ============================================================
def get_next_tuesday_expiry(dt):
    """Get the next Tuesday at 15:30 from the given datetime.
    If dt is Tuesday before 15:30, use that same Tuesday.
    """
    days_ahead = 1 - dt.weekday()  # Tuesday = 1
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0:
        # It's Tuesday - check if before 15:30
        expiry_time = dt.replace(hour=15, minute=30, second=0, microsecond=0)
        if dt < expiry_time:
            return expiry_time
        else:
            days_ahead = 7  # Next Tuesday
    
    next_tue = dt + timedelta(days=days_ahead)
    return next_tue.replace(hour=15, minute=30, second=0, microsecond=0)

def time_to_expiry_years(current_dt, expiry_dt):
    """Calculate T in years."""
    diff = (expiry_dt - current_dt).total_seconds()
    if diff <= 0:
        return 0.0
    return diff / (365.25 * 24 * 3600)

def find_strike_near_premium(S, current_dt, option_type, target_premium=200.0,
                              r=RISK_FREE_RATE, sigma=IV):
    """Find the Nifty strike whose BS premium is closest to target_premium."""
    expiry_dt = get_next_tuesday_expiry(current_dt)
    T = time_to_expiry_years(current_dt, expiry_dt)
    
    if T <= 0:
        # Expiry is now or past - use next week
        expiry_dt = get_next_tuesday_expiry(current_dt + timedelta(days=1))
        T = time_to_expiry_years(current_dt, expiry_dt)
    
    # Scan strikes around ATM (±2000 points at 50-point intervals)
    atm = round(S / STRIKE_INTERVAL) * STRIKE_INTERVAL
    best_strike = atm
    best_premium = 0.0
    best_delta = 0.0
    min_diff = float('inf')
    
    for offset in range(-40, 41):  # ±2000 points
        K = atm + offset * STRIKE_INTERVAL
        if K <= 0:
            continue
            
        if option_type == "CE":
            premium = bs_call(S, K, T, r, sigma)
            delta = bs_call_delta(S, K, T, r, sigma)
        else:
            premium = bs_put(S, K, T, r, sigma)
            delta = abs(bs_put_delta(S, K, T, r, sigma))
            
        diff = abs(premium - target_premium)
        if diff < min_diff:
            min_diff = diff
            best_strike = K
            best_premium = premium
            best_delta = delta
            
    return best_strike, best_premium, best_delta, expiry_dt

def compute_option_price(S, K, expiry_dt, current_dt, option_type,
                          r=RISK_FREE_RATE, sigma=IV):
    """Compute option price at a given time."""
    T = time_to_expiry_years(current_dt, expiry_dt)
    if option_type == "CE":
        return bs_call(S, K, T, r, sigma)
    else:
        return bs_put(S, K, T, r, sigma)

def compute_option_delta(S, K, expiry_dt, current_dt, option_type,
                          r=RISK_FREE_RATE, sigma=IV):
    """Compute option delta at a given time."""
    T = time_to_expiry_years(current_dt, expiry_dt)
    if option_type == "CE":
        return bs_call_delta(S, K, T, r, sigma)
    else:
        return abs(bs_put_delta(S, K, T, r, sigma))

# ============================================================
# INDICATOR CALCULATIONS
# ============================================================
def calculate_sma(series, length):
    return series.rolling(window=length).mean()

def calculate_ema_exact(series, length):
    vals = series.values
    ema = np.empty(len(vals))
    ema.fill(np.nan)
    
    if len(vals) < length:
        return pd.Series(ema, index=series.index)
        
    seed = np.mean(vals[:length])
    ema[length-1] = seed
    
    multiplier = 2.0 / (length + 1.0)
    current_ema = seed
    for i in range(length, len(vals)):
        current_ema = (vals[i] * multiplier) + (current_ema * (1.0 - multiplier))
        ema[i] = current_ema
        
    return pd.Series(ema, index=series.index)

# ============================================================
# MAIN BACKTEST ENGINE
# ============================================================
def run_backtest():
    print("="*80)
    print(" Nifty 5m Triple Confirm - Spot + Options Backtest Engine")
    print("="*80)
    print(f"  IV: Dynamic (India VIX from vix.csv) | Risk-Free: {RISK_FREE_RATE*100:.1f}% | Target Premium: Rs.{TARGET_PREMIUM:.0f}")
    print(f"  Expiry: Weekly Tuesday 15:30 | Strike Interval: {STRIKE_INTERVAL}")
    print(f"  Lot Size: {LOT_SIZE}")
    print("="*80)
    
    print("\nLoading Nifty 5m Spot data...")
    df = pd.read_csv("NIFTY_NSE_INDEX_5m.csv")
    df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])
    df = df.sort_values('datetime').reset_index(drop=True)
    
    # Load India VIX daily data for dynamic IV
    print("Loading India VIX daily data...")
    vix_df = pd.read_csv("vix.csv")
    vix_df['vix_date'] = pd.to_datetime(vix_df['date'])
    vix_df = vix_df.sort_values('vix_date').reset_index(drop=True)
    vix_df['iv'] = vix_df['close'] / 100.0  # VIX 16.45 -> 0.1645 annualized vol
    vix_daily = vix_df[['vix_date', 'iv']].copy()
    
    # Map VIX to Nifty 5m bars by date
    df['trade_date'] = df['datetime'].dt.normalize()
    df = df.merge(vix_daily, left_on='trade_date', right_on='vix_date', how='left')
    df['iv'] = df['iv'].ffill().bfill()  # Forward-fill then back-fill any gaps
    df.drop(columns=['vix_date'], inplace=True)
    print(f"  VIX range: {df['iv'].min()*100:.1f}% - {df['iv'].max()*100:.1f}% | Mean: {df['iv'].mean()*100:.1f}%")
    
    print("Calculating indicators (SMA68, SMA90, EMA340)...")
    df['sma68'] = calculate_sma(df['close'], SMA_FAST_LEN)
    df['sma90'] = calculate_sma(df['close'], SMA_SLOW_LEN)
    df['ema340'] = calculate_ema_exact(df['close'], EMA_TREND_LEN)
    df['prev_sma68'] = df['sma68'].shift(1)
    df['prev_sma90'] = df['sma90'].shift(1)
    
    # Filter data starting from January 1, 2022
    df = df[df['datetime'] >= '2022-01-01 00:00:00'].reset_index(drop=True)
    
    # Strategy states
    position_size = 0
    position_avg_price = 0.0
    pending_entry = 0
    pending_entry_reason = ""
    pending_exit = False
    
    trail_high = np.nan
    trail_low = np.nan
    stop_loss = np.nan
    trail_active = False
    
    bullTrend = False
    bearTrend = False
    longPullbackReady = False
    shortPullbackReady = False
    lastSignal = 0
    oppositeSeen = True
    
    # Option tracking
    opt_strike = 0
    opt_entry_premium = 0.0
    opt_entry_delta = 0.0
    opt_expiry_dt = None
    opt_type = ""
    
    active_trade = None
    trades = []
    
    print("Running state machine simulation with next-bar-open execution...\n")
    
    for idx, row in df.iterrows():
        if pd.isna(row['sma68']) or pd.isna(row['sma90']) or pd.isna(row['ema340']) or pd.isna(row['prev_sma68']) or pd.isna(row['prev_sma90']):
            continue
            
        current_open = row['open']
        current_close = row['close']
        current_high = row['high']
        current_low = row['low']
        current_time = row['datetime']
        current_iv = row['iv']
        
        sma68 = row['sma68']
        sma90 = row['sma90']
        ema340 = row['ema340']
        prev_sma68 = row['prev_sma68']
        prev_sma90 = row['prev_sma90']
        
        # =============================================================
        # 1. PROCESS ORDERS AT THE OPEN OF THE BAR (Fill Pending Orders)
        # =============================================================
        if pending_exit:
            # Calculate option exit premium at the open price of this bar (dynamic IV)
            opt_exit_premium = compute_option_price(
                current_open, opt_strike, opt_expiry_dt, current_time, opt_type, sigma=current_iv)
            opt_exit_delta = compute_option_delta(
                current_open, opt_strike, opt_expiry_dt, current_time, opt_type, sigma=current_iv)
            
            pnl_points = 0.0
            if position_size > 0:
                pnl_points = current_open - position_avg_price
            elif position_size < 0:
                pnl_points = position_avg_price - current_open
                
            pnl_pct = pnl_points / position_avg_price * 100
            opt_pnl = opt_exit_premium - opt_entry_premium  # Buying options: exit - entry
            opt_pnl_pct = opt_pnl / opt_entry_premium * 100 if opt_entry_premium > 0 else 0.0
            
            # Calculate holding period
            hold_hours = (current_time - active_trade["entry_time"]).total_seconds() / 3600
            
            # Theoretical theta decay (entry premium - intrinsic at exit)
            if opt_type == "CE":
                intrinsic_at_exit = max(0.0, current_open - opt_strike)
            else:
                intrinsic_at_exit = max(0.0, opt_strike - current_open)
            time_value_lost = (opt_entry_premium - max(0, opt_entry_premium - abs(pnl_points * opt_entry_delta))) 
            
            active_trade["exit_time"] = current_time
            active_trade["exit_price"] = current_open
            active_trade["exit_reason"] = "TSL_EXIT"
            active_trade["pnl_points"] = pnl_points
            active_trade["pnl_pct"] = pnl_pct
            active_trade["opt_strike"] = opt_strike
            active_trade["opt_entry_premium"] = opt_entry_premium
            active_trade["opt_exit_premium"] = opt_exit_premium
            active_trade["opt_pnl"] = opt_pnl
            active_trade["opt_pnl_pct"] = opt_pnl_pct
            active_trade["opt_entry_delta"] = opt_entry_delta
            active_trade["opt_exit_delta"] = opt_exit_delta
            active_trade["opt_pnl_per_lot"] = opt_pnl * LOT_SIZE
            active_trade["hold_hours"] = hold_hours
            active_trade["peak_price"] = trail_high if position_size > 0 else trail_low
            active_trade["stop_loss_final"] = stop_loss
            trades.append(active_trade)
            
            active_trade = None
            position_size = 0
            position_avg_price = 0.0
            pending_exit = False
            
            oppositeSeen = True
            lastSignal = 0
            if bullTrend:
                longPullbackReady = False
            if bearTrend:
                shortPullbackReady = False
            trail_active = False
                
        elif pending_entry != 0:
            position_size = pending_entry
            position_avg_price = current_open
            
            # Select option strike and compute entry premium
            opt_type = "CE" if position_size > 0 else "PE"
            opt_strike, opt_entry_premium, opt_entry_delta, opt_expiry_dt = \
                find_strike_near_premium(current_open, current_time, opt_type, target_premium=TARGET_PREMIUM, sigma=current_iv)
            
            trail_active = False
            if position_size > 0:
                trail_high = current_high
                stop_loss = position_avg_price * (1 - SL_PCT / 100)
                active_trade = {
                    "type": "LONG", "entry_time": current_time,
                    "entry_price": position_avg_price,
                    "entry_reason": pending_entry_reason,
                }
            else:
                trail_low = current_low
                stop_loss = position_avg_price * (1 + SL_PCT / 100)
                active_trade = {
                    "type": "SHORT", "entry_time": current_time,
                    "entry_price": position_avg_price,
                    "entry_reason": pending_entry_reason,
                }
            pending_entry = 0
            pending_entry_reason = ""
            
        # =============================================================
        # 2. UPDATE TRAILING HIGHS/LOWS & SL DURING THE BAR
        # =============================================================
        if position_size > 0:
            profit_pct = (current_high - position_avg_price) / position_avg_price * 100
            if not trail_active and profit_pct >= TRAIL_PCT:
                trail_active = True
                trail_high = current_high
            
            if trail_active:
                trail_high = max(trail_high, current_high)
                dynamic_sl = trail_high * (1 - TRAIL_PCT / 100)
                stop_loss = max(stop_loss, dynamic_sl)
            else:
                stop_loss = position_avg_price * (1 - SL_PCT / 100)
                
        elif position_size < 0:
            profit_pct = (position_avg_price - current_low) / position_avg_price * 100
            if not trail_active and profit_pct >= TRAIL_PCT:
                trail_active = True
                trail_low = current_low
                
            if trail_active:
                trail_low = min(trail_low, current_low)
                dynamic_sl = trail_low * (1 + TRAIL_PCT / 100)
                stop_loss = min(stop_loss, dynamic_sl)
            else:
                stop_loss = position_avg_price * (1 + SL_PCT / 100)
            
        # =============================================================
        # 3. UPDATE INDICATOR-BASED TREND STATE
        # =============================================================
        bull_cross = (prev_sma68 <= prev_sma90) and (sma68 > sma90)
        bear_cross = (prev_sma68 >= prev_sma90) and (sma68 < sma90)
        
        if bull_cross:
            bullTrend = True
            bearTrend = False
        elif bear_cross:
            bearTrend = True
            bullTrend = False
            
        aboveEMA = current_close > ema340
        belowEMA = current_close < ema340
        
        if bullTrend and belowEMA:
            longPullbackReady = True
        if bearTrend and aboveEMA:
            shortPullbackReady = True
            
        if bear_cross:
            longPullbackReady = False
        if bull_cross:
            shortPullbackReady = False
            
        if lastSignal == 1 and bear_cross:
            oppositeSeen = True
        elif lastSignal == -1 and bull_cross:
            oppositeSeen = True
            
        # =============================================================
        # 4. CHECK FOR EXITS AT CANDLE CLOSE
        # =============================================================
        if position_size > 0 and not pending_exit:
            if current_low <= stop_loss:
                pending_exit = True
        elif position_size < 0 and not pending_exit:
            if current_high >= stop_loss:
                pending_exit = True
                
        # =============================================================
        # 5. CHECK FOR ENTRIES AT CANDLE CLOSE
        # =============================================================
        if position_size == 0 and not pending_exit and pending_entry == 0:
            market_time = current_time.time()
            inTimeGate = (current_time.weekday() < 5) and (time(9, 15) <= market_time < time(15, 30))
            
            if inTimeGate:
                distFromEMA = abs(current_close - ema340) / ema340 * 100
                validDistance = distFromEMA >= DIST_PCT
                reEntryDist = distFromEMA >= RE_ENTRY_DIST_PCT
                
                longBase = bull_cross and aboveEMA and validDistance and (lastSignal != 1 or oppositeSeen)
                longReEntry = bullTrend and longPullbackReady and aboveEMA and reEntryDist
                
                if longBase or longReEntry:
                    pending_entry = 1
                    pending_entry_reason = "BASE_ENTRY" if longBase else "SMART_REENTRY"
                    lastSignal = 1
                    oppositeSeen = False
                    longPullbackReady = False
                else:
                    shortBase = bear_cross and belowEMA and validDistance and (lastSignal != -1 or oppositeSeen)
                    shortReEntry = bearTrend and shortPullbackReady and belowEMA and reEntryDist
                    
                    if shortBase or shortReEntry:
                        pending_entry = -1
                        pending_entry_reason = "BASE_ENTRY" if shortBase else "SMART_REENTRY"
                        lastSignal = -1
                        oppositeSeen = False
                        shortPullbackReady = False

    # Handle open position at end of data
    if active_trade is not None:
        last_row = df.iloc[-1]
        last_time = last_row['datetime']
        last_close = last_row['close']
        
        last_iv = last_row['iv']
        opt_exit_premium = compute_option_price(
            last_close, opt_strike, opt_expiry_dt, last_time, opt_type, sigma=last_iv)
        opt_exit_delta = compute_option_delta(
            last_close, opt_strike, opt_expiry_dt, last_time, opt_type, sigma=last_iv)
        
        if active_trade["type"] == "LONG":
            pnl_points = last_close - position_avg_price
        else:
            pnl_points = position_avg_price - last_close
            
        pnl_pct = pnl_points / position_avg_price * 100
        opt_pnl = opt_exit_premium - opt_entry_premium
        opt_pnl_pct = opt_pnl / opt_entry_premium * 100 if opt_entry_premium > 0 else 0.0
        hold_hours = (last_time - active_trade["entry_time"]).total_seconds() / 3600
        
        active_trade["exit_time"] = last_time
        active_trade["exit_price"] = last_close
        active_trade["exit_reason"] = "Open"
        active_trade["pnl_points"] = pnl_points
        active_trade["pnl_pct"] = pnl_pct
        active_trade["opt_strike"] = opt_strike
        active_trade["opt_entry_premium"] = opt_entry_premium
        active_trade["opt_exit_premium"] = opt_exit_premium
        active_trade["opt_pnl"] = opt_pnl
        active_trade["opt_pnl_pct"] = opt_pnl_pct
        active_trade["opt_entry_delta"] = opt_entry_delta
        active_trade["opt_exit_delta"] = opt_exit_delta
        active_trade["opt_pnl_per_lot"] = opt_pnl * LOT_SIZE
        active_trade["hold_hours"] = hold_hours
        active_trade["peak_price"] = trail_high if active_trade["type"] == "LONG" else trail_low
        active_trade["stop_loss_final"] = stop_loss
        trades.append(active_trade)

    # ================================================================
    # RESULTS
    # ================================================================
    trades_df = pd.DataFrame(trades)
    
    if trades_df.empty:
        print("No trades triggered during backtest!")
        return
        
    trades_df['entry_date'] = trades_df['entry_time'].dt.strftime("%Y-%m-%d")
    trades_df['entry_time_str'] = trades_df['entry_time'].dt.strftime("%H:%M")
    trades_df['exit_date'] = trades_df['exit_time'].dt.strftime("%Y-%m-%d")
    trades_df['exit_time_str'] = trades_df['exit_time'].dt.strftime("%H:%M")
    trades_df['hold_days'] = (trades_df['hold_hours'] / 24).round(1)
    
    start_dt = pd.to_datetime(ANALYSIS_START_DATE)
    end_dt = pd.to_datetime(ANALYSIS_END_DATE) + timedelta(days=1)
    
    last_month = trades_df[(trades_df['entry_time'] >= start_dt) & (trades_df['entry_time'] < end_dt)].copy()
    
    # ========== LAST 1 MONTH TRADE LOG ==========
    print("\n" + "="*130)
    print(f" SPOT + OPTION TRADE LOG — LAST 1 MONTH ({ANALYSIS_START_DATE} to {ANALYSIS_END_DATE})")
    print("="*130)
    
    if last_month.empty:
        print("No trades triggered in the last 1 month.")
    else:
        hdr = (f"{'#':<3} {'Type':<5} {'Reason':<13} "
               f"{'Entry':<16} {'Exit':<16} "
               f"{'EntSpot':<9} {'ExSpot':<9} {'SpotPnL':<9} "
               f"{'Strike':<7} {'EntPrem':<8} {'ExPrem':<8} {'OptPnL':<8} {'Opt%':<7} "
               f"{'Delta':<6} {'Hold':<5} {'Status':<6}")
        print(hdr)
        print("-"*130)
        
        rev = last_month.iloc[::-1].reset_index(drop=True)
        for i, r in rev.iterrows():
            n = len(rev) - i
            ent = f"{r['entry_date'][5:]} {r['entry_time_str']}"
            ext = f"{r['exit_date'][5:]} {r['exit_time_str']}"
            print(f"{n:<3} {r['type']:<5} {r['entry_reason']:<13} "
                  f"{ent:<16} {ext:<16} "
                  f"{r['entry_price']:<9.1f} {r['exit_price']:<9.1f} {r['pnl_points']:>+8.1f} "
                  f"{int(r['opt_strike']):<7} {r['opt_entry_premium']:<8.1f} {r['opt_exit_premium']:<8.1f} {r['opt_pnl']:>+8.1f} {r['opt_pnl_pct']:>+6.1f}% "
                  f"{r['opt_entry_delta']:<6.2f} {r['hold_days']:<5} {r['exit_reason']:<6}")

    # ========== PERFORMANCE COMPARISON ==========
    def print_comparison(subset, label):
        if subset.empty:
            print(f"  No trades for {label}")
            return
        n = len(subset)
        
        # Spot metrics
        spot_wins = (subset['pnl_points'] > 0).sum()
        spot_total = subset['pnl_points'].sum()
        spot_avg = subset['pnl_points'].mean()
        spot_gp = subset.loc[subset['pnl_points'] > 0, 'pnl_points'].sum()
        spot_gl = abs(subset.loc[subset['pnl_points'] <= 0, 'pnl_points'].sum())
        spot_pf = spot_gp / spot_gl if spot_gl > 0 else float('inf')
        spot_eq = subset['pnl_points'].cumsum()
        spot_dd = (spot_eq.cummax() - spot_eq).max()
        
        # Option metrics
        opt_wins = (subset['opt_pnl'] > 0).sum()
        opt_total = subset['opt_pnl'].sum()
        opt_avg = subset['opt_pnl'].mean()
        opt_gp = subset.loc[subset['opt_pnl'] > 0, 'opt_pnl'].sum()
        opt_gl = abs(subset.loc[subset['opt_pnl'] <= 0, 'opt_pnl'].sum())
        opt_pf = opt_gp / opt_gl if opt_gl > 0 else float('inf')
        opt_eq = subset['opt_pnl'].cumsum()
        opt_dd = (opt_eq.cummax() - opt_eq).max()
        opt_total_lot = subset['opt_pnl_per_lot'].sum()
        
        # Average hold time
        avg_hold = subset['hold_hours'].mean()
        avg_delta = subset['opt_entry_delta'].mean()
        
        # Worst case analysis
        worst_opt_pct = subset['opt_pnl_pct'].min()
        worst_opt_rs = subset['opt_pnl'].min()
        best_opt_pct = subset['opt_pnl_pct'].max()
        
        print(f"\n  +{'='*60}+")
        print(f"  |  {label:<56}  |")
        print(f"  +{'='*60}+")
        print(f"  |  {'Metric':<30} {'Spot':>12} {'Option':>12}  |")
        print(f"  +{'='*60}+")
        print(f"  |  {'Total Trades':<30} {n:>12} {n:>12}  |")
        wl_spot = f'{spot_wins}/{n-spot_wins}'
        wl_opt = f'{opt_wins}/{n-opt_wins}'
        print(f"  |  {'Wins / Losses':<30} {wl_spot:>12} {wl_opt:>12}  |")
        wr_spot = f'{spot_wins/n*100:.1f}%'
        wr_opt = f'{opt_wins/n*100:.1f}%'
        print(f"  |  {'Win Rate':<30} {wr_spot:>12} {wr_opt:>12}  |")
        pnl_s = f'{spot_total:+.1f}'
        pnl_o = f'{opt_total:+.1f}'
        print(f"  |  {'Total PnL (Pts/Rs)':<30} {pnl_s:>12} {pnl_o:>12}  |")
        avg_s = f'{spot_avg:+.1f}'
        avg_o = f'{opt_avg:+.1f}'
        print(f"  |  {'Avg PnL per Trade':<30} {avg_s:>12} {avg_o:>12}  |")
        pf_s = f'{spot_pf:.2f}'
        pf_o = f'{opt_pf:.2f}'
        print(f"  |  {'Profit Factor':<30} {pf_s:>12} {pf_o:>12}  |")
        dd_s = f'{spot_dd:.1f}'
        dd_o = f'{opt_dd:.1f}'
        print(f"  |  {'Max Drawdown':<30} {dd_s:>12} {dd_o:>12}  |")
        print(f"  +{'='*60}+")
        print(f"  |  {'--- OPTION-SPECIFIC METRICS ---':<56}  |")
        print(f"  |  {'Avg Entry Delta':<30} {f'{avg_delta:.3f}':>25}  |")
        print(f"  |  {'Avg Hold Time (hours)':<30} {f'{avg_hold:.1f}':>25}  |")
        opt_lot_str = f'Rs.{opt_total_lot:+,.0f}'
        print(f"  |  {'Total PnL per Lot (Rs)':<30} {opt_lot_str:>25}  |")
        print(f"  |  {'Worst Trade (Option %)':<30} {f'{worst_opt_pct:+.1f}%':>25}  |")
        print(f"  |  {'Best Trade (Option %)':<30} {f'{best_opt_pct:+.1f}%':>25}  |")
        worst_rs_str = f'Rs.{worst_opt_rs:+.1f}'
        print(f"  |  {'Worst Trade (Rs/unit)':<30} {worst_rs_str:>25}  |")
        print(f"  +{'='*60}+")
    
    print("\n" + "="*130)
    print(" PERFORMANCE COMPARISON: SPOT vs OPTIONS")
    print("="*130)
    
    print_comparison(last_month, f"Last 1 Month ({ANALYSIS_START_DATE} to {ANALYSIS_END_DATE})")
    print_comparison(trades_df, "Full History (Jan 2022 - May 2026)")
    
    # ========== SUSTAINABILITY ANALYSIS ==========
    print("\n" + "="*130)
    print(" SUSTAINABILITY & RISK ANALYSIS")
    print("="*130)

    
    if not trades_df.empty:
        # Monthly breakdown
        trades_df['month'] = trades_df['entry_time'].dt.to_period('M')
        monthly = trades_df.groupby('month').agg(
            trades=('opt_pnl', 'count'),
            spot_pnl=('pnl_points', 'sum'),
            opt_pnl=('opt_pnl', 'sum'),
            opt_pnl_lot=('opt_pnl_per_lot', 'sum'),
            win_rate=('opt_pnl', lambda x: (x > 0).sum() / len(x) * 100),
            avg_delta=('opt_entry_delta', 'mean'),
        ).reset_index()
        
        print(f"\n  {'Month':<10} {'Trades':>6} {'SpotPnL':>10} {'OptPnL':>10} {'OptPnL/Lot':>12} {'WinRate':>8} {'AvgDelta':>9}")
        print("  " + "-"*70)
        for _, m in monthly.iterrows():
            lot_str = f"Rs.{m['opt_pnl_lot']:+,.0f}"
            print(f"  {str(m['month']):<10} {m['trades']:>6} {m['spot_pnl']:>+10.1f} {m['opt_pnl']:>+10.1f} {lot_str:>12} {m['win_rate']:>7.1f}% {m['avg_delta']:>8.3f}")
        
        # Gap risk analysis
        print(f"\n  --- Gap Risk (Overnight Holds) ---")
        overnight = trades_df[trades_df['hold_hours'] > 7].copy()
        if not overnight.empty:
            print(f"  Trades held overnight    : {len(overnight)}/{len(trades_df)} ({len(overnight)/len(trades_df)*100:.0f}%)")
            print(f"  Avg Option PnL (O/N)     : Rs.{overnight['opt_pnl'].mean():+.1f} per unit")
            print(f"  Worst O/N Option PnL     : Rs.{overnight['opt_pnl'].min():+.1f} per unit ({overnight['opt_pnl_pct'].min():+.1f}%)")
            print(f"  Total O/N Option PnL/Lot : Rs.{overnight['opt_pnl_per_lot'].sum():+,.0f}")
        else:
            print("  No overnight trades.")
            
        intraday = trades_df[trades_df['hold_hours'] <= 7].copy()
        if not intraday.empty:
            print(f"\n  --- Intraday Trades ---")
            print(f"  Trades closed same day   : {len(intraday)}/{len(trades_df)} ({len(intraday)/len(trades_df)*100:.0f}%)")
            print(f"  Avg Option PnL (Intra)   : Rs.{intraday['opt_pnl'].mean():+.1f} per unit")
            print(f"  Total Intra Option PnL   : Rs.{intraday['opt_pnl_per_lot'].sum():+,.0f} per lot")
    
    print("\n" + "="*130)
    print(" VERDICT")
    print("="*130)
    
    full_opt_total = trades_df['opt_pnl'].sum()
    full_opt_wins = (trades_df['opt_pnl'] > 0).sum()
    full_n = len(trades_df)
    full_opt_wr = full_opt_wins / full_n * 100
    full_opt_lot = trades_df['opt_pnl_per_lot'].sum()
    avg_decay_per_trade = trades_df['pnl_points'].mean() * trades_df['opt_entry_delta'].mean() - trades_df['opt_pnl'].mean()
    
    print(f"\n  Over {full_n} trades across 4.4 years:")
    print(f"  * Option Win Rate       : {full_opt_wr:.1f}%")
    print(f"  * Total Option PnL      : Rs.{full_opt_total:+.1f} per unit")
    lot_total_str = f'Rs.{full_opt_lot:+,.0f}'
    print(f"  * Total per Lot ({LOT_SIZE})    : {lot_total_str}")
    print(f"  * Avg Theta Decay/Trade : ~Rs.{avg_decay_per_trade:.1f} (premium lost to time)")
    print(f"  * Note: IV is dynamic from India VIX (vix.csv). Range: {df['iv'].min()*100:.1f}%-{df['iv'].max()*100:.1f}%")
    
    if full_opt_total > 0:
        print(f"\n  [OK] STRATEGY IS PROFITABLE IN OPTIONS over 4.4 years (net +Rs.{full_opt_total:.0f}/unit)")
    else:
        print(f"\n  [!!] STRATEGY IS NEGATIVE IN OPTIONS over 4.4 years (net Rs.{full_opt_total:.0f}/unit)")
        print(f"       Theta decay erodes {abs(avg_decay_per_trade):.1f} Rs/trade on average.")
    
    print()
    
    # Generate HTML Report
    generate_html_report(trades_df, monthly, full_opt_total, full_opt_lot, full_opt_wr, avg_decay_per_trade)

def generate_html_report(trades_df, monthly_df, full_opt_total, full_opt_lot, full_opt_wr, avg_decay):
    # Calculate Sharpe & Sortino based on daily returns
    # Assuming capital of Rs. 50,000 per lot to trade safely
    CAPITAL = 50000.0
    
    # Aggregate PnL daily
    trades_df['exit_day'] = trades_df['exit_time'].dt.normalize()
    daily_pnl = trades_df.groupby('exit_day')['opt_pnl_per_lot'].sum().reset_index()
    
    # Create complete date range for the year
    start_date = trades_df['entry_time'].min().normalize()
    end_date = trades_df['exit_time'].max().normalize()
    date_range = pd.date_range(start=start_date, end=end_date, freq='D')
    
    daily_df = pd.DataFrame({'date': date_range})
    daily_df = daily_df.merge(daily_pnl, left_on='date', right_on='exit_day', how='left').fillna(0)
    
    # Daily returns
    daily_df['return'] = daily_df['opt_pnl_per_lot'] / CAPITAL
    
    # Daily RF (assuming 7% annual / 365)
    daily_rf = 0.07 / 365
    
    excess_returns = daily_df['return'] - daily_rf
    mean_excess = excess_returns.mean()
    std_dev = daily_df['return'].std()
    
    # Annualized Sharpe
    if std_dev > 0:
        sharpe = (mean_excess / std_dev) * np.sqrt(365)
    else:
        sharpe = 0.0
        
    # Annualized Sortino
    downside_returns = daily_df.loc[daily_df['return'] < 0, 'return']
    downside_std = downside_returns.std() if len(downside_returns) > 0 else 0
    if downside_std > 0:
        sortino = (mean_excess / downside_std) * np.sqrt(365)
    else:
        sortino = 0.0
        
    # Calculate Average Profit and Average Loss
    opt_wins = trades_df[trades_df['opt_pnl'] > 0]
    opt_losses = trades_df[trades_df['opt_pnl'] <= 0]
    
    avg_opt_win_pts = opt_wins['opt_pnl'].mean() if len(opt_wins) > 0 else 0.0
    avg_opt_win_rs = opt_wins['opt_pnl_per_lot'].mean() if len(opt_wins) > 0 else 0.0
    
    avg_opt_loss_pts = opt_losses['opt_pnl'].mean() if len(opt_losses) > 0 else 0.0
    avg_opt_loss_rs = opt_losses['opt_pnl_per_lot'].mean() if len(opt_losses) > 0 else 0.0
    
    spot_wins = trades_df[trades_df['pnl_points'] > 0]
    spot_losses = trades_df[trades_df['pnl_points'] <= 0]
    
    avg_spot_win_pts = spot_wins['pnl_points'].mean() if len(spot_wins) > 0 else 0.0
    avg_spot_loss_pts = spot_losses['pnl_points'].mean() if len(spot_losses) > 0 else 0.0
        
    # Spot vs Option cumulative lists for plotting
    trades_sorted = trades_df.sort_values('entry_time').reset_index(drop=True)
    trades_sorted['cum_spot_pnl'] = trades_sorted['pnl_points'].cumsum()
    trades_sorted['cum_opt_pnl'] = (trades_sorted['opt_pnl_per_lot']).cumsum()
    
    dates_js = [t.strftime("%Y-%m-%d %H:%M") for t in trades_sorted['entry_time']]
    spot_js = list(trades_sorted['cum_spot_pnl'].round(1))
    opt_js = list(trades_sorted['cum_opt_pnl'].round(0))
    
    # Monthly JS arrays
    monthly_sorted = monthly_df.sort_values('month').reset_index(drop=True)
    months_labels = [str(m) for m in monthly_sorted['month']]
    monthly_spot_pnl = list(monthly_sorted['spot_pnl'].round(1))
    monthly_opt_pnl = list(monthly_sorted['opt_pnl_lot'].round(0))
    
    # ROI, Brokerages, Monthly Win/Loss Streaks, expectancies, etc.
    CAPITAL_ROI = 50000.0  # recommended safety capital per lot
    
    # 1. Brokerages
    brokerage_per_trade = 60.0
    total_brokerage = len(trades_df) * brokerage_per_trade
    estimated_net_pnl = full_opt_lot - total_brokerage
    
    # 2. ROI
    absolute_roi = (full_opt_lot / CAPITAL_ROI) * 100
    # Annualized ROI (assuming 4.4 years backtest)
    annualized_roi = absolute_roi / 4.4
    
    # 3. Monthly returns
    pos_months = monthly_sorted[monthly_sorted['opt_pnl_lot'] > 0]
    neg_months = monthly_sorted[monthly_sorted['opt_pnl_lot'] < 0]
    avg_monthly_win_rs = pos_months['opt_pnl_lot'].mean() if len(pos_months) > 0 else 0.0
    avg_monthly_win_pct = (avg_monthly_win_rs / CAPITAL_ROI) * 100
    
    avg_monthly_loss_rs = neg_months['opt_pnl_lot'].mean() if len(neg_months) > 0 else 0.0
    avg_monthly_loss_pct = (avg_monthly_loss_rs / CAPITAL_ROI) * 100
    
    # Average monthly return across all months
    avg_monthly_all_rs = monthly_sorted['opt_pnl_lot'].mean() if len(monthly_sorted) > 0 else 0.0
    avg_monthly_all_pct = (avg_monthly_all_rs / CAPITAL_ROI) * 100
    
    # 4. Max Streak of Monthly Losses
    streak = 0
    max_monthly_loss_streak = 0
    for val in monthly_sorted['opt_pnl_lot']:
        if val < 0:
            streak += 1
            max_monthly_loss_streak = max(max_monthly_loss_streak, streak)
        else:
            streak = 0
            
    # 5. Trade Streaks
    wins_streak = 0
    max_wins_streak = 0
    losses_streak = 0
    max_losses_streak = 0
    for val in trades_df['opt_pnl']:
        if val > 0:
            wins_streak += 1
            max_wins_streak = max(max_wins_streak, wins_streak)
            losses_streak = 0
        else:
            losses_streak += 1
            max_losses_streak = max(max_losses_streak, losses_streak)
            wins_streak = 0
            
    # 6. Recovery Factor
    max_drawdown_rs = (trades_df['opt_pnl_per_lot'].cumsum().cummax() - trades_df['opt_pnl_per_lot'].cumsum()).max()
    recovery_factor = full_opt_lot / max_drawdown_rs if max_drawdown_rs > 0 else 0.0
    
    # 7. Expectancy (Rs. per trade)
    expectancy_rs = trades_df['opt_pnl_per_lot'].mean()
    
    # Calmar and Martin Ratios
    # 1. Calmar Ratio
    calmar = (annualized_roi) / (max_drawdown_rs / CAPITAL_ROI * 100) if max_drawdown_rs > 0 else 0.0
    
    # 2. Ulcer Index & Martin Ratio
    daily_df['cum_pnl'] = daily_df['opt_pnl_per_lot'].cumsum()
    daily_df['equity'] = CAPITAL_ROI + daily_df['cum_pnl']
    daily_df['peak'] = daily_df['equity'].cummax()
    daily_df['dd_pct'] = (daily_df['peak'] - daily_df['equity']) / CAPITAL_ROI * 100
    ulcer_index = np.sqrt((daily_df['dd_pct'] ** 2).mean())
    
    martin = (annualized_roi - 7.0) / ulcer_index if ulcer_index > 0 else 0.0
    
    # 8. Top 5 winning and losing trades
    top_5_wins = trades_df.sort_values(by='opt_pnl_per_lot', ascending=False).head(5)
    top_5_losses = trades_df.sort_values(by='opt_pnl_per_lot', ascending=True).head(5)
    
    top_wins_rows = ""
    for idx, r in top_5_wins.iterrows():
        chronological_idx = trades_sorted[trades_sorted['entry_time'] == r['entry_time']].index[0]
        trade_num = len(trades_sorted) - chronological_idx
        status_cls = "badge-success" if r['pnl_points'] > 0 else "badge-danger"
        opt_pnl_cls = "text-success" if r['opt_pnl'] > 0 else "text-danger"
        spot_pnl_cls = "text-success" if r['pnl_points'] > 0 else "text-danger"
        
        top_wins_rows += f"""
        <tr>
            <td>{trade_num}</td>
            <td><span class="badge { 'badge-primary' if r['type'] == 'LONG' else 'badge-secondary' }">{r['type']}</span></td>
            <td>{r['entry_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td>{r['exit_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td class="{spot_pnl_cls} font-weight-bold">{r['pnl_points']:+.1f}</td>
            <td class="font-weight-bold">{int(r['opt_strike'])}</td>
            <td class="{opt_pnl_cls} font-weight-bold">{r['opt_pnl']:+.1f} ({r['opt_pnl_pct']:+.1f}%)</td>
            <td class="{opt_pnl_cls} font-weight-bold">Rs.{r['opt_pnl_per_lot']:+,.0f}</td>
            <td>{r['hold_days']}</td>
            <td><span class="badge {status_cls}">{r['exit_reason']}</span></td>
        </tr>
        """
        
    top_losses_rows = ""
    for idx, r in top_5_losses.iterrows():
        chronological_idx = trades_sorted[trades_sorted['entry_time'] == r['entry_time']].index[0]
        trade_num = len(trades_sorted) - chronological_idx
        status_cls = "badge-success" if r['pnl_points'] > 0 else "badge-danger"
        opt_pnl_cls = "text-success" if r['opt_pnl'] > 0 else "text-danger"
        spot_pnl_cls = "text-success" if r['pnl_points'] > 0 else "text-danger"
        
        top_losses_rows += f"""
        <tr>
            <td>{trade_num}</td>
            <td><span class="badge { 'badge-primary' if r['type'] == 'LONG' else 'badge-secondary' }">{r['type']}</span></td>
            <td>{r['entry_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td>{r['exit_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td class="{spot_pnl_cls} font-weight-bold">{r['pnl_points']:+.1f}</td>
            <td class="font-weight-bold">{int(r['opt_strike'])}</td>
            <td class="{opt_pnl_cls} font-weight-bold">{r['opt_pnl']:+.1f} ({r['opt_pnl_pct']:+.1f}%)</td>
            <td class="{opt_pnl_cls} font-weight-bold">Rs.{r['opt_pnl_per_lot']:+,.0f}</td>
            <td>{r['hold_days']}</td>
            <td><span class="badge {status_cls}">{r['exit_reason']}</span></td>
        </tr>
        """
        
    # Generate detailed trade HTML rows
    trade_rows = ""
    for idx, r in trades_sorted.iloc[::-1].iterrows():
        status_cls = "badge-success" if r['pnl_points'] > 0 else "badge-danger"
        opt_pnl_cls = "text-success" if r['opt_pnl'] > 0 else "text-danger"
        spot_pnl_cls = "text-success" if r['pnl_points'] > 0 else "text-danger"
        
        trade_rows += f"""
        <tr>
            <td>{len(trades_sorted) - idx}</td>
            <td><span class="badge { 'badge-primary' if r['type'] == 'LONG' else 'badge-secondary' }">{r['type']}</span></td>
            <td>{r['entry_reason']}</td>
            <td>{r['entry_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td>{r['exit_time'].strftime('%Y-%m-%d %H:%M')}</td>
            <td>{r['entry_price']:.1f}</td>
            <td>{r['exit_price']:.1f}</td>
            <td class="{spot_pnl_cls} font-weight-bold">{r['pnl_points']:+.1f}</td>
            <td class="font-weight-bold">{int(r['opt_strike'])}</td>
            <td>{r['opt_entry_premium']:.1f}</td>
            <td>{r['opt_exit_premium']:.1f}</td>
            <td class="{opt_pnl_cls} font-weight-bold">{r['opt_pnl']:+.1f} ({r['opt_pnl_pct']:+.1f}%)</td>
            <td class="{opt_pnl_cls} font-weight-bold">Rs.{r['opt_pnl_per_lot']:+,.0f}</td>
            <td>{r['opt_entry_delta']:.2f}</td>
            <td>{r['hold_days']}</td>
            <td><span class="badge {status_cls}">{r['exit_reason']}</span></td>
        </tr>
        """
        
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nifty 5m Triple Confirm Backtest Report</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.7);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #10b981;
            --primary-glow: rgba(16, 185, 129, 0.15);
            --secondary: #3b82f6;
            --secondary-glow: rgba(59, 130, 246, 0.15);
            --accent: #f59e0b;
            --danger: #ef4444;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --text-dim: #6b7280;
        }}

        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            background-color: var(--bg-color);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            background-image: 
                radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.08) 0px, transparent 50%),
                radial-gradient(at 50% 0%, rgba(16, 185, 129, 0.05) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(245, 158, 11, 0.03) 0px, transparent 50%);
            background-attachment: fixed;
            min-height: 100vh;
            padding-bottom: 50px;
        }}

        header {{
            padding: 40px 5% 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--card-border);
            backdrop-filter: blur(10px);
            background: rgba(11, 15, 25, 0.5);
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .logo-section h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(to right, #10b981, #3b82f6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }}

        .logo-section p {{
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 20px;
        }}

        .alert-card {{
            background: linear-gradient(135deg, rgba(59, 130, 246, 0.1) 0%, rgba(16, 185, 129, 0.05) 100%);
            border: 1px solid rgba(59, 130, 246, 0.2);
            border-left: 5px solid var(--secondary);
            border-radius: 12px;
            padding: 20px;
            margin: 30px 0;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}

        .alert-card h3 {{
            font-family: 'Outfit', sans-serif;
            font-size: 18px;
            color: #60a5fa;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .alert-card p {{
            font-size: 14px;
            line-height: 1.6;
            color: var(--text-main);
        }}

        .alert-card strong {{
            color: #38bdf8;
        }}

        .grid-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}

        .card-stat {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(16px);
            position: relative;
            overflow: hidden;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }}

        .card-stat:hover {{
            transform: translateY(-5px);
            border-color: rgba(255, 255, 255, 0.15);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        }}

        .card-stat::after {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
        }}

        .card-stat.primary::after {{ background-color: var(--primary); }}
        .card-stat.secondary::after {{ background-color: var(--secondary); }}
        .card-stat.accent::after {{ background-color: var(--accent); }}
        .card-stat.danger::after {{ background-color: var(--danger); }}

        .stat-label {{
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-muted);
            margin-bottom: 8px;
        }}

        .stat-value {{
            font-family: 'Outfit', sans-serif;
            font-size: 28px;
            font-weight: 700;
            margin-bottom: 6px;
        }}

        .stat-sub {{
            font-size: 11px;
            color: var(--text-dim);
        }}

        .text-success {{ color: #10b981 !important; }}
        .text-danger {{ color: #ef4444 !important; }}

        .grid-charts {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}

        @media (max-width: 1024px) {{
            .grid-charts {{
                grid-template-columns: 1fr;
            }}
        }}

        .card-chart {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(16px);
        }}

        .card-chart h2 {{
            font-family: 'Outfit', sans-serif;
            font-size: 18px;
            margin-bottom: 20px;
            color: var(--text-main);
        }}

        .chart-container {{
            position: relative;
            height: 350px;
            width: 100%;
        }}

        .card-table {{
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 24px;
            backdrop-filter: blur(16px);
            margin-bottom: 30px;
            overflow: hidden;
        }}

        .card-table h2 {{
            font-family: 'Outfit', sans-serif;
            font-size: 18px;
            margin-bottom: 20px;
        }}

        .table-responsive {{
            overflow-x: auto;
            max-height: 500px;
            overflow-y: auto;
            border-radius: 8px;
            border: 1px solid var(--card-border);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}

        th {{
            background-color: rgba(255, 255, 255, 0.03);
            color: var(--text-muted);
            padding: 12px 16px;
            font-weight: 600;
            border-bottom: 1px solid var(--card-border);
            position: sticky;
            top: 0;
            z-index: 10;
            backdrop-filter: blur(5px);
        }}

        td {{
            padding: 12px 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            color: var(--text-main);
        }}

        tr:hover td {{
            background-color: rgba(255, 255, 255, 0.015);
        }}

        .badge {{
            display: inline-block;
            padding: 4px 8px;
            font-size: 10px;
            font-weight: 600;
            border-radius: 4px;
            text-transform: uppercase;
        }}

        .badge-primary {{
            background-color: rgba(16, 185, 129, 0.15);
            color: #10b981;
        }}

        .badge-secondary {{
            background-color: rgba(239, 68, 68, 0.15);
            color: #ef4444;
        }}

        .badge-success {{
            background-color: rgba(59, 130, 246, 0.15);
            color: #3b82f6;
        }}

        .badge-danger {{
            background-color: rgba(245, 158, 11, 0.15);
            color: #f59e0b;
        }}

        .font-weight-bold {{
            font-weight: 600;
        }}

        /* Scrollbar styles */
        ::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        ::-webkit-scrollbar-track {{
            background: rgba(0, 0, 0, 0.1);
        }}
        ::-webkit-scrollbar-thumb {{
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }}
        ::-webkit-scrollbar-thumb:hover {{
            background: rgba(255, 255, 255, 0.2);
        }}

        .grid-top-trades {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 30px;
        }}
        @media (max-width: 1200px) {{
            .grid-top-trades {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>

    <header>
        <div class="logo-section">
            <h1>NIFTY 5M TRIPLE CONFIRM</h1>
            <p>4.4-Year Options Simulation & Analytics Report (Jan 2022 - Apr 2026)</p>
        </div>
        <div>
            <span class="badge badge-primary" style="font-size:12px; padding: 6px 12px;">IV: Dynamic (India VIX) | DIST=0.0% | RE_ENTRY=0.0%</span>
        </div>
    </header>

    <div class="container">
        
        <!-- Explanation Alert Card -->
        <div class="alert-card">
            <h3>
                <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>
                4.4-Year Backtest: Key Observations
            </h3>
            <p>
                This report covers <strong>{len(trades_df)} trades from January 2022 to May 2026</strong> (4.4 years). 
                The strategy is trend-following, so it thrives during strong directional moves and suffers during sideways/choppy phases. 
                Option performance is simulated assuming constant IV of 14% and weekly Tuesday expiry. 
                We recommend maintaining at least <strong>Rs. 1.5 Lakhs capital per lot</strong> to safely sustain standard drawdown phases.
            </p>
        </div>

        <!-- Performance & Brokerage Grid -->
        <h2 style="font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 600; margin: 30px 0 15px; color: #60a5fa; display: flex; align-items: center; gap: 8px;">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17h-2v-2h2v2zm2.07-7.75l-.9.92C13.45 12.9 13 13.5 13 15h-2v-.5c0-1.1.45-2.1 1.17-2.83l1.24-1.26c.37-.36.59-.86.59-1.41 0-1.1-.9-2-2-2s-2 .9-2 2H7c0-2.76 2.24-5 5-5s5 2.24 5 5c0 1.04-.42 1.99-1.07 2.75z"/></svg>
            Performance & Brokerage
        </h2>
        <div class="grid-stats">
            <div class="card-stat primary">
                <div class="stat-label">Gross Option Profit</div>
                <div class="stat-value text-success">Rs. {full_opt_lot:+,.0f}</div>
                <div class="stat-sub">+{full_opt_total:+.1f} Points per Unit (1 Lot = {LOT_SIZE} Qty)</div>
            </div>
            
            <div class="card-stat primary">
                <div class="stat-label">Total Brokerage</div>
                <div class="stat-value text-danger">Rs. {total_brokerage:,.0f}</div>
                <div class="stat-sub">Rs. 60 per trade (Buy + Sell execution)</div>
            </div>

            <div class="card-stat primary">
                <div class="stat-label">Estimated Net PnL</div>
                <div class="stat-value text-success">Rs. {estimated_net_pnl:+,.0f}</div>
                <div class="stat-sub">Gross PnL minus Brokerages</div>
            </div>

            <div class="card-stat primary">
                <div class="stat-label">Spot Net Profit</div>
                <div class="stat-value text-success">+{trades_df['pnl_points'].sum():+,.1f} Pts</div>
                <div class="stat-sub">Index Point Gains (Spot)</div>
            </div>
        </div>

        <!-- ROI & Monthly Analytics Grid -->
        <h2 style="font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 600; margin: 30px 0 15px; color: #60a5fa; display: flex; align-items: center; gap: 8px;">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-2 10h-4v4h-2v-4H7v-2h4V7h2v4h4v2z"/></svg>
            ROI & Monthly Analytics
        </h2>
        <div class="grid-stats">
            <div class="card-stat secondary">
                <div class="stat-label">Gross Option ROI</div>
                <div class="stat-value">{absolute_roi:.1f}%</div>
                <div class="stat-sub">Safety Capital: Rs. 1.5 Lakhs | {annualized_roi:.1f}% Annualized</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Avg Monthly Return</div>
                <div class="stat-value text-success">Rs. {avg_monthly_all_rs:+,.0f}</div>
                <div class="stat-sub">{avg_monthly_all_pct:+.1f}% average return across all months</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Avg Win / Loss Month</div>
                <div class="stat-value">Rs. {avg_monthly_win_rs:+,.0f} / {avg_monthly_loss_rs:,.0f}</div>
                <div class="stat-sub">+{avg_monthly_win_pct:+.1f}% / {avg_monthly_loss_pct:+.1f}% average on capital</div>
            </div>

            <div class="card-stat danger">
                <div class="stat-label">Max Monthly Loss Streak</div>
                <div class="stat-value">{max_monthly_loss_streak} Months</div>
                <div class="stat-sub">Max consecutive months with negative PnL</div>
            </div>
        </div>

        <!-- Risk & Trade-Level Ratios Grid -->
        <h2 style="font-family: 'Outfit', sans-serif; font-size: 18px; font-weight: 600; margin: 30px 0 15px; color: #60a5fa; display: flex; align-items: center; gap: 8px;">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            Risk & Trade-Level Ratios
        </h2>
        <div class="grid-stats">
            <div class="card-stat secondary">
                <div class="stat-label">Option Win Rate</div>
                <div class="stat-value">{full_opt_wr:.1f}%</div>
                <div class="stat-sub">{ (trades_df['opt_pnl'] > 0).sum() } Wins / { (trades_df['opt_pnl'] <= 0).sum() } Losses</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Avg Trade Win / Loss</div>
                <div class="stat-value">Rs. {avg_opt_win_rs:+,.0f} / {avg_opt_loss_rs:,.0f}</div>
                <div class="stat-sub">+{avg_opt_win_pts:+.1f} Pts / {avg_opt_loss_pts:+.1f} Pts Option (Avg)</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Sharpe / Sortino</div>
                <div class="stat-value">{sharpe:.2f} / {sortino:.2f}</div>
                <div class="stat-sub">Daily Annualized Sharpe & Sortino Ratios</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Calmar / Martin</div>
                <div class="stat-value">{calmar:.2f} / {martin:.2f}</div>
                <div class="stat-sub">Ulcer Index: {ulcer_index:.2f}% | Risk-Free: 7.0%</div>
            </div>

            <div class="card-stat danger">
                <div class="stat-label">Max Drawdown</div>
                <div class="stat-value">Rs. {max_drawdown_rs:,.0f}</div>
                <div class="stat-sub">Recovery Factor: {recovery_factor:.2f} | Expectancy: Rs. {expectancy_rs:+,.0f}/trade</div>
            </div>

            <div class="card-stat secondary">
                <div class="stat-label">Max Streak (W / L)</div>
                <div class="stat-value">{max_wins_streak} / {max_losses_streak} Trades</div>
                <div class="stat-sub">Max consecutive winning / losing trade streaks</div>
            </div>

            <div class="card-stat primary">
                <div class="stat-label">Total Trades</div>
                <div class="stat-value">{len(trades_df)}</div>
                <div class="stat-sub">{ (trades_df['opt_pnl'] > 0).sum() } Wins &nbsp;|&nbsp; { (trades_df['opt_pnl'] <= 0).sum() } Losses &nbsp;|&nbsp; {full_opt_wr:.1f}% Win Rate</div>
            </div>
        </div>

        <!-- Charts Grid -->
        <div class="grid-charts">
            <div class="card-chart">
                <h2>Cumulative Profit Curve (1 Lot Options vs Spot Points)</h2>
                <div class="chart-container">
                    <canvas id="equityChart"></canvas>
                </div>
            </div>
            <div class="card-chart">
                <h2>Monthly Lot Profit Breakdown (Rs.)</h2>
                <div class="chart-container">
                    <canvas id="monthlyChart"></canvas>
                </div>
            </div>
        </div>

        <!-- Monthly Table -->
        <div class="card-table">
            <h2>Monthly Aggregate breakdown</h2>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>Month</th>
                            <th>Trades Triggered</th>
                            <th>Spot PnL (Points)</th>
                            <th>Option PnL (Points/Unit)</th>
                            <th>Option PnL per Lot (Rs.)</th>
                            <th>Win Rate %</th>
                            <th>Avg Entry Delta</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        
    for _, m in monthly_sorted.iloc[::-1].iterrows():
        pnl_cls = "text-success" if m['opt_pnl'] > 0 else "text-danger"
        spot_pnl_cls = "text-success" if m['spot_pnl'] > 0 else "text-danger"
        html_content += f"""
                        <tr>
                            <td class="font-weight-bold">{m['month']}</td>
                            <td>{m['trades']}</td>
                            <td class="{spot_pnl_cls}">{m['spot_pnl']:+.1f}</td>
                            <td class="{pnl_cls}">{m['opt_pnl']:+.1f}</td>
                            <td class="{pnl_cls} font-weight-bold">Rs.{m['opt_pnl_lot']:+,.0f}</td>
                            <td>{m['win_rate']:.1f}%</td>
                            <td>{m['avg_delta']:.3f}</td>
                        </tr>
        """
        
    html_content += f"""
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Top 5 Wins and Losses -->
        <div class="grid-top-trades">
            <div class="card-table" style="margin-bottom: 0;">
                <h2 style="color: var(--primary); display: flex; align-items: center; gap: 8px;">
                    <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z"/></svg>
                    Top 5 Winning Trades (Options)
                </h2>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Type</th>
                                <th>Entry Datetime</th>
                                <th>Exit Datetime</th>
                                <th>Spot PnL</th>
                                <th>Strike</th>
                                <th>Opt PnL</th>
                                <th>Opt PnL (1 Lot)</th>
                                <th>Hold (Days)</th>
                                <th>Exit Reason</th>
                            </tr>
                        </thead>
                        <tbody>
                            {top_wins_rows}
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="card-table" style="margin-bottom: 0;">
                <h2 style="color: var(--danger); display: flex; align-items: center; gap: 8px;">
                    <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
                    Top 5 Losing Trades (Options)
                </h2>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>#</th>
                                <th>Type</th>
                                <th>Entry Datetime</th>
                                <th>Exit Datetime</th>
                                <th>Spot PnL</th>
                                <th>Strike</th>
                                <th>Opt PnL</th>
                                <th>Opt PnL (1 Lot)</th>
                                <th>Hold (Days)</th>
                                <th>Exit Reason</th>
                            </tr>
                        </thead>
                        <tbody>
                            {top_losses_rows}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Detailed Trade Log -->
        <div class="card-table">
            <h2>Detailed Trades Log (Jan 2022 - May 2026)</h2>
            <div class="table-responsive">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Type</th>
                            <th>Reason</th>
                            <th>Entry Datetime</th>
                            <th>Exit Datetime</th>
                            <th>Entry Spot</th>
                            <th>Exit Spot</th>
                            <th>Spot PnL</th>
                            <th>Strike</th>
                            <th>Ent Premium</th>
                            <th>Ex Premium</th>
                            <th>Opt PnL</th>
                            <th>Opt PnL (1 Lot)</th>
                            <th>Delta</th>
                            <th>Hold (Days)</th>
                            <th>Exit Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        {trade_rows}
                    </tbody>
                </table>
            </div>
        </div>

    </div>

    <script>
        // Equity Chart
        const equityCtx = document.getElementById('equityChart').getContext('2d');
        new Chart(equityCtx, {{
            type: 'line',
            data: {{
                labels: {dates_js},
                datasets: [
                    {{
                        label: 'Option Lot PnL (Rs. - Left Axis)',
                        data: {opt_js},
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.05)',
                        fill: true,
                        tension: 0.1,
                        yAxisID: 'yOpt'
                    }},
                    {{
                        label: 'Spot PnL (Points - Right Axis)',
                        data: {spot_js},
                        borderColor: '#3b82f6',
                        backgroundColor: 'transparent',
                        fill: false,
                        tension: 0.1,
                        yAxisID: 'ySpot'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        labels: {{ color: '#f3f4f6', font: {{ family: 'Inter' }} }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.03)' }},
                        ticks: {{ color: '#9ca3af', font: {{ family: 'Inter', size: 10 }} }}
                    }},
                    yOpt: {{
                        position: 'left',
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{
                            color: '#10b981',
                            font: {{ family: 'Inter' }},
                            callback: function(value) {{ return 'Rs.' + value.toLocaleString(); }}
                        }}
                    }},
                    ySpot: {{
                        position: 'right',
                        grid: {{ drawOnChartArea: false }},
                        ticks: {{
                            color: '#3b82f6',
                            font: {{ family: 'Inter' }},
                            callback: function(value) {{ return value + ' pts'; }}
                        }}
                    }}
                }}
            }}
        }});

        // Monthly Bar Chart
        const monthlyCtx = document.getElementById('monthlyChart').getContext('2d');
        new Chart(monthlyCtx, {{
            type: 'bar',
            data: {{
                labels: {months_labels},
                datasets: [{{
                    label: 'Option Lot PnL (Rs.)',
                    data: {monthly_opt_pnl},
                    backgroundColor: {monthly_opt_pnl}.map(val => val >= 0 ? 'rgba(16, 185, 129, 0.7)' : 'rgba(239, 68, 68, 0.7)'),
                    borderColor: {monthly_opt_pnl}.map(val => val >= 0 ? '#10b981' : '#ef4444'),
                    borderWidth: 1
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255,255,255,0.03)' }},
                        ticks: {{ color: '#9ca3af', font: {{ family: 'Inter' }} }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255,255,255,0.05)' }},
                        ticks: {{
                            color: '#9ca3af',
                            font: {{ family: 'Inter' }},
                            callback: function(value) {{ return 'Rs.' + value.toLocaleString(); }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    
    with open("nifty_report.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print("\n" + "="*80)
    print(" [OK] HTML REPORT GENERATED SUCCESSFULLY: nifty_report.html")
    print(f"  Sharpe Ratio: {sharpe:.2f} | Sortino Ratio: {sortino:.2f}")
    print(f"  Calmar Ratio: {calmar:.2f} | Martin Ratio: {martin:.2f}")
    print("="*80)


if __name__ == "__main__":
    run_backtest()
