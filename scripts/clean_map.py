import json
import logging
from pathlib import Path
from dex_screener.dexscreener import search_best_pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

map_file = Path("data/binance_dex_map.json")
state_file = Path("data/pools_state.json")

if not map_file.exists():
    logger.error("Карта не найдена")
    exit(1)

with open(map_file, "r", encoding="utf-8") as f:
    dex_map = json.load(f)

# Список монет, которые давали искаженные результаты
distorted_syms = ["USELESS", "CAKE", "TIA", "ONE", "MMT", "XDC", "BSV", "SAFE"]
updated_count = 0
keys_to_remove = []
entries_to_add = {}

for key, val in list(dex_map.items()):
    sym = val.get("symbol", "").upper()
    if sym in distorted_syms:
        logger.info(f"Проверка и обновление монеты: {sym} (текущий пул {key})")
        new_pool = search_best_pool(
            sym,
            binance_vol=val.get("binance_vol", 0.0),
            bybit_vol=val.get("bybit_vol", 0.0),
            exchanges=val.get("exchanges", []),
        )
        if new_pool:
            new_key = f"{new_pool['chain']}:{new_pool['pair_address']}"
            if new_key != key:
                logger.info(f"Заменяем искаженный пул {key} на реальный {new_key} ({new_pool['dex']}, {new_pool['quote_symbol']}, ${new_pool['liquidity_usd']:,.0f})")
                keys_to_remove.append(key)
                entries_to_add[new_key] = new_pool
                updated_count += 1
            else:
                logger.info(f"Пул {key} актуален.")

for k in keys_to_remove:
    del dex_map[k]

dex_map.update(entries_to_add)

with open(map_file, "w", encoding="utf-8") as f:
    json.dump(dex_map, f, indent=2, ensure_ascii=False)

logger.info(f"Обновлено {updated_count} пулов в {map_file}. Всего пулов: {len(dex_map)}")

# Очистим удаленные ключи из pools_state.json
if state_file.exists():
    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)
    for k in keys_to_remove:
        if k in state:
            del state[k]
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    logger.info(f"Очищен стейт для устаревших пулов в {state_file}")
