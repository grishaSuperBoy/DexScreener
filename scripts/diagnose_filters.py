import json
import time
from pathlib import Path
from dex_screener.config import settings

state_file = Path("data/pools_state.json")
map_file = Path("data/binance_dex_map.json")

if not state_file.exists() or not map_file.exists():
    print("Files not found")
    exit(1)

with open(state_file, encoding="utf-8") as f:
    state = json.load(f)

with open(map_file, encoding="utf-8") as f:
    dex_map = json.load(f)

now = time.time()

# Stats counters
short_candidates = 0
reasons_short = {
    "price_lag_fail": 0,
    "coherence_fail": 0,
    "impact_fail": 0,
    "active_txns_fail": 0,
    "balanced_quote_fail": 0,
    "peak_age_3h_fail": 0,
    "PASSED": 0
}

long_candidates = 0
reasons_long = {
    "not_active_or_balanced": 0,
    "threshold_not_reached": 0,
    "stealth_price_too_high": 0,
    "stealth_tx_ratio_fail": 0,
    "stealth_buys_count_fail": 0,
    "peak_age_3h_fail": 0,
    "PASSED": 0
}

near_misses_short = []
near_misses_long = []

for key, st in state.items():
    history = st.get("history", [])
    if not history or len(history) < 2:
        continue

    map_info = dex_map.get(key, {})
    sym = map_info.get("symbol", "UNKNOWN")
    quote_sym = map_info.get("quote_symbol", "USDT")
    is_stable = quote_sym.upper() in {"USDT", "USDC", "BUSD", "USD", "DAI", "FDUSD", "TUSD"}

    last_snap = history[-1]
    quote_res = last_snap.get("quote_res", 0.0)
    base_res = last_snap.get("base_res", 0.0)
    liq_usd = last_snap.get("liq_usd", 0.0)
    price_usd = last_snap.get("price_usd", 0.0)
    buys_h1 = st.get("buys_h1", 0)
    sells_h1 = st.get("sells_h1", 0)
    vol_h1 = st.get("vol_h1_usd", 0.0)
    total_txns_h1 = buys_h1 + sells_h1

    is_active_pool = total_txns_h1 >= settings.min_pool_txns_h1
    quote_val_usd = quote_res if is_stable else (liq_usd * 0.5)
    quote_tvl_share = (quote_val_usd / liq_usd * 100.0) if liq_usd > 0 else 0.0
    is_balanced_pool = quote_tvl_share >= settings.min_quote_tvl_share_pct if is_stable else True

    binance_vol = map_info.get("binance_vol", 0.0)
    bybit_vol = map_info.get("bybit_vol", 0.0)
    cex_hourly_vol = (binance_vol + bybit_vol) / 24.0

    timeframes = [("1h", 3600), ("3h", 10800)]
    for tf_name, tf_seconds in timeframes:
        tf_history = [s for s in history if (now - s.get("ts", 0)) <= tf_seconds]
        if not tf_history or len(tf_history) < 2:
            continue

        tf_mult = settings.tf_3h_threshold_multiplier if tf_name == "3h" else 1.0
        tf_hours = tf_seconds / 3600.0

        # --- Check SHORT ---
        max_snap = max(tf_history, key=lambda x: x.get("quote_res", 0.0))
        max_q = max_snap.get("quote_res", quote_res)
        q_drop = ((quote_res - max_q) / max_q * 100.0) if max_q > 0 else 0.0

        if q_drop <= (settings.liq_drop_threshold_pct * tf_mult):
            short_candidates += 1
            max_liq = max_snap.get("liq_usd", liq_usd)
            usd_drop = ((liq_usd - max_liq) / max_liq * 100.0) if max_liq > 0 else 0.0
            max_p = max_snap.get("price_usd", price_usd)
            p_chg = ((price_usd - max_p) / max_p * 100.0) if max_p > 0 else 0.0

            if tf_name == "3h" and (now - max_snap.get("ts", now)) <= 3600:
                reasons_short["peak_age_3h_fail"] += 1
            elif not (settings.max_price_drop_pct <= p_chg <= settings.max_price_pump_pct):
                reasons_short["price_lag_fail"] += 1
            else:
                coh = abs(usd_drop) / abs(q_drop) if abs(q_drop) > 0.001 else 1.0
                q_drain = abs(max_q - quote_res) if is_stable else 0.0
                real_drain = max(abs(max_liq - liq_usd), q_drain)
                total_win_vol = (cex_hourly_vol + vol_h1) * tf_hours
                impact = (real_drain / total_win_vol) if total_win_vol > 0 else 0.0

                if coh < settings.min_drain_coherence_ratio:
                    reasons_short["coherence_fail"] += 1
                    near_misses_short.append((sym, tf_name, f"Coh {coh:.3f} < {settings.min_drain_coherence_ratio}", q_drop, p_chg))
                elif impact < settings.min_drain_impact_pct:
                    reasons_short["impact_fail"] += 1
                    near_misses_short.append((sym, tf_name, f"Impact {impact*100:.2f}% < {settings.min_drain_impact_pct*100:.2f}%", q_drop, p_chg))
                elif not is_active_pool:
                    reasons_short["active_txns_fail"] += 1
                    near_misses_short.append((sym, tf_name, f"Txns {total_txns_h1} < {settings.min_pool_txns_h1}", q_drop, p_chg))
                elif not is_balanced_pool:
                    reasons_short["balanced_quote_fail"] += 1
                    near_misses_short.append((sym, tf_name, f"QuoteShare {quote_tvl_share:.2f}% < {settings.min_quote_tvl_share_pct}%", q_drop, p_chg))
                else:
                    reasons_short["PASSED"] += 1

        # --- Check LONG ---
        min_snap = min(tf_history, key=lambda x: x.get("quote_res", quote_res))
        min_q = min_snap.get("quote_res", quote_res)
        q_gain = ((quote_res - min_q) / min_q * 100.0) if min_q > 0 else 0.0
        if q_gain >= (settings.stealth_buy_quote_pct * tf_mult):
            long_candidates += 1
            min_base = min_snap.get("base_res", base_res)
            base_drop = ((base_res - min_base) / min_base * 100.0) if min_base > 0 else 0.0
            min_p = min_snap.get("price_usd", price_usd)
            p_chg = ((price_usd - min_p) / min_p * 100.0) if min_p > 0 else 0.0
            tx_ratio = (buys_h1 / sells_h1) if sells_h1 > 0 else float(buys_h1)

            if tf_name == "3h" and (now - min_snap.get("ts", now)) <= 3600:
                reasons_long["peak_age_3h_fail"] += 1
            elif not (is_active_pool and is_balanced_pool):
                reasons_long["not_active_or_balanced"] += 1
            elif p_chg > settings.stealth_buy_max_price_pct:
                reasons_long["stealth_price_too_high"] += 1
                near_misses_long.append((sym, tf_name, f"Price +{p_chg:.1f}% > max {settings.stealth_buy_max_price_pct}% (уже улетела)", q_gain))
            elif tx_ratio < settings.stealth_buy_min_tx_ratio:
                reasons_long["stealth_tx_ratio_fail"] += 1
                near_misses_long.append((sym, tf_name, f"TxRatio {tx_ratio:.1f} < {settings.stealth_buy_min_tx_ratio}", q_gain))
            elif buys_h1 < settings.stealth_buy_min_buys_h1:
                reasons_long["stealth_buys_count_fail"] += 1
                near_misses_long.append((sym, tf_name, f"Buys {buys_h1} < {settings.stealth_buy_min_buys_h1}", q_gain))
            else:
                reasons_long["PASSED"] += 1

print("\n=== SHORT CANDIDATES DIAGNOSTIC ===")
print("Total raw candidates with liquidity drop:", short_candidates)
print(json.dumps(reasons_short, indent=2))
print("\nSample Short Filter Rejections:")
for m in near_misses_short[:8]:
    print(" ", m)

print("\n=== LONG CANDIDATES DIAGNOSTIC ===")
print("Total raw candidates with liquidity inflow:", long_candidates)
print(json.dumps(reasons_long, indent=2))
print("\nSample Long Filter Rejections:")
for m in near_misses_long[:8]:
    print(" ", m)
