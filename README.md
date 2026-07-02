# Nifty Master - Algorithmic Trading Suite

**Nifty Master** is a self-hosted algorithmic trading platform built by extending the open-source **OpenAlgo** framework. It incorporates custom intraday trading strategies, backtesting components, custom API endpoints, and a dedicated frontend interface tailored for Indian Index Options (Nifty/Bank Nifty).

---

## 🚀 My Custom Implementations & Work

I have extended the OpenAlgo core codebase with the following custom components and strategies:

### 1. Custom Trading Strategies & Logic
- **[nifty_weekly_master.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/nifty_weekly_master.py)**: A comprehensive automated trading strategy for executing weekly option selling/buying setups on Nifty indices.
- **[nifty_5m_triple_confirm.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/nifty_5m_triple_confirm.py)**: Intraday strategy operating on a 5-minute timeframe using a triple indicator confirmation logic to minimize false signals.
- **[option buying/option_buying.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/option%20buying/option_buying.py)**: Dedicated options buying script with trailing stop-losses, premium decay checks, and position sizing.
- **[option selling/nifty_weekly_master.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/option%20selling/nifty_weekly_master.py)**: Weekly option writing strategy designed to capture time decay (theta) with predefined risk boundaries.
- **[options intraday/spottest.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/options%20intraday/spottest.py)**: Utility script to fetch and align option strikes dynamically based on the spot index price.

### 2. Backtesting Engine & Custom Reports
- **[nifty_backtest.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/nifty_backtest.py)**: Backtesting script designed to run simulations over historical Nifty 5-minute bar data.
- **Visual Reports**:
  - **[nifty_report.html](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/nifty_report.html)**: Interactive HTML report generated post-backtest, displaying equity curves, drawdown curves, and statistics.
  - **[reverse_condor_report.html](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/reverse_condor_report.html)**: Backtest performance report for Reverse Condor strategies.

### 3. Frontend & API Integration
- **[NiftyMaster.tsx](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/frontend/src/pages/NiftyMaster.tsx)**: React UI view integrated into the sidebar to configure, start, stop, and monitor Nifty Master strategies.
- **[nifty_master_api.py](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/blueprints/nifty_master_api.py)**: Backend Flask API endpoint routing to manage real-time strategy operations.
- **[Testing.tsx](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/frontend/src/pages/Testing.tsx)**: UI environment for validating live broker connections and feed latency.

### 4. Setup Batches
- **[start_algo.bat](file:///c:/Users/Kush%20Tejani/Downloads/github/openalgo/openalgo/start_algo.bat)**: Automated batch startup script initializing local servers, database checkpoints, and setting up the environment configuration.

---

## 🛠️ Base Architecture: OpenAlgo Framework

This project is built on top of the open-source **OpenAlgo** ecosystem.
OpenAlgo provides a robust self-hosted infrastructure:
- **Unified REST API Layer**: A standardized interface across 30+ Indian brokers (AngelOne, Zerodha, Fyers, Flattrade, Kotak Neo, etc.).
- **Real-Time Data**: WebSocket streaming normalized across brokers.
- **Security**: Argon2 password hashing and Fernet-symmetric API key encryption.

For the full list of supported brokers and detailed framework capabilities, refer to the [OpenAlgo Official Documentation](https://docs.openalgo.in).

---

## 💻 Tech Stack Used

- **Frontend**: React 19, TypeScript, Tailwind CSS, shadcn/ui, TanStack Query, Zustand, xyflow/React Flow.
- **Backend**: Flask 3.0, SQLAlchemy 2.0 ORM, Flask-SocketIO.
- **Databases**: SQLite (main, logs, sandbox), DuckDB (historical datasets).
- **Communication**: ZeroMQ, Socket.IO.

---

## 🚀 Quick Start & Installation

### Requirements
- Python 3.11 or higher
- Node.js 20+ (for running the React frontend)
- `uv` package manager (recommended for speed)

### Setup
1. Clone this repository:
   ```bash
   git clone https://github.com/KushT00/nifty_master.git
   cd nifty_master
   ```
2. Set up the environment variables:
   ```bash
   cp .sample.env .env
   # Edit .env and enter your broker API credentials
   ```
3. Run the application:
   ```bash
   uv run app.py
   ```
   Or run the batch setup file directly on Windows:
   ```bash
   start_algo.bat
   ```

The dashboard will be available at `http://127.0.0.1:5000`.

---

## ⚠️ Disclaimer
**This software is for educational purposes only. Do not risk money which you are afraid to lose. USE THE SOFTWARE AT YOUR OWN RISK. THE AUTHORS AND ALL AFFILIATES ASSUME NO RESPONSIBILITY FOR YOUR TRADING RESULTS.**
