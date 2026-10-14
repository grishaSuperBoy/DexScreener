import json
import logging
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dex_screener.binance import get_binance_target_symbols
from dex_screener.dexscreener import search_best_pool, fetch_batches
from dex_screener.storage import safe_load_json, safe_save_json
from dex_screener.monitor import PoolMonitor
from dex_screener.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_map")

def main():
    print("[*] Загрузка токенов с Binance Futures ($1M-$50M)...")
    targets = get_binance_target_symbols(1_000_000, 50_000_000)
    print(f"[*] Найдено {len(targets)} токенов на Binance.")

    # Загружаем существующую карту, если есть
    map_file = settings.pools_map_file
    dex_map = safe_load_json(map_file)
    existing_symbols = {info.get("symbol", "").upper() for info in dex_map.values()}
    print(f"[*] Уже известно {len(dex_map)} пулов.")

    # Отбираем токены, которых еще нет
    to_scan = [s for s, vol in targets if s.upper() not in existing_symbols]
    print(f"[*] Сканируем ВСЕ {len(to_scan)} оставшихся токенов на DexScreener...")

    found_new = 0
    # Обходим все оставшиеся токены
    for i, sym in enumerate(to_scan, 1):
        # Пропускаем явные акции / фиат / сырье
        if sym in {"MSFT", "NVDA", "AAPL", "GOOGL", "AMZN", "META", "TSLA", "SPY", "QQQ", "TQQQ", "COIN", "ORCL", "ARM", "HOOD", "DELL", "AVGO", "INTC", "AMD", "BABA", "TSM", "PLTR", "QCOM"}:
            continue

        try:
            pool = search_best_pool(sym, min_liq_usd=40_000)
            if pool:
                key = f"{pool['chain']}:{pool['pair_address']}"
                dex_map[key] = pool
                found_new += 1
                print(f"  [{i}/{len(to_scan)}] + {sym:10} -> ${pool['liquidity_usd']:>11,.0f} ({pool['chain']}/{pool['dex']}) [Всего в карте: {len(dex_map)}]")
                if found_new % 5 == 0:
                    safe_save_json(dex_map, map_file)
            else:
                print(f"  [{i}/{len(to_scan)}] - {sym:10} (нет DEX-пула > $40k)")
        except Exception as e:
            logger.error(f"Ошибка {sym}: {e}")

        time.sleep(0.22)

    safe_save_json(dex_map, map_file)
    print(f"\n[✓] Сохранено {len(dex_map)} пулов в {map_file}")

    # Теперь опрашиваем их батчем, чтобы сразу заполнить state
    print("[*] Опрос всех пулов пачками через Batch API...")
    live_pairs = fetch_batches(dex_map, batch_size=30)
    print(f"[✓] Получено {len(live_pairs)} пулов онлайн.")

    state = safe_load_json(settings.state_file)
    monitor = PoolMonitor(settings=settings, dex_map=dex_map, state=state)
    monitor.check_cycle(live_pairs)
    safe_save_json(monitor.state, settings.state_file)
    print(f"[✓] pools_state.json успешно обновлен ({len(monitor.state)} записей)!")

if __name__ == "__main__":
    main()
