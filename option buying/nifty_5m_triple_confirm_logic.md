# Nifty 5m | Triple Confirm + Smart EMA Re-Entry + Trailing Stop Loss (TSL) v4
## Comprehensive Strategy Logic Document

This document outlines the exact trade logic, state variables, indicators, entry/exit conditions, and protective safeguards defined in the Pinescript strategy `Nifty 5m | Triple Confirm + Smart EMA Re-Entry + TSL v4`.

---

## 1. Parameters & Indicators

### Technical Indicators
1. **SMA Fast**: Simple Moving Average with a period of **68** (Fast Trend).
2. **SMA Slow**: Simple Moving Average with a period of **90** (Medium Trend).
3. **EMA Trend**: Exponential Moving Average with a period of **340** (Macro Regime Filter).
4. **Distance Metric**: The absolute percentage difference between the closing price and the `EMA 340`:
   $$\text{Distance (\%)} = \frac{|\text{Close} - \text{EMA}_{340}|}{\text{EMA}_{340}} \times 100$$

### Key Input Parameters
- **Initial Stop Loss (`slPct`)**: `0.75%` calculated on **Nifty Spot Index** from average entry Spot price.
- **Trailing Stop Loss (`trailPct`)**: `0.75%` calculated on **Nifty Spot Index** trailing from Spot peak/trough.
- **Base Entry Distance (`distPct`)**: `0.20%` minimum distance between Close and `EMA 340` (strict filter to prevent trading in flat/choppy markets near EMA).
- **Re-Entry Distance (`reEntryDistPct`)**: `0.10%` minimum distance between Close and `EMA 340` (relaxed filter to allow trades to execute close to the pullback level).

### Option Trading Execution
The strategy evaluates all conditions and indicators on the **Nifty Spot Index (`NSE_INDEX:NIFTY`)** but executes trades in **weekly index options**:
- **Bullish / LONG Entry**: Buys the Call Option (`CE`) of the **current weekly expiry** whose premium price is closest to **Rs. 200**.
- **Bearish / SHORT Entry**: Buys the Put Option (`PE`) of the **current weekly expiry** whose premium price is closest to **Rs. 200**.
- **Exits**: When the Spot-based Trailing SL is breached on Nifty Spot index candle close, the active option position is squared off via market order.

---

## 2. Trend & Pullback Memory State

The strategy relies on a persistent state machine to track crossovers, trends, and pullbacks.

### A. Trend Memory (`bullTrend` / `bearTrend`)
- **Bullish Trend (`bullTrend = true`, `bearTrend = false`)**: Triggered when the `SMA 68` crosses *above* `SMA 90` (`bullCross`).
- **Bearish Trend (`bearTrend = true`, `bullTrend = false`)**: Triggered when the `SMA 68` crosses *under* `SMA 90` (`bearCross`).
- This trend memory persists across bars until an opposing crossover occurs.

### B. Pullback Arming (`longPullbackReady` / `shortPullbackReady`)
Pullback readiness is strictly **close-based**. Wick-only spikes or dips beyond the EMA are ignored.
- **Long Pullback Arming (`longPullbackReady = true`)**: Armed during an active `bullTrend` if a candle's closing price drops **below** the `EMA 340` (`close < EMA 340`).
- **Short Pullback Arming (`shortPullbackReady = true`)**: Armed during an active `bearTrend` if a candle's closing price rises **above** the `EMA 340` (`close > EMA 340`).
- **State Reset**:
  - `longPullbackReady` resets to `false` immediately on a `bearCross` (trend change).
  - `shortPullbackReady` resets to `false` immediately on a `bullCross` (trend change).
  - Both pullback flags are reset when an active position is closed (see Debounce below) or when an entry is triggered.

---

## 3. Entry Setup Logic

The strategy defines two distinct entry setups: **Base Entries** (crossover-based) and **Smart Re-Entries** (pullback-based). All entries require that there is no open position (`position_size == 0`).

### Setup 1: Base (Initial) Entries
Base entries capture the momentum of a fresh crossover, provided the market is trending away from the EMA.

* **LONG Base Entry Conditions (`longBase` is true)**:
  1. **Cross**: `SMA 68` crosses *above* `SMA 90` (`bullCross`).
  2. **Regime**: Candle close is **above** `EMA 340` (`aboveEMA`).
  3. **Distance Check**: Distance from `EMA 340` is **>= 0.20%** (`validDistance`).
  4. **Debounce**: Preventing duplicate consecutive entries in the same trend direction (must satisfy `lastSignal != 1` or `oppositeSeen`).

* **SHORT Base Entry Conditions (`shortBase` is true)**:
  1. **Cross**: `SMA 68` crosses *under* `SMA 90` (`bearCross`).
  2. **Regime**: Candle close is **below** `EMA 340` (`belowEMA`).
  3. **Distance Check**: Distance from `EMA 340` is **>= 0.20%** (`validDistance`).
  4. **Debounce**: Preventing duplicate consecutive entries in the same trend direction (must satisfy `lastSignal != -1` or `oppositeSeen`).

---

### Setup 2: Smart Re-Entries
Smart Re-entries capture trades after a healthy trend pullback. No crossover is required; it is triggered entirely by close-based reversion back across the EMA.

* **LONG Smart Re-Entry Conditions (`longReEntry` is true)**:
  1. **Trend Active**: `bullTrend` remains active (no `bearCross` has occurred).
  2. **Pullback Confirmed**: `longPullbackReady` is armed (a previous candle closed below the `EMA 340` during this trend).
  3. **Trigger**: Current candle closes **above** the `EMA 340` (`aboveEMA`).
  4. **Re-Entry Distance**: Distance from `EMA 340` is **>= 0.10%** (`reEntryDist`).

* **SHORT Smart Re-Entry Conditions (`shortReEntry` is true)**:
  1. **Trend Active**: `bearTrend` remains active (no `bullCross` has occurred).
  2. **Pullback Confirmed**: `shortPullbackReady` is armed (a previous candle closed above the `EMA 340` during this trend).
  3. **Trigger**: Current candle closes **below** the `EMA 340` (`belowEMA`).
  4. **Re-Entry Distance**: Distance from `EMA 340` is **>= 0.10%** (`reEntryDist`).

---

## 4. Debounce & State Reset Engine

To prevent multiple overlapping entries in a single trend direction, a robust debounce state engine is used.

### State Variables
- `lastSignal`: Records the direction of the last entered trade (`1` for LONG, `-1` for SHORT, `0` for none/reset).
- `oppositeSeen`: A boolean flag tracking whether an opposing crossover has been observed since the last entry.

### Debounce State Transitions
1. **On Entry Trigger**:
   - `lastSignal` is set to `1` (LONG) or `-1` (SHORT).
   - `oppositeSeen` is set to `false`.
   - `longPullbackReady` or `shortPullbackReady` is reset to `false`.
2. **On Opposing Cross**:
   - If `lastSignal == 1` and `bearCross` occurs, `oppositeSeen := true`.
   - If `lastSignal == -1` and `bullCross` occurs, `oppositeSeen := true`.
3. **On Position Square-Off (TSL Exit)**:
   - When the strategy position size drops from non-zero to zero (trade exited):
     - `oppositeSeen := true` (allows fresh base entry if a new setup appears).
     - `lastSignal := 0` (resets signal lock).
     - **Pullback Reset**: If `bullTrend` is active, `longPullbackReady := false`. If `bearTrend` is active, `shortPullbackReady := false`. This forces the market to make a new pullback (closing across the EMA) before a re-entry can be armed, preventing immediate re-entry on the very next bar after an exit.

---

## 5. Trailing Stop Loss (TSL) Engine

The TSL is calculated and trailed entirely on the **Nifty Spot Index (`NSE_INDEX:NIFTY`)** candle highs, lows, and closes. It applies identically to both **Base Entries** and **Smart Re-Entries**. It updates on every bar and only executes exits on a **5-minute candle close**, preventing wick-based noise exits.

### A. Initialization (When a new option position is opened)
Upon option entry, we record the closing price of the Nifty Spot Index candle that triggered the entry as `entrySpotPrice`.
- **For LONG (Call Option bought)**:
  - `trailHigh` is set to the current candle's Spot `high`.
  - `stopLoss` is set to `entrySpotPrice * (1 - 0.0075)` (0.75% below the entry Spot price).
- **For SHORT (Put Option bought)**:
  - `trailLow` is set to the current candle's Spot `low`.
  - `stopLoss` is set to `entrySpotPrice * (1 + 0.0075)` (0.75% above the entry Spot price).

### B. Trailing Logic (Updating on every subsequent Nifty Spot 5m bar close)
* **LONG (Call Option) Position Trailing**:
  1. Update `trailHigh`: Keep track of the highest Spot price high seen during the trade:
     $$\text{trailHigh} = \max(\text{trailHigh}, \text{current\_spot\_high})$$
  2. Compute trailing threshold:
     $$\text{dynamicSL} = \text{trailHigh} \times (1 - 0.0075)$$
  3. Update `stopLoss`: The Spot-based stop loss can only ratchet upwards:
     $$\text{stopLoss} = \max(\text{stopLoss}, \text{dynamicSL})$$
  4. **Exit Check**: If the 5-minute Nifty Spot candle **closes** below or equal to `stopLoss` (`close <= stopLoss`), immediately sell the Call option via market order.

* **SHORT (Put Option) Position Trailing**:
  1. Update `trailLow`: Keep track of the lowest Spot price low seen during the trade:
     $$\text{trailLow} = \min(\text{trailLow}, \text{current\_spot\_low})$$
  2. Compute trailing threshold:
     $$\text{dynamicSL} = \text{trailLow} \times (1 + 0.0075)$$
  3. Update `stopLoss`: The Spot-based stop loss can only ratchet downwards:
     $$\text{stopLoss} = \min(\text{stopLoss}, \text{dynamicSL})$$
  4. **Exit Check**: If the 5-minute Nifty Spot candle **closes** above or equal to `stopLoss` (`close >= stopLoss`), immediately sell the Put option via market order.
