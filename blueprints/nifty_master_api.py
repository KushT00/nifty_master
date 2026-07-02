"""
Blueprint to serve the Nifty Weekly Master strategy state to the frontend.
Reads from the nifty_master_state.json file written by the strategy engine.
Also serves trade journal CSV data with aggregate performance stats.
"""
import csv
import json
import os

from flask import Blueprint, jsonify, request, session
from database.auth_db import get_auth_token
from utils.session import check_session_validity
from services.positionbook_service import get_positionbook

nifty_master_bp = Blueprint("nifty_master_bp", __name__, url_prefix="/api/nifty-master")

# Path to the state file (relative to the openalgo root)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(_ROOT, "strategies", "scripts", "nifty_master_state.json")
TRADE_LOG = os.path.join(_ROOT, "nifty_master_trades.csv")


@nifty_master_bp.route("/state")
@check_session_validity
def get_state():
    """Return the current strategy state as JSON."""
    if not os.path.exists(STATE_FILE):
        return jsonify({"error": "Strategy state file not found. Start the strategy engine first."}), 404

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        return jsonify(state)
    except json.JSONDecodeError:
        return jsonify({"error": "Strategy state file is corrupted."}), 500
    except Exception as e:
        return jsonify({"error": f"Failed to read state: {str(e)}"}), 500


@nifty_master_bp.route("/journal")
@check_session_validity
def get_journal():
    """Return trade journal with aggregate performance stats."""
    if not os.path.exists(TRADE_LOG):
        return jsonify({"trades": [], "stats": {}})

    try:
        trades = []
        with open(TRADE_LOG, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                trades.append(row)

        # --- LIFETIME STATS CALCULATION ---
        lifetime_exits = [t for t in trades if t.get("action") == "EXIT"]
        l_wins = sum(1 for t in lifetime_exits if float(t.get("pnl", 0)) >= 0)
        l_losses = sum(1 for t in lifetime_exits if float(t.get("pnl", 0)) < 0)
        l_total_pnl = sum(float(t.get("pnl", 0)) for t in lifetime_exits)
        l_win_rate = (l_wins / (l_wins + l_losses) * 100) if (l_wins + l_losses) > 0 else 0
        l_gross_wins = sum(float(t.get("pnl", 0)) for t in lifetime_exits if float(t.get("pnl", 0)) >= 0)
        l_gross_losses = abs(sum(float(t.get("pnl", 0)) for t in lifetime_exits if float(t.get("pnl", 0)) < 0))
        l_profit_factor = l_gross_wins / l_gross_losses if l_gross_losses > 0 else 0

        lifetime_stats = {
            "win_rate": round(l_win_rate, 1),
            "total_pnl": round(l_total_pnl, 0),
            "profit_factor": round(l_profit_factor, 2)
        }

        # Limit to last N trades for the table display (query param, default 50)
        display_limit = int(request.args.get("limit", 50))
        display_trades = trades[-display_limit:]

        # Compute RECENT stats from sliced list
        exits = [t for t in display_trades if t.get("action") == "EXIT"]
        wins, losses, total_pnl = 0, 0, 0.0
        by_slot = {}  # per-engine stats

        for t in exits:
            try:
                pnl = float(t.get("pnl", 0))
            except (ValueError, TypeError):
                continue
            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

            slot = t.get("slot", "unknown")
            if slot not in by_slot:
                by_slot[slot] = {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0}
            by_slot[slot]["trades"] += 1
            by_slot[slot]["total_pnl"] += pnl
            if pnl >= 0:
                by_slot[slot]["wins"] += 1
            else:
                by_slot[slot]["losses"] += 1

        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        avg_win = sum(float(t.get("pnl", 0)) for t in exits if float(t.get("pnl", 0)) >= 0) / max(wins, 1)
        avg_loss = sum(float(t.get("pnl", 0)) for t in exits if float(t.get("pnl", 0)) < 0) / max(losses, 1)
        gross_wins = sum(float(t.get("pnl", 0)) for t in exits if float(t.get("pnl", 0)) >= 0)
        gross_losses = abs(sum(float(t.get("pnl", 0)) for t in exits if float(t.get("pnl", 0)) < 0))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

        stats = {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 0),
            "avg_win": round(avg_win, 0),
            "avg_loss": round(avg_loss, 0),
            "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "∞",
            "by_slot": {k: {**v, "total_pnl": round(v["total_pnl"], 0),
                            "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0}
                        for k, v in by_slot.items()},
        }

        return jsonify({
            "trades": display_trades,
            "stats": stats,
            "lifetime_stats": lifetime_stats
        })

    except Exception as e:
        return jsonify({"error": f"Failed to read journal: {str(e)}"}), 500


@nifty_master_bp.route("/broker-positions")
@check_session_validity
def get_broker_positions():
    """Return live broker positions as JSON for P&L matching."""
    login_username = session.get("user")
    auth_token = get_auth_token(login_username)
    broker = session.get("broker")

    if not auth_token or not broker:
        return jsonify({"status": "error", "message": "Broker session not found"}), 401

    try:
        success, response, status_code = get_positionbook(auth_token=auth_token, broker=broker)
        return jsonify(response), status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
