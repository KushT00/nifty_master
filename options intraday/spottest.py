import os
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, time, timedelta

# ==========================================
# 1. Backtest Configurations & Settings
# ==========================================
CONFIG = {
    # File Paths
    'spot_path': 'c:/Users/Kush Tejani/Downloads/backtest/NIFTY_NSE_INDEX_5m.csv',
    'vix_path': 'c:/Users/Kush Tejani/Downloads/backtest/vix.csv',
    'trade_log_filename': 'c:/Users/Kush Tejani/Downloads/backtest/trade_log.csv',
    'equity_curve_filename': 'c:/Users/Kush Tejani/Downloads/backtest/equity_curve.png',
    'report_filename': 'c:/Users/Kush Tejani/Downloads/backtest/report.html',
    
    # Capital Settings
    'initial_capital': 50000.0,       # Rs. 50,000 initial capital
    'dynamic_lot_size': False,        # Fixed lot size as per user request
    'fixed_lot_size': 65,            # Lot size is 130 (2 lots of 65)
    
    # Options target premium setting
    'target_premium': 200.0,          # Buy option closest to 200 premium
    
    # Trading Costs (Slippages, Brokerage, etc.)
    'slippage_points_per_order': 0.3, # 0.3 points slippage per order
    'brokerage_per_order': 20.0,      # Flat Rs. 20 per order (Rs. 40 round-trip)
    
    # Strategy Inputs
    'atr_period': 10,
    'supertrend_multiplier': 3.0,
    
    # Session Times
    'session_start_str': '1130',      # HHMM
    'session_end_str': '1445',        # HHMM (Filters out late whipsaws)
    'squareoff_time_str': '1520',     # HHMM (Intraday close begins here)
    
    # Exits & SL/TP (Spot points)
    'spot_sl_points': 50.0,
    'spot_tp_points': 150.0,          # Captures large trend runs
    
    # Breakout & ATR Filters
    'filter_atr_period': 20,          # ATR filter period
    'min_atr_value': 12.0,            # Minimum ATR value
    'candle_min_body_ratio': 0.30,    # Minimum body ratio
    'candle_min_close_pos': 0.85,     # Minimum close position
    
    # Option trade configuration
    'risk_free_rate': 0.07,           # 7% risk-free interest rate
    'expiry_weekday': 1,              # 1 = Tuesday Expiry for Nifty weekly options
}

# ==========================================
# 2. Black-Scholes Pricing & Delta
# ==========================================
def normal_cdf(x):
    """Cumulative distribution function for the standard normal distribution."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def black_scholes_call(S, K, t_years, r, sigma):
    """Calculates the price of a European Call option using Black-Scholes formula."""
    if S <= 0 or K <= 0:
        return 0.0
    if t_years <= 0:
        return max(0.0, S - K)
    if sigma <= 0:
        sigma = 1e-4
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * t_years) / (sigma * math.sqrt(t_years))
    d2 = d1 - sigma * math.sqrt(t_years)
    return S * normal_cdf(d1) - K * math.exp(-r * t_years) * normal_cdf(d2)

def calculate_bs_delta(S, K, t_years, r, sigma):
    """Calculates the option delta for a European Call."""
    if S <= 0 or K <= 0:
        return 0.0
    if t_years <= 0:
        return 1.0 if S >= K else 0.0
    if sigma <= 0:
        sigma = 1e-4
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * t_years) / (sigma * math.sqrt(t_years))
    return normal_cdf(d1)

def find_strike_near_premium(S, t_years, r, sigma, target_premium=200.0):
    """Finds the Nifty strike (multiple of 50) closest to the target premium."""
    # Find ATM strike as a baseline
    atm_strike = int(round(S / 50.0) * 50)
    
    best_strike = atm_strike
    min_diff = float('inf')
    
    # Check strikes in a range of -1500 to +1500 around ATM (steps of 50)
    for k in range(atm_strike - 1500, atm_strike + 1501, 50):
        prem = black_scholes_call(S, k, t_years, r, sigma)
        diff = abs(prem - target_premium)
        if diff < min_diff:
            min_diff = diff
            best_strike = k
            
    return best_strike

# ==========================================
# 3. Technical Indicator Library
# ==========================================
def calculate_rma(series, period):
    """Wilder's RMA moving average (same as ta.rma in TradingView)."""
    alpha = 1.0 / period
    rma = np.zeros(len(series))
    if len(series) > 0:
        rma[period-1] = np.mean(series[:period])
        for i in range(period, len(series)):
            rma[i] = alpha * series[i] + (1.0 - alpha) * rma[i-1]
        rma[:period-1] = np.nan
    return pd.Series(rma, index=series.index)

# ==========================================
# 4. Weekly Expiry Helper & Lot Size Helper
# ==========================================
def get_tuesday_expiry(date_obj, time_obj, expiry_weekday=1):
    """Calculates the upcoming weekly Tuesday expiry date."""
    wd = date_obj.weekday()
    if wd == expiry_weekday:
        if time_obj >= time(15, 30):
            days_to_expiry = 7
        else:
            days_to_expiry = 0
    elif wd < expiry_weekday:
        days_to_expiry = expiry_weekday - wd
    else:
        days_to_expiry = 7 - (wd - expiry_weekday)
    return date_obj + timedelta(days=days_to_expiry)

def get_lot_size(date_obj):
    """Returns the historical lot size for Nifty based on the date."""
    if not CONFIG['dynamic_lot_size']:
        return CONFIG['fixed_lot_size']
    # Nifty lot size changed from 50 to 25 starting April 26, 2024
    if date_obj >= pd.to_datetime('2024-04-26').date():
        return 25
    return 50

# ==========================================
# 5. Trading Costs Calculator (India Options)
# ==========================================
def calculate_costs(entry_prem, exit_prem, lot_size):
    """Calculates brokerage, slippage, and statutory taxes/charges for option buying."""
    # Slippage scales with lot size (number of contracts/shares)
    # 0.5 points per order * 2 orders = 1.0 point per share total
    slippage_rs = (CONFIG['slippage_points_per_order'] * 2) * lot_size
    
    # Brokerage is flat Rs. 20 per order (round trip is 40.0 Rs), DOES NOT scale with lot size
    brokerage_rs = CONFIG['brokerage_per_order'] * 2
    
    # Exchange Transaction Charge (NSE: 0.053% of premium value) scales with lot size
    etc_rs = 0.00053 * (entry_prem + exit_prem) * lot_size
    
    # STT (Securities Transaction Tax: 0.0625% on sell premium side) scales with lot size
    stt_rs = 0.000625 * exit_prem * lot_size
    
    # GST (18% on Brokerage + Exchange charges)
    gst_rs = 0.18 * (brokerage_rs + etc_rs)
    
    # Stamp Duty + SEBI charges (approx 0.003% of buy value)
    sebi_stamp_rs = 0.00003 * entry_prem * lot_size
    
    total_cost = slippage_rs + brokerage_rs + etc_rs + stt_rs + gst_rs + sebi_stamp_rs
    return total_cost

# ==========================================
# 6. Main Backtest Loop
# ==========================================
def run_backtest():
    print("Loading data files...")
    if not os.path.exists(CONFIG['spot_path']):
        raise FileNotFoundError(f"Spot CSV file not found at: {CONFIG['spot_path']}")
    if not os.path.exists(CONFIG['vix_path']):
        raise FileNotFoundError(f"VIX CSV file not found at: {CONFIG['vix_path']}")
        
    # Load Spot Data
    df = pd.read_csv(CONFIG['spot_path'])
    df['datetime'] = pd.to_datetime(df['date'] + ' ' + df['time'])
    df['date_obj'] = pd.to_datetime(df['date']).dt.date
    df = df.sort_values('datetime').reset_index(drop=True)
    
    # Load VIX Data
    vix_df = pd.read_csv(CONFIG['vix_path'])
    vix_df['date_obj'] = pd.to_datetime(vix_df['date']).dt.date
    vix_df.rename(columns={'close': 'vix'}, inplace=True)
    
    # Merge VIX data with spot data
    df = pd.merge(df, vix_df[['date_obj', 'vix']], on='date_obj', how='left')
    df['vix'] = df['vix'].ffill().bfill().fillna(15.0)
    
    # 1. Technical Indicator Calculations
    high = df['high']
    low = df['low']
    close = df['close']
    open_val = df['open']
    
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(prev_close - low)))
    df['tr'] = tr
    
    # Supertrend ATR (Period 10, RMA ATR)
    df['atr_st'] = calculate_rma(df['tr'], CONFIG['atr_period'])
    
    # Supertrend Bands
    multiplier = CONFIG['supertrend_multiplier']
    src = high
    
    up = src - multiplier * df['atr_st']
    dn = src + multiplier * df['atr_st']
    
    up_final = np.zeros(len(df))
    dn_final = np.zeros(len(df))
    trend = np.ones(len(df), dtype=int)
    
    up_final[0] = up.iloc[0] if not pd.isna(up.iloc[0]) else 0.0
    dn_final[0] = dn.iloc[0] if not pd.isna(dn.iloc[0]) else 0.0
    trend[0] = 1
    
    close_vals = close.values
    up_vals = up.values
    dn_vals = dn.values
    
    for i in range(1, len(df)):
        if close_vals[i-1] > up_final[i-1]:
            up_final[i] = max(up_vals[i], up_final[i-1])
        else:
            up_final[i] = up_vals[i]
            
        if close_vals[i-1] < dn_final[i-1]:
            dn_final[i] = min(dn_vals[i], dn_final[i-1])
        else:
            dn_final[i] = dn_vals[i]
            
        if trend[i-1] == -1 and close_vals[i] > dn_final[i-1]:
            trend[i] = 1
        elif trend[i-1] == 1 and close_vals[i] < up_final[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]
            
    df['up_final'] = up_final
    df['dn_final'] = dn_final
    df['trend'] = trend
    
    # Filter ATR (Period 20)
    df['filter_atr'] = calculate_rma(df['tr'], CONFIG['filter_atr_period'])
    
    # Candle filters
    candle_range = high - low
    candle_body = np.abs(close - open_val)
    df['body_ratio'] = np.where(candle_range > 0, candle_body / candle_range, 0.0)
    df['close_pos'] = np.where(candle_range > 0, (close - low) / candle_range, 0.5)
    
    # Session checks
    df['time_str'] = df['time'].str.replace(':', '').str[:4]
    df['in_entry_session'] = (df['time_str'] >= CONFIG['session_start_str']) & (df['time_str'] <= CONFIG['session_end_str'])
    df['is_squareoff_time'] = df['time_str'] >= CONFIG['squareoff_time_str']
    
    # Entry conditions
    df['atr_ok'] = df['filter_atr'] >= CONFIG['min_atr_value']
    df['candle_ok'] = (df['body_ratio'] >= CONFIG['candle_min_body_ratio']) & (df['close_pos'] >= CONFIG['candle_min_close_pos'])
    
    df['trend_prev'] = df['trend'].shift(1)
    df['buy_signal'] = (df['trend'] == 1) & (df['trend_prev'] == -1) & df['in_entry_session'] & df['atr_ok'] & df['candle_ok']
    df['reversal_exit'] = (df['trend'] == -1) & (df['trend_prev'] == 1)
    
    # Run backtest loop
    trades = []
    position = None
    capital = CONFIG['initial_capital']
    r = CONFIG['risk_free_rate']
    spot_sl_pts = CONFIG['spot_sl_points']
    spot_tp_pts = CONFIG['spot_tp_points']
    expiry_weekday = CONFIG['expiry_weekday']
    
    print(f"Simulating options trading from 2022 onwards with Initial Capital of Rs. {capital}...")
    for i in range(1, len(df)):
        # Skip if indicators are not fully calculated
        if pd.isna(df['atr_st'].iloc[i]) or pd.isna(df['filter_atr'].iloc[i]):
            continue
            
        row = df.iloc[i]
        current_dt = row['datetime']
        current_date = row['date_obj']
        current_time_obj = current_dt.time()
        
        # Check active position
        if position is not None:
            expiry_datetime = datetime.combine(position['expiry_date'], time(15, 30))
            
            # Time to expiry in years for current bar
            t_bar = (expiry_datetime - current_dt).total_seconds() / (365.0 * 24.0 * 3600.0)
            t_bar = max(1e-6, t_bar)
            sigma_bar = row['vix'] / 100.0
            
            # Map spot high, low, close to option contract premiums
            p_low = black_scholes_call(row['low'], position['strike'], t_bar, r, sigma_bar)
            p_high = black_scholes_call(row['high'], position['strike'], t_bar, r, sigma_bar)
            p_close = black_scholes_call(row['close'], position['strike'], t_bar, r, sigma_bar)
            
            sl_hit = p_low <= position['premium_sl']
            tp_hit = p_high >= position['premium_tp']
            
            # Helper to close trade and update capital (including brokerage/taxes)
            def close_trade(exit_prem, reason, exit_sp, ext_d, ext_t):
                nonlocal capital, position
                pnl_pts = exit_prem - position['entry_premium']
                gross_pnl_rs = pnl_pts * position['lot_size']
                
                # Calculate stat charges + brokerage + slippage
                costs_rs = calculate_costs(position['entry_premium'], exit_prem, position['lot_size'])
                net_pnl_rs = gross_pnl_rs - costs_rs
                
                capital_before = capital
                capital = capital + net_pnl_rs
                
                trades.append({
                    'entry_date': position['entry_date'],
                    'entry_time': position['entry_time'].strftime('%Y-%m-%d %H:%M'),
                    'entry_spot': position['entry_spot'],
                    'entry_premium': position['entry_premium'],
                    'strike': position['strike'],
                    'expiry': position['expiry_date'].strftime('%Y-%m-%d'),
                    'exit_date': ext_d,
                    'exit_time': ext_t,
                    'exit_spot': exit_sp,
                    'exit_premium': exit_prem,
                    'pnl': pnl_pts,
                    'lot_size': position['lot_size'],
                    'gross_pnl_rs': gross_pnl_rs,
                    'costs_rs': costs_rs,
                    'pnl_rs': net_pnl_rs, # Net P&L (after costs)
                    'capital_before': capital_before,
                    'capital_after': capital,
                    'reason': reason,
                    'delta': position['delta'],
                    'vix': position['vix']
                })
                position = None

            if sl_hit and tp_hit:
                # Conservatively assume SL hit first
                close_trade(position['premium_sl'], "SL (both hit)", row['low'], row['date'], row['time'])
            elif sl_hit:
                close_trade(position['premium_sl'], "SL", row['low'], row['date'], row['time'])
            elif tp_hit:
                close_trade(position['premium_tp'], "TP", row['high'], row['date'], row['time'])
            elif row['reversal_exit']:
                close_trade(p_close, "Reversal", row['close'], row['date'], row['time'])
            elif row['is_squareoff_time']:
                close_trade(p_close, "Intraday Squareoff", row['close'], row['date'], row['time'])
                
        # Check for new entry if no position is active
        if position is None:
            prev_row = df.iloc[i-1]
            if prev_row['buy_signal']:
                # Determine lot size
                lot_size = get_lot_size(current_date)
                
                # Enter at the open of current bar
                entry_spot = row['open']
                vix_entry = row['vix']
                sigma_entry = vix_entry / 100.0
                
                expiry_date = get_tuesday_expiry(current_date, current_time_obj, expiry_weekday)
                expiry_datetime = datetime.combine(expiry_date, time(15, 30))
                
                t_entry = (expiry_datetime - current_dt).total_seconds() / (365.0 * 24.0 * 3600.0)
                t_entry = max(1e-6, t_entry)
                
                # Find the strike closest to Rs 200 premium
                strike = find_strike_near_premium(entry_spot, t_entry, r, sigma_entry, CONFIG['target_premium'])
                
                # Recalculate exact entry premium for this strike
                entry_premium = black_scholes_call(entry_spot, strike, t_entry, r, sigma_entry)
                
                # Cost to enter 1 lot
                entry_cost = entry_premium * lot_size
                
                # Risk check: Check if capital is enough to buy 1 lot
                if capital < entry_cost:
                    # Skip trade due to insufficient capital
                    print(f"Skipping trade on {current_dt} due to insufficient capital: Need Rs. {entry_cost:.2f}, Have Rs. {capital:.2f}")
                    continue
                
                # Pre-placed Stop Loss based on 50 spot points down
                premium_sl = black_scholes_call(entry_spot - spot_sl_pts, strike, t_entry, r, sigma_entry)
                premium_sl = max(1.0, premium_sl)  # Enforce minimum option value
                
                # Pre-placed Take Profit based on 200 spot points up
                premium_tp = black_scholes_call(entry_spot + spot_tp_pts, strike, t_entry, r, sigma_entry)
                
                delta = calculate_bs_delta(entry_spot, strike, t_entry, r, sigma_entry)
                
                position = {
                    'entry_date': row['date'],
                    'entry_time': current_dt,
                    'entry_spot': entry_spot,
                    'entry_premium': entry_premium,
                    'strike': strike,
                    'expiry_date': expiry_date,
                    'premium_sl': premium_sl,
                    'premium_tp': premium_tp,
                    'delta': delta,
                    'vix': vix_entry,
                    'lot_size': lot_size
                }
                
    trades_df = pd.DataFrame(trades)
    return trades_df

# ==========================================
# 7. Report Generation
# ==========================================
def generate_html_report(trades_df, net_pnl, win_rate, total_trades, winning_count, losing_count, profit_factor, avg_pnl, max_drawdown_rs, max_drawdown_pct, sharpe_ratio, sortino_ratio, calmar_ratio, final_capital, net_return_pct, max_win_streak, max_lose_streak, max_dd_days, filename):
    cum_pnl_list = trades_df['cum_pnl_rs'].tolist()
    dates_list = trades_df['entry_time'].tolist()
    
    # Exit reasons counts
    reasons = trades_df['reason'].value_counts().to_dict()
    
    # Monthly P&L Grid (Multi-Year Matrix in Rs)
    trades_df['entry_time_dt'] = pd.to_datetime(trades_df['entry_time'])
    trades_df['year'] = trades_df['entry_time_dt'].dt.year
    trades_df['month'] = trades_df['entry_time_dt'].dt.month
    
    years = sorted(trades_df['year'].unique())
    months = list(range(1, 13))
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    monthly_matrix = trades_df.groupby(['year', 'month'])['pnl_rs'].sum().unstack(fill_value=0.0)
    for m in months:
        if m not in monthly_matrix.columns:
            monthly_matrix[m] = 0.0
    monthly_matrix = monthly_matrix[months]
    
    matrix_rows_html = ""
    for y in years:
        row_pnl = monthly_matrix.loc[y]
        total_y = row_pnl.sum()
        
        cells_html = ""
        for m in months:
            val = row_pnl[m]
            if val > 0:
                cells_html += f'<td class="value-green" style="padding: 0.75rem; font-weight: 500;">+Rs {val:,.0f}</td>'
            elif val < 0:
                cells_html += f'<td class="value-red" style="padding: 0.75rem; font-weight: 500;">-Rs {abs(val):,.0f}</td>'
            else:
                cells_html += '<td style="padding: 0.75rem; color: var(--text-muted);">-</td>'
                
        total_class = "value-green" if total_y >= 0 else "value-red"
        matrix_rows_html += f"""
        <tr style="border-bottom: 1px solid rgba(255,255,255,0.02);">
            <td style="padding: 0.75rem; text-align: left; font-weight: 700; color: #60a5fa;">{y}</td>
            {cells_html}
            <td class="{total_class}" style="padding: 0.75rem; font-weight: 700; background: rgba(255,255,255,0.015);">
                {"+" if total_y >= 0 else "-"}Rs {abs(total_y):,.0f}
            </td>
        </tr>
        """
        
    reason_rows_html = "".join(f'<div class="reason-row"><span class="reason-label">{k}</span><span class="reason-value">{v}</span></div>' for k, v in reasons.items())
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TriexDev Options Backtest Report (2022-Latest)</title>
    <meta name="description" content="Visual performance report for TriexDev - SuperBuySellStrategy Options Backtest (2022-Latest).">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-color: #080b13;
            --card-bg: rgba(16, 22, 38, 0.7);
            --border-color: rgba(255, 255, 255, 0.06);
            --primary: #10b981;
            --primary-glow: rgba(16, 185, 129, 0.15);
            --secondary: #3b82f6;
            --secondary-glow: rgba(59, 130, 246, 0.15);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --red: #ef4444;
            --red-glow: rgba(239, 68, 68, 0.15);
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        
        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-main);
            line-height: 1.5;
            padding: 2.5rem 1.5rem;
            min-height: 100vh;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        header {{
            margin-bottom: 2.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
        }}
        
        .logo-title h1 {{
            font-size: 2.25rem;
            font-weight: 800;
            background: linear-gradient(135deg, #10b981 0%, #3b82f6 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.25rem;
        }}
        
        .logo-title p {{
            color: var(--text-muted);
            font-size: 0.95rem;
        }}
        
        .timestamp {{
            font-size: 0.85rem;
            color: var(--text-muted);
            background: rgba(255,255,255,0.03);
            padding: 0.5rem 1rem;
            border-radius: 20px;
            border: 1px solid var(--border-color);
        }}
        
        .grid-4 {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        
        .card {{
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.75rem;
            backdrop-filter: blur(16px);
            transition: transform 0.25s ease, border-color 0.25s ease, box-shadow 0.25s ease;
            position: relative;
        }}
        
        .card:hover {{
            transform: translateY(-5px);
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.4);
            border-color: rgba(255, 255, 255, 0.12);
        }}
        
        .card-title {{
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-bottom: 0.5rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-weight: 600;
        }}
        
        .card-value {{
            font-size: 2.25rem;
            font-weight: 800;
            letter-spacing: -0.02em;
        }}
        
        .value-green {{
            color: var(--primary);
            text-shadow: 0 0 16px var(--primary-glow);
        }}
        
        .value-red {{
            color: var(--red);
            text-shadow: 0 0 16px var(--red-glow);
        }}
        
        .grid-2-1 {{
            display: grid;
            grid-template-columns: 2.2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        
        @media (max-width: 960px) {{
            .grid-2-1 {{
                grid-template-columns: 1fr;
            }}
        }}
        
        .chart-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.25rem;
        }}
        
        .chart-header h2 {{
            font-size: 1.25rem;
            font-weight: 700;
        }}
        
        .chart-container {{
            height: 380px;
            position: relative;
            width: 100%;
        }}
        
        .reason-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.85rem;
            padding-bottom: 0.85rem;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }}
        
        .reason-row:last-child {{
            border-bottom: none;
            margin-bottom: 0;
            padding-bottom: 0;
        }}
        
        .reason-label {{
            font-size: 0.875rem;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }}
        
        .reason-label::before {{
            content: '';
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--secondary);
        }}
        
        .reason-row:nth-child(1) .reason-label::before {{ background-color: #ef4444; }}
        .reason-row:nth-child(2) .reason-label::before {{ background-color: #10b981; }}
        .reason-row:nth-child(3) .reason-label::before {{ background-color: #fbbf24; }}
        .reason-row:nth-child(4) .reason-label::before {{ background-color: #3b82f6; }}
        
        .reason-value {{
            font-weight: 700;
            font-size: 0.95rem;
        }}
        
        .ratio-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: 1.5rem;
            margin-top: 1.25rem;
        }}
        
        .ratio-item {{
            display: flex;
            flex-direction: column;
            border-left: 3px solid var(--secondary);
            padding-left: 1rem;
            background: rgba(255,255,255,0.01);
            padding-top: 0.5rem;
            padding-bottom: 0.5rem;
            border-radius: 0 8px 8px 0;
        }}
        
        .ratio-item.green {{
            border-left-color: var(--primary);
        }}
        
        .ratio-label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-bottom: 0.25rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }}
        
        .ratio-val {{
            font-size: 1.35rem;
            font-weight: 800;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-title">
                <h1>TriexDev - Strategy Dashboard</h1>
                <p>Performance analytics report for Simulated Nifty Option Premium (2022-Latest)</p>
            </div>
            <div class="timestamp">
                Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </div>
        </header>
        
        <!-- Key Metrics Row -->
        <div class="grid-4">
            <div class="card">
                <div class="card-title">Net Capital P&L (Rs)</div>
                <div class="card-value {"value-green" if net_pnl >= 0 else "value-red"}">
                    {"+" if net_pnl >= 0 else "-"}Rs {abs(final_capital - CONFIG['initial_capital']):,.2f}
                </div>
            </div>
            <div class="card">
                <div class="card-title">Net Return (%)</div>
                <div class="card-value {"value-green" if net_return_pct >= 0 else "value-red"}">
                    {"+" if net_return_pct >= 0 else ""}{net_return_pct:.2f}%
                </div>
            </div>
            <div class="card">
                <div class="card-title">Win Rate</div>
                <div class="card-value" style="color: #60a5fa; text-shadow: 0 0 16px rgba(96, 165, 250, 0.15);">
                    {win_rate:.2f}%
                </div>
            </div>
            <div class="card">
                <div class="card-title">Profit Factor</div>
                <div class="card-value" style="color: #fbbf24; text-shadow: 0 0 16px rgba(251, 191, 36, 0.15);">
                    {profit_factor:.2f}
                </div>
            </div>
        </div>
        
        <!-- Chart & Stats Section -->
        <div class="grid-2-1">
            <div class="card">
                <div class="chart-header">
                    <h2>Account Equity Curve (Rs, Net of Costs)</h2>
                </div>
                <div class="chart-container">
                    <canvas id="equityChart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <div class="chart-header">
                    <h2>Trade Exits Analysis</h2>
                </div>
                <div style="margin-top: 1rem;">
                    {reason_rows_html}
                </div>
            </div>
        </div>
        
        <!-- Advanced Metrics Card -->
        <div class="card" style="margin-bottom: 2rem;">
            <div class="chart-header" style="margin-bottom: 0.5rem;">
                <h2>Advanced Risk-Adjusted & Streak Ratios (Net of Costs)</h2>
            </div>
            <div class="ratio-grid">
                <div class="ratio-item green">
                    <span class="ratio-label">Sharpe Ratio</span>
                    <span class="ratio-val" style="color: var(--primary);">{sharpe_ratio:.2f}</span>
                </div>
                <div class="ratio-item green">
                    <span class="ratio-label">Sortino Ratio</span>
                    <span class="ratio-val" style="color: var(--primary);">{sortino_ratio:.2f}</span>
                </div>
                <div class="ratio-item">
                    <span class="ratio-label">Calmar Ratio</span>
                    <span class="ratio-val" style="color: #fbbf24;">{calmar_ratio:.2f}</span>
                </div>
                <div class="ratio-item" style="border-left-color: var(--red);">
                    <span class="ratio-label">Max Drawdown</span>
                    <span class="ratio-val" style="color: var(--red);">Rs {max_drawdown_rs:,.2f} ({max_drawdown_pct:.2f}%)</span>
                </div>
                <div class="ratio-item">
                    <span class="ratio-label">Max Drawdown Days</span>
                    <span class="ratio-val" style="color: var(--red);">{max_dd_days:.1f} Days</span>
                </div>
                <div class="ratio-item">
                    <span class="ratio-label">Max Winning Streak</span>
                    <span class="ratio-val" style="color: var(--primary);">{max_win_streak} Trades</span>
                </div>
                <div class="ratio-item">
                    <span class="ratio-label">Max Losing Streak</span>
                    <span class="ratio-val" style="color: var(--red);">{max_lose_streak} Trades</span>
                </div>
                <div class="ratio-item">
                    <span class="ratio-label">Average Net P&L</span>
                    <span class="ratio-val">Rs {avg_pnl:,.2f}</span>
                </div>
            </div>
        </div>
        
        <!-- Monthly Returns Grid Matrix -->
        <div class="card" style="margin-bottom: 2rem; overflow-x: auto;">
            <div class="chart-header">
                <h2>Monthly Net Profit/Loss (Rupees, Net of Costs)</h2>
            </div>
            <table style="width: 100%; border-collapse: collapse; margin-top: 1rem; text-align: center;">
                <thead>
                    <tr style="border-bottom: 1px solid var(--border-color); padding-bottom: 0.5rem;">
                        <th style="padding: 0.75rem; text-align: left; color: var(--text-muted);">Year</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Jan</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Feb</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Mar</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Apr</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">May</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Jun</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Jul</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Aug</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Sep</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Oct</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Nov</th>
                        <th style="padding: 0.75rem; color: var(--text-muted);">Dec</th>
                        <th style="padding: 0.75rem; color: var(--text-muted); font-weight: 700;">Total</th>
                    </tr>
                </thead>
                <tbody>
                    {matrix_rows_html}
                </tbody>
            </table>
        </div>
    </div>
    
    <script>
        const dates = {dates_list};
        const equity = {cum_pnl_list};
        
        const ctx = document.getElementById('equityChart').getContext('2d');
        
        const gradient = ctx.createLinearGradient(0, 0, 0, 350);
        gradient.addColorStop(0, 'rgba(16, 185, 129, 0.3)');
        gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');
        
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: dates.map((_, i) => `Trade ${{i + 1}}`),
                datasets: [{{
                    label: 'Account Equity (Rs)',
                    data: equity,
                    borderColor: '#10b981',
                    borderWidth: 2.5,
                    backgroundColor: gradient,
                    fill: true,
                    tension: 0.2,
                    pointRadius: 1,
                    pointHoverRadius: 5,
                    pointBackgroundColor: '#10b981',
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        display: false
                    }},
                    tooltip: {{
                        mode: 'index',
                        intersect: false,
                        backgroundColor: '#111827',
                        titleColor: '#9ca3af',
                        bodyColor: '#f3f4f6',
                        borderColor: 'rgba(255,255,255,0.08)',
                        borderWidth: 1,
                        callbacks: {{
                            title: function(context) {{
                                const idx = context[0].dataIndex;
                                return dates[idx];
                            }},
                            label: function(context) {{
                                return "Equity: Rs " + Number(context.parsed.y).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{
                            color: 'rgba(255, 255, 255, 0.03)'
                        }},
                        ticks: {{
                            color: '#6b7280',
                            font: {{
                                family: 'Plus Jakarta Sans',
                                size: 10
                            }}
                        }}
                    }},
                    y: {{
                        grid: {{
                            color: 'rgba(255, 255, 255, 0.03)'
                        }},
                        ticks: {{
                            color: '#6b7280',
                            font: {{
                                family: 'Plus Jakarta Sans',
                                size: 10
                            }},
                            callback: function(value) {{
                                return "Rs " + Number(value).toLocaleString();
                            }}
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    with open(filename, 'w') as f:
        f.write(html_content)
    print(f"Visual HTML report saved successfully to: {filename}")

def report_performance(trades_df):
    if len(trades_df) == 0:
        print("\n==========================================")
        print("         NO TRADES SIGNALS GENERATED       ")
        print("==========================================")
        return
        
    total_trades = len(trades_df)
    winning_trades = trades_df[trades_df['pnl_rs'] > 0]
    losing_trades = trades_df[trades_df['pnl_rs'] <= 0]
    
    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    total_profit_rs = winning_trades['pnl_rs'].sum()
    total_loss_rs = abs(losing_trades['pnl_rs'].sum())
    net_pnl_rs = trades_df['pnl_rs'].sum()
    
    profit_factor = total_profit_rs / total_loss_rs if total_loss_rs > 0 else float('inf')
    avg_pnl_rs = trades_df['pnl_rs'].mean()
    
    # Calculate Drawdown
    initial_cap = CONFIG['initial_capital']
    trades_df['cum_pnl_rs'] = initial_cap + trades_df['pnl_rs'].cumsum()
    trades_df['peak_rs'] = trades_df['cum_pnl_rs'].cummax()
    trades_df['drawdown_rs'] = trades_df['peak_rs'] - trades_df['cum_pnl_rs']
    trades_df['drawdown_pct'] = (trades_df['drawdown_rs'] / trades_df['peak_rs']) * 100
    
    max_drawdown_rs = trades_df['drawdown_rs'].max()
    max_drawdown_pct = trades_df['drawdown_pct'].max()
    
    final_capital = trades_df['capital_after'].iloc[-1]
    net_return_pct = ((final_capital - initial_cap) / initial_cap) * 100
    
    # Streaks calculation
    pnl_signs = trades_df['pnl_rs'].apply(lambda x: 1 if x > 0 else -1).tolist()
    max_win_streak = 0
    max_lose_streak = 0
    current_win_streak = 0
    current_lose_streak = 0
    for sign in pnl_signs:
        if sign == 1:
            current_win_streak += 1
            current_lose_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_lose_streak += 1
            current_win_streak = 0
            max_lose_streak = max(max_lose_streak, current_lose_streak)
            
    # Max days under drawdown (recovery time)
    trades_df['entry_time_dt'] = pd.to_datetime(trades_df['entry_time'])
    trades_df = trades_df.sort_values('entry_time_dt').reset_index(drop=True)
    
    peak_times = []
    current_peak_val = -float('inf')
    current_peak_time = None
    
    for i, row in trades_df.iterrows():
        eq = trades_df['cum_pnl_rs'].iloc[i]
        t = row['entry_time_dt']
        if eq >= current_peak_val:
            current_peak_val = eq
            current_peak_time = t
        peak_times.append(current_peak_time)
        
        # Keep track of the current active peak to verify drawdown recovery
        
    drawdown_durations = [trades_df['entry_time_dt'].iloc[j] - peak_times[j] for j in range(len(trades_df))]
    max_dd_duration = max(drawdown_durations)
    max_dd_days = max_dd_duration.total_seconds() / (24 * 3600)
    
    # Sharpe & Sortino based on daily percentage returns
    daily_pnl = trades_df.groupby(trades_df['entry_time_dt'].dt.date)['pnl_rs'].sum()
    unique_dates = sorted(daily_pnl.index)
    daily_returns = []
    
    current_cap = initial_cap
    for d in unique_dates:
        pnl_d = daily_pnl.loc[d]
        ret = pnl_d / current_cap
        daily_returns.append(ret)
        current_cap += pnl_d
        
    daily_returns = pd.Series(daily_returns)
    daily_mean = daily_returns.mean()
    daily_std = daily_returns.std()
    
    sharpe_ratio = (daily_mean / daily_std) * math.sqrt(252) if daily_std > 0 else 0.0
    
    downside_daily = daily_returns[daily_returns < 0]
    downside_std = downside_daily.std() if len(downside_daily) > 1 else 1e-6
    sortino_ratio = (daily_mean / downside_std) * math.sqrt(252) if downside_std > 1e-5 else 0.0
    
    calmar_ratio = (net_return_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0
    
    # Total Costs Summary
    total_costs_rs = trades_df['costs_rs'].sum()
    
    print("\n==========================================")
    print("  SIMULATED OPTIONS BACKTEST PERFORMANCE RESULTS (2022-Latest)")
    print("==========================================")
    print(f"Initial Capital:        Rs. {initial_cap:,.2f}")
    print(f"Final Capital:          Rs. {final_capital:,.2f}")
    print(f"Net Return (%):         {net_return_pct:.2f}%")
    print(f"Total Trades:           {total_trades}")
    print(f"Winning Trades:         {len(winning_trades)}")
    print(f"Losing Trades:          {len(losing_trades)}")
    print(f"Win Rate:               {win_rate:.2f}%")
    print(f"Gross Profit:           Rs. {total_profit_rs + total_costs_rs:,.2f}")
    print(f"Total Costs (Charges):  Rs. {total_costs_rs:,.2f}")
    print(f"Net Capital P&L:        Rs. {net_pnl_rs:,.2f}")
    print(f"Profit Factor:          {profit_factor:.2f}")
    print(f"Average Net Trade P&L:  Rs. {avg_pnl_rs:,.2f}")
    print(f"Max Drawdown (Rs):      Rs. {max_drawdown_rs:,.2f} ({max_drawdown_pct:.2f}%)")
    print(f"Max Drawdown Duration:  {max_dd_days:.1f} calendar days")
    print(f"Max Winning Streak:     {max_win_streak} consecutive trades")
    print(f"Max Losing Streak:      {max_lose_streak} consecutive trades")
    print(f"Sharpe Ratio (Annual):  {sharpe_ratio:.2f}")
    print(f"Sortino Ratio (Annual): {sortino_ratio:.2f}")
    print(f"Calmar Ratio:           {calmar_ratio:.2f}")
    print("==========================================")
    
    # Save Trade Log
    trades_df.to_csv(CONFIG['trade_log_filename'], index=False)
    print(f"Trade log saved successfully to: {CONFIG['trade_log_filename']}")
    
    # Generate Matplotlib chart
    plt.figure(figsize=(12, 6))
    plt.style.use('dark_background')
    plt.plot(trades_df['cum_pnl_rs'], color='#00ffcc', linewidth=2, label='Account Equity (Rs, Net)')
    plt.fill_between(range(len(trades_df)), trades_df['cum_pnl_rs'], initial_cap, color='#00ffcc', alpha=0.1)
    plt.title('TriexDev - SuperBuySellStrategy Options Backtest (2022-Latest)', fontsize=14, fontweight='bold', color='#ffffff')
    plt.xlabel('Trade Count', fontsize=12, color='#cccccc')
    plt.ylabel('Account Value (Rupees)', fontsize=12, color='#cccccc')
    plt.grid(True, linestyle=':', alpha=0.4, color='#888888')
    plt.legend(loc='upper left', framealpha=0.8)
    plt.tight_layout()
    plt.savefig(CONFIG['equity_curve_filename'], dpi=300)
    print(f"Equity curve chart saved to: {CONFIG['equity_curve_filename']}")
    plt.close()
    
    # Generate interactive HTML dashboard
    generate_html_report(
        trades_df=trades_df,
        net_pnl=net_pnl_rs,
        win_rate=win_rate,
        total_trades=total_trades,
        winning_count=len(winning_trades),
        losing_count=len(losing_trades),
        profit_factor=profit_factor,
        avg_pnl=avg_pnl_rs,
        max_drawdown_rs=max_drawdown_rs,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        sortino_ratio=sortino_ratio,
        calmar_ratio=calmar_ratio,
        final_capital=final_capital,
        net_return_pct=net_return_pct,
        max_win_streak=max_win_streak,
        max_lose_streak=max_lose_streak,
        max_dd_days=max_dd_days,
        filename=CONFIG['report_filename']
    )

# ==========================================
# 8. Main Execution Entrypoint
# ==========================================
if __name__ == '__main__':
    try:
        trades_df = run_backtest()
        report_performance(trades_df)
    except Exception as e:
        import traceback
        print(f"\n[ERROR] Backtest run failed: {e}")
        traceback.print_exc()
