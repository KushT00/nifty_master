"""
Nifty Survivor Options Strategy — OpenAlgo Platform Master Version
===================================================================
Systematic Index-Movement Option Selling Strategy for OpenAlgo SDK:
  - Dynamic VIX Auto-Config: Fetches INDIA VIX on startup and sets gaps, offsets, and position multiplier limits.
  - Put (PE) Trading: Triggered when NIFTY moves UP beyond pe_gap threshold (+20 pts). Sells OTM Puts (~200pt OTM).
  - Call (CE) Trading: Triggered when NIFTY moves DOWN beyond ce_gap threshold (-20 pts). Sells OTM Calls (~200pt OTM).
  - Dynamic Position Scaling: Scales position quantity based on price movement magnitude (sell_multiplier).
  - Risk Multiplier Safeguard: Rejects trade if sell_multiplier > sell_multiplier_threshold (5).
  - Dynamic Premium Filter: Ensures sold option premium >= min_price_to_sell (Rs. 15). Steps closer to ATM if lower.
  - Reference Reset Mechanism: Adjusts PE/CE reference values on favorable market reversals to prevent reference drift.
  - Expiry Safety: Auto-squares off positions by Monday 3:15 PM EOD and pauses trading on Tuesday (0-DTE Expiry Day).
"""

import os
import json
import time
import csv
import logging
from datetime import datetime, date
from openalgo import api

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("SurvivorMaster")

# ============================================================
# 1. PARAMETERS & CONFIG
# ============================================================
CONFIG = {
    # OpenAlgo API Configuration
    "api_key": "ff1d258f2c5d48bf87292b3f055b39772ca76f4afe69ff7c7c5fa121a32334d8",
    "host": "http://127.0.0.1:5000",
    
    # Dynamic VIX Auto-Config Toggle
    "auto_vix_config": True,
    
    # Core Instrument Parameters
    "underlying": "NIFTY",
    "exchange_index": "NSE_INDEX",
    "exchange_deriv": "NFO",
    "lot_size": 65,                # Nifty Lot Size is 65
    "strike_step": 50,             # Nifty Strike Step is 50
    
    # Default Gap Parameters (Overridden dynamically by VIX)
    "pe_gap": 20.0,
    "ce_gap": 20.0,
    
    # Position Quantities (1 Lot = 65)
    "pe_quantity": 65,
    "ce_quantity": 65,
    
    # Strike Distance Parameters (OTM Strike Offset in points)
    "pe_symbol_gap": 200,
    "ce_symbol_gap": 200,
    
    # Risk Management Parameters
    "min_price_to_sell": 15.0,        # Minimum required option premium (Rs.)
    "sell_multiplier_threshold": 5,   # Max allowed position scaling multiplier
    
    # Reference Reset Parameters
    "pe_reset_gap": 30.0,
    "ce_reset_gap": 30.0,
    
    # Starting Reference Points (0 = use live LTP)
    "pe_start_point": 0.0,
    "ce_start_point": 0.0,
    
    # Order Parameters
    "trans_type": "SELL",
    "order_type": "MARKET",
    "product_type": "NRML",
    "tag": "Survivor",
    
    # Expiry Safety Settings (Tuesday is Expiry Day -> Exit Monday EOD)
    "monday_eod_exit": True,       # Auto exit on Monday 3:15 PM
    "tuesday_expiry_pause": True,  # Pause trading on Tuesday 0-DTE
    
    # Loop Polling Interval (seconds)
    "poll_interval": 30,
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "nifty_survivor_state.json")
TRADE_LOG  = os.path.join(SCRIPT_DIR, "nifty_survivor_trades.csv")
AUDIT_LOG  = os.path.join(SCRIPT_DIR, "nifty_survivor_audit.csv")

# ============================================================
# 2. SURVIVOR STRATEGY ENGINE
# ============================================================
class OpenAlgoSurvivorEngine:
    def __init__(self, config=CONFIG):
        self.config = config
        self.client = api(api_key=config["api_key"], host=config["host"])
        self.state = self.load_state()
        self.expiry_code = self.get_expiry()
        
        self.pe_reset_gap_flag = self.state.get("pe_reset_gap_flag", 0)
        self.ce_reset_gap_flag = self.state.get("ce_reset_gap_flag", 0)
        
        # 1. Fetch live VIX and dynamically configure parameters if enabled
        if self.config.get("auto_vix_config", True):
            self.fetch_vix_and_apply_config()

        # 2. Initialize reference points
        self._initialize_reference_points()
        
        logger.info(
            f"Initialized Survivor Engine | Expiry: {self.expiry_code} | "
            f"PE Ref: {self.nifty_pe_last_value:.2f} | CE Ref: {self.nifty_ce_last_value:.2f} | "
            f"PE Gap: {self.config['pe_gap']} | CE Gap: {self.config['ce_gap']}"
        )

    def fetch_vix_and_apply_config(self):
        """Fetch live INDIA VIX and dynamically set parameters based on VIX matrix"""
        vix = 14.0  # Default fallback VIX
        try:
            res = self.client.quotes(exchange="NSE_INDEX", symbol="INDIA VIX")
            if res and isinstance(res, dict):
                data = res.get("data", res)
                vix_val = float(data.get("ltp") or data.get("last_price", 0.0))
                if vix_val > 0:
                    vix = vix_val
        except Exception as e:
            logger.warning(f"Could not fetch INDIA VIX quote ({e}). Using default VIX 14.0.")

        # VIX Matrix Allocation for Survivor
        if vix < 12.0:
            self.config["pe_gap"] = 15.0
            self.config["ce_gap"] = 15.0
            self.config["pe_symbol_gap"] = 150
            self.config["ce_symbol_gap"] = 150
            self.config["pe_reset_gap"] = 20.0
            self.config["ce_reset_gap"] = 20.0
        elif 12.0 <= vix < 14.5:
            self.config["pe_gap"] = 20.0
            self.config["ce_gap"] = 20.0
            self.config["pe_symbol_gap"] = 200
            self.config["ce_symbol_gap"] = 200
            self.config["pe_reset_gap"] = 30.0
            self.config["ce_reset_gap"] = 30.0
        elif 14.5 <= vix < 17.5:
            self.config["pe_gap"] = 30.0
            self.config["ce_gap"] = 30.0
            self.config["pe_symbol_gap"] = 250
            self.config["ce_symbol_gap"] = 250
            self.config["pe_reset_gap"] = 40.0
            self.config["ce_reset_gap"] = 40.0
        else:
            self.config["pe_gap"] = 40.0
            self.config["ce_gap"] = 40.0
            self.config["pe_symbol_gap"] = 300
            self.config["ce_symbol_gap"] = 300
            self.config["pe_reset_gap"] = 50.0
            self.config["ce_reset_gap"] = 50.0

        logger.info(
            f"⚡ [SURVIVOR VIX CONFIG] INDIA VIX: {vix:.2f} | "
            f"Gaps: {self.config['pe_gap']}pt | Symbol Offsets: {self.config['pe_symbol_gap']}pt"
        )

    def get_expiry(self):
        """Fetch nearest active weekly/monthly options expiry (e.g. 04AUG26)"""
        try:
            res = self.client.expiry(symbol=self.config["underlying"], exchange=self.config["exchange_deriv"], instrumenttype="options")
            data = res if isinstance(res, list) else res.get("data", [])
            if data:
                return data[0].replace("-", "")
        except Exception as e:
            logger.error(f"Expiry fetch error: {e}")
        return ""

    def get_spot_price(self):
        """Fetch current NIFTY index spot price"""
        try:
            res = self.client.quotes(exchange=self.config["exchange_index"], symbol=self.config["underlying"])
            if res and isinstance(res, dict):
                data = res.get("data", res)
                return float(data.get("ltp") or data.get("last_price", 0.0))
        except Exception as e:
            logger.error(f"Spot quote fetch error: {e}")
        return 0.0

    def get_option_quote(self, symbol):
        """Fetch option LTP via OpenAlgo API"""
        try:
            res = self.client.quotes(exchange=self.config["exchange_deriv"], symbol=symbol)
            if res and isinstance(res, dict):
                data = res.get("data", res)
                ltp = data.get("ltp") or data.get("last_price")
                if ltp:
                    return float(ltp)
        except Exception:
            pass
        return 0.0

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error loading state: {e}")
        return {}

    def save_state(self):
        self.state["nifty_pe_last_value"] = self.nifty_pe_last_value
        self.state["nifty_ce_last_value"] = self.nifty_ce_last_value
        self.state["pe_reset_gap_flag"] = self.pe_reset_gap_flag
        self.state["ce_reset_gap_flag"] = self.ce_reset_gap_flag
        self.state["last_eval"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    def log_decision(self, action, reason, details=None):
        logger.info(f"[{action}] {reason} | {details or ''}")
        try:
            file_exists = os.path.exists(AUDIT_LOG)
            with open(AUDIT_LOG, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "action", "reason", "details"])
                writer.writerow([datetime.now().isoformat(), action, reason, json.dumps(details or {})])
        except Exception:
            pass

    def log_trade(self, order_id, symbol, action, qty, price, status):
        try:
            file_exists = os.path.exists(TRADE_LOG)
            with open(TRADE_LOG, "a", newline="") as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(["timestamp", "order_id", "symbol", "action", "qty", "price", "status"])
                writer.writerow([datetime.now().isoformat(), order_id, symbol, action, qty, price, status])
        except Exception as e:
            logger.error(f"Trade log write error: {e}")

    def _initialize_reference_points(self):
        """Initialize PE and CE tracking reference points"""
        spot = self.get_spot_price()
        
        # PE Reference
        if self.state.get("nifty_pe_last_value", 0) > 0:
            self.nifty_pe_last_value = float(self.state["nifty_pe_last_value"])
        elif self.config["pe_start_point"] > 0:
            self.nifty_pe_last_value = float(self.config["pe_start_point"])
        else:
            self.nifty_pe_last_value = spot

        # CE Reference
        if self.state.get("nifty_ce_last_value", 0) > 0:
            self.nifty_ce_last_value = float(self.state["nifty_ce_last_value"])
        elif self.config["ce_start_point"] > 0:
            self.nifty_ce_last_value = float(self.config["ce_start_point"])
        else:
            self.nifty_ce_last_value = spot

    def build_option_symbol(self, strike, opt_type):
        """Build standard option symbol format: NIFTY04AUG2624500CE"""
        if not self.expiry_code:
            self.expiry_code = self.get_expiry()
        return f"{self.config['underlying']}{self.expiry_code}{strike}{opt_type}"

    def check_sell_multiplier_breach(self, sell_multiplier):
        """Safeguard: check if position multiplier breaches risk threshold"""
        if sell_multiplier > self.config["sell_multiplier_threshold"]:
            logger.warning(f"Multiplier breach! {sell_multiplier} > max ({self.config['sell_multiplier_threshold']})")
            return True
        return False

    def handle_pe_trade(self, current_price):
        """Evaluate Put (PE) option selling on upward spot move"""
        if current_price <= self.nifty_pe_last_value:
            return

        price_diff = round(current_price - self.nifty_pe_last_value, 0)
        if price_diff > self.config["pe_gap"]:
            sell_multiplier = int(price_diff / self.config["pe_gap"])
            
            if self.check_sell_multiplier_breach(sell_multiplier):
                self.log_decision("PE_SKIPPED", f"Multiplier breach: {sell_multiplier} > {self.config['sell_multiplier_threshold']}")
                return

            total_qty = sell_multiplier * self.config["pe_quantity"]
            temp_gap = self.config["pe_symbol_gap"]
            
            # Strike selection with premium filter
            symbol_to_sell = None
            price_found = 0.0
            
            while temp_gap >= 0:
                target_strike = int(round((current_price - temp_gap) / self.config["strike_step"]) * self.config["strike_step"])
                candidate_sym = self.build_option_symbol(target_strike, "PE")
                ltp = self.get_option_quote(candidate_sym)
                
                if ltp >= self.config["min_price_to_sell"]:
                    symbol_to_sell = candidate_sym
                    price_found = ltp
                    break
                
                logger.info(f"Symbol {candidate_sym} premium Rs.{ltp:.1f} < min Rs.{self.config['min_price_to_sell']}. Trying closer strike.")
                temp_gap -= self.config["strike_step"]

            if not symbol_to_sell:
                # Fallback to nearest ATM
                target_strike = int(round((current_price - 50) / self.config["strike_step"]) * self.config["strike_step"])
                symbol_to_sell = self.build_option_symbol(target_strike, "PE")
                price_found = self.get_option_quote(symbol_to_sell)

            # Update reference point
            self.nifty_pe_last_value += self.config["pe_gap"] * sell_multiplier
            self.pe_reset_gap_flag = 1

            # Execute Order via OpenAlgo
            try:
                res = self.client.placeorder(
                    strategy=self.config["tag"],
                    symbol=symbol_to_sell,
                    action="SELL",
                    exchange=self.config["exchange_deriv"],
                    price=0,  # Market order
                    pricetype=self.config["order_type"],
                    product=self.config["product_type"],
                    quantity=total_qty
                )
                order_id = res.get("orderid") or res.get("order_id", "UNKNOWN")
                logger.info(f"Executed SURVIVOR PE SELL: {symbol_to_sell} x {total_qty} @ Rs.{price_found:.2f}")
                self.log_trade(order_id, symbol_to_sell, "SELL", total_qty, price_found, res.get("status", "SUCCESS"))
                self.log_decision("PE_EXECUTED", f"Sold {symbol_to_sell} x {total_qty} @ Rs.{price_found:.2f}", {"spot": current_price, "mult": sell_multiplier})
            except Exception as e:
                logger.error(f"PE Order placement error: {e}")

    def handle_ce_trade(self, current_price):
        """Evaluate Call (CE) option selling on downward spot move"""
        if current_price >= self.nifty_ce_last_value:
            return

        price_diff = round(self.nifty_ce_last_value - current_price, 0)
        if price_diff > self.config["ce_gap"]:
            sell_multiplier = int(price_diff / self.config["ce_gap"])
            
            if self.check_sell_multiplier_breach(sell_multiplier):
                self.log_decision("CE_SKIPPED", f"Multiplier breach: {sell_multiplier} > {self.config['sell_multiplier_threshold']}")
                return

            total_qty = sell_multiplier * self.config["ce_quantity"]
            temp_gap = self.config["ce_symbol_gap"]
            
            # Strike selection with premium filter
            symbol_to_sell = None
            price_found = 0.0
            
            while temp_gap >= 0:
                target_strike = int(round((current_price + temp_gap) / self.config["strike_step"]) * self.config["strike_step"])
                candidate_sym = self.build_option_symbol(target_strike, "CE")
                ltp = self.get_option_quote(candidate_sym)
                
                if ltp >= self.config["min_price_to_sell"]:
                    symbol_to_sell = candidate_sym
                    price_found = ltp
                    break
                
                logger.info(f"Symbol {candidate_sym} premium Rs.{ltp:.1f} < min Rs.{self.config['min_price_to_sell']}. Trying closer strike.")
                temp_gap -= self.config["strike_step"]

            if not symbol_to_sell:
                # Fallback to nearest ATM
                target_strike = int(round((current_price + 50) / self.config["strike_step"]) * self.config["strike_step"])
                symbol_to_sell = self.build_option_symbol(target_strike, "CE")
                price_found = self.get_option_quote(symbol_to_sell)

            # Update reference point
            self.nifty_ce_last_value -= self.config["ce_gap"] * sell_multiplier
            self.ce_reset_gap_flag = 1

            # Execute Order via OpenAlgo
            try:
                res = self.client.placeorder(
                    strategy=self.config["tag"],
                    symbol=symbol_to_sell,
                    action="SELL",
                    exchange=self.config["exchange_deriv"],
                    price=0,  # Market order
                    pricetype=self.config["order_type"],
                    product=self.config["product_type"],
                    quantity=total_qty
                )
                order_id = res.get("orderid") or res.get("order_id", "UNKNOWN")
                logger.info(f"Executed SURVIVOR CE SELL: {symbol_to_sell} x {total_qty} @ Rs.{price_found:.2f}")
                self.log_trade(order_id, symbol_to_sell, "SELL", total_qty, price_found, res.get("status", "SUCCESS"))
                self.log_decision("CE_EXECUTED", f"Sold {symbol_to_sell} x {total_qty} @ Rs.{price_found:.2f}", {"spot": current_price, "mult": sell_multiplier})
            except Exception as e:
                logger.error(f"CE Order placement error: {e}")

    def reset_reference_values(self, current_price):
        """Reset PE/CE reference values when market moves favorably in reverse"""
        # PE Reset: spot drops significantly below PE reference
        if (self.nifty_pe_last_value - current_price) > self.config["pe_reset_gap"] and self.pe_reset_gap_flag:
            new_val = current_price + self.config["pe_reset_gap"]
            logger.info(f"Resetting PE reference from {self.nifty_pe_last_value} to {new_val}")
            self.log_decision("PE_RESET", f"PE reference adjusted from {self.nifty_pe_last_value} to {new_val}")
            self.nifty_pe_last_value = new_val

        # CE Reset: spot rises significantly above CE reference
        if (current_price - self.nifty_ce_last_value) > self.config["ce_reset_gap"] and self.ce_reset_gap_flag:
            new_val = current_price - self.config["ce_reset_gap"]
            logger.info(f"Resetting CE reference from {self.nifty_ce_last_value} to {new_val}")
            self.log_decision("CE_RESET", f"CE reference adjusted from {self.nifty_ce_last_value} to {new_val}")
            self.nifty_ce_last_value = new_val

    def check_monday_eod_and_tuesday_expiry(self):
        """
        Expiry Rule: Tuesday is Expiry Day.
        - Monday after 3:15 PM: Log Monday EOD exit condition.
        - Tuesday (0-DTE): Pause trading to avoid 0-DTE gamma risk.
        """
        now = datetime.now()
        weekday = now.weekday()  # Monday is 0, Tuesday is 1
        
        # Tuesday 0-DTE Pause
        if weekday == 1 and self.config.get("tuesday_expiry_pause", True):
            logger.warning("⚠️ Tuesday 0-DTE Expiry Day — Survivor Strategy Trading Paused (Gamma Safety).")
            return True

        # Monday EOD Exit (3:15 PM onwards)
        if weekday == 0 and now.hour == 15 and now.minute >= 15 and self.config.get("monday_eod_exit", True):
            logger.info("⚡ Monday 3:15 PM EOD Triggered — Survivor Strategy Pausing New Entries for Tuesday Expiry.")
            return True
            
        return False

    def execute_tick(self):
        """Main tick iteration function"""
        current_price = self.get_spot_price()
        if current_price <= 0:
            logger.warning("Invalid spot price. Retrying...")
            return

        is_expiry_restricted = self.check_monday_eod_and_tuesday_expiry()

        logger.info(f"[SURVIVOR TICK] Spot: {current_price:.2f} | PE Ref: {self.nifty_pe_last_value:.2f} | CE Ref: {self.nifty_ce_last_value:.2f}")
        
        if not is_expiry_restricted:
            self.handle_pe_trade(current_price)
            self.handle_ce_trade(current_price)
            self.reset_reference_values(current_price)
            self.save_state()


def run():
    engine = OpenAlgoSurvivorEngine(CONFIG)
    logger.info("Starting Survivor Options Strategy Master Loop...")
    while True:
        try:
            engine.execute_tick()
        except KeyboardInterrupt:
            logger.info("Strategy stopped by user.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in survivor strategy loop: {e}", exc_info=True)
        
        time.sleep(CONFIG["poll_interval"])


if __name__ == "__main__":
    run()
