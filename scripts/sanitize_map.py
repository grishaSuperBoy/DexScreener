import json
import logging
import time
from pathlib import Path

from dex_screener.dexscreener import search_best_pool, safe_float

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sanitize_map")

map_file = Path("data/binance_dex_map.json")
if not map_file.exists():
    logger.error("data/binance_dex_map.json does not exist!")
    exit(1)

with open(map_file, "r", encoding="utf-8") as f:
    dex_map = json.load(f)

logger.info(f"Loaded {len(dex_map)} pools from {map_file}")

# Identify suspicious Solana CLMM pools with inflated TVL
broken_keys = []
for key, info in dex_map.items():
    liq = safe_float(info.get("liquidity_usd", 0.0))
    chain = info.get("chain", "").lower()
    dex = info.get("dex", "").lower()

    if chain == "solana" and "raydium" in dex and liq > 10_000_000:
        broken_keys.append((key, info))

logger.info(f"Found {len(broken_keys)} suspicious large Solana CLMM pools to check/re-map.")

fixed_count = 0
purged_count = 0
new_map = dict(dex_map)

for old_key, info in broken_keys:
    sym = info.get("symbol")
    b_vol = info.get("binance_vol", 0.0)
    by_vol = info.get("bybit_vol", 0.0)
    exchanges = info.get("exchanges", ["binance"])

    logger.info(f"Re-mapping {sym} (current: {old_key}, liq: ${info.get('liquidity_usd', 0):,.0f})...")
    time.sleep(0.35)

    try:
        best = search_best_pool(
            symbol=sym,
            binance_vol=b_vol,
            bybit_vol=by_vol,
            exchanges=exchanges,
            min_liq_usd=50_000.0,
            min_vol_24h_usd=5_000.0,
        )

        # Remove the broken pool
        if old_key in new_map:
            del new_map[old_key]

        if best:
            new_key = f"{best['chain']}:{best['pair_address']}"
            new_map[new_key] = best
            logger.info(f" -> REMAPPED {sym}: {new_key} on {best['chain']}/{best['dex']} (liq: ${best['liquidity_usd']:,.0f})")
            fixed_count += 1
        else:
            logger.info(f" -> PURGED {sym}: No clean pool meeting minimum criteria found.")
            purged_count += 1
    except Exception as e:
        logger.error(f"Error checking {sym}: {e}")

logger.info(f"Sanitization complete! Remapped: {fixed_count}, Purged: {purged_count}, Total in map now: {len(new_map)}")

# Backup old map
backup_file = Path("data/binance_dex_map.backup.json")
with open(backup_file, "w", encoding="utf-8") as f:
    json.dump(dex_map, f, indent=2, ensure_ascii=False)
logger.info(f"Backup saved to {backup_file}")

with open(map_file, "w", encoding="utf-8") as f:
    json.dump(new_map, f, indent=2, ensure_ascii=False)
logger.info(f"Saved sanitized map to {map_file}")
