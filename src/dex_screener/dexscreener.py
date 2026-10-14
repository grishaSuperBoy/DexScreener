from collections import defaultdict
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import requests
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn

logger = logging.getLogger(__name__)

DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEXSCREENER_PAIRS_URL = "https://api.dexscreener.com/latest/dex/pairs"

# Доверенные котируемые активы в пулах (защита от фейковых пулов)
ALLOWED_QUOTE_SYMBOLS = {
    "USDT",
    "USDC",
    "USDC.E",
    "USDT0",
    "FDUSD",
    "DAI",
    "SOL",
    "WSOL",
    "WETH",
    "ETH",
    "WBNB",
    "BNB",
    "WAVAX",
    "WMATIC",
    "WPOL",
    "SUI",
    "TON",
}


def safe_float(val: Any, default: float = 0.0) -> float:
    """Безопасно конвертирует значение в float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def search_best_pool(
    symbol: str,
    min_liq_usd: float = 80_000.0,
    min_vol_24h_usd: float = 10_000.0,
    binance_vol: float = 0.0,
    bybit_vol: float = 0.0,
    exchanges: Optional[List[str]] = None,
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    Ищет крупнейший живой DEX-пул для заданного символа на DexScreener.
    """
    if len(symbol) < 2:
        return None

    url = f"{DEXSCREENER_SEARCH_URL}?q={symbol}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"DexScreener search вернул статус {resp.status_code} для {symbol}")
            return None

        data = resp.json()
        pairs = data.get("pairs") or []
    except Exception as e:
        logger.error(f"Сетевая ошибка при поиске пулов для {symbol}: {e}")
        return None

    target_sym_upper = symbol.upper()
    valid_pairs = []

    for p in pairs:
        base_sym = (p.get("baseToken") or {}).get("symbol", "").upper()
        quote_sym = (p.get("quoteToken") or {}).get("symbol", "").upper()
        liq_dict = p.get("liquidity") or {}
        vol_dict = p.get("volume") or {}

        liq_usd = safe_float(liq_dict.get("usd"))
        quote_res = safe_float(liq_dict.get("quote"))
        vol_h24 = safe_float(vol_dict.get("h24"))
        price_usd = safe_float(p.get("priceUsd"))
        txns_h24 = (p.get("txns") or {}).get("h24") or {}
        tx_count_24h = int(safe_float(txns_h24.get("buys")) + safe_float(txns_h24.get("sells")))

        # Проверка баланса пула для стейблкоинов: отсекаем фейковые CLMM пулы с нарисованным TVL
        is_stable_quote = quote_sym in {"USDT", "USDC", "BUSD", "USD", "DAI", "FDUSD", "TUSD"}
        quote_val_usd = quote_res if is_stable_quote else (liq_usd * 0.5)
        quote_share = (quote_val_usd / liq_usd) if liq_usd > 0 else 0.0

        # Если пул со стейблкоином, но стейблкоинов меньше 5% от TVL — это фейк/дутый пул (напр. миллиард токенов и 30 USDC)
        if is_stable_quote and quote_share < 0.05:
            continue

        if (
            base_sym == target_sym_upper
            and liq_usd >= min_liq_usd
            and quote_res > 0
            and vol_h24 >= min_vol_24h_usd
            and quote_sym in ALLOWED_QUOTE_SYMBOLS
        ):
            # Эффективная ликвидность: не позволяет дутому пулу обойти реальный живой пул
            effective_liq = min(liq_usd, quote_val_usd * 2.5) if is_stable_quote else liq_usd
            score = effective_liq * (1.0 + min(vol_h24 / 100_000.0, 5.0))
            valid_pairs.append((p, score, liq_usd))

    if not valid_pairs:
        return None

    best_pair, best_score, best_liq = max(valid_pairs, key=lambda item: item[1])

    chain = best_pair.get("chainId", "")
    pair_addr = best_pair.get("pairAddress", "")
    quote_token = best_pair.get("quoteToken") or {}

    exch_list = list(exchanges or [])
    if not exch_list:
        if binance_vol > 0:
            exch_list.append("binance")
        if bybit_vol > 0:
            exch_list.append("bybit")
        if not exch_list:
            exch_list = ["binance"]

    return {
        "symbol": symbol,
        "chain": chain,
        "pair_address": pair_addr,
        "dex": best_pair.get("dexId", "unknown"),
        "quote_symbol": quote_token.get("symbol", ""),
        "liquidity_usd": best_liq,
        "binance_vol": binance_vol,
        "bybit_vol": bybit_vol,
        "exchanges": exch_list,
        "url": best_pair.get("url", ""),
    }


def build_dex_map(
    symbols: List[Any],
    min_liq_usd: float = 80_000.0,
    min_vol_24h_usd: float = 10_000.0,
    rate_limit_delay: float = 0.35,
    show_progress: bool = True,
    existing_map: Optional[Dict[str, Any]] = None,
    save_callback: Optional[Any] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Обходит список символов и строит карту {chain:pair_address -> info}.
    Поддерживает мульти-биржевые свойства (Binance + Bybit).
    """
    dex_map: Dict[str, Dict[str, Any]] = dict(existing_map or {})
    already_found_symbols = {info.get("symbol", "").upper() for info in dex_map.values()}

    # Словарь метаданных по всем входным целям
    targets_meta: Dict[str, Dict[str, Any]] = {}
    for item in symbols:
        if isinstance(item, dict):
            sym = str(item.get("symbol", "")).upper()
            b_vol = safe_float(item.get("binance_vol"))
            by_vol = safe_float(item.get("bybit_vol"))
            exchs = list(item.get("exchanges", []))
        elif isinstance(item, (tuple, list)):
            sym = str(item[0]).upper()
            b_vol = safe_float(item[1]) if len(item) > 1 else 0.0
            by_vol = safe_float(item[2]) if len(item) > 2 else 0.0
            exchs = list(item[3]) if len(item) > 3 else (["binance"] if b_vol > 0 else ["bybit"])
        else:
            sym = str(item).upper()
            b_vol, by_vol, exchs = 0.0, 0.0, ["binance"]

        if sym:
            existing_meta = targets_meta.get(sym, {"binance_vol": 0.0, "bybit_vol": 0.0, "exchanges": set()})
            existing_meta["binance_vol"] = max(existing_meta["binance_vol"], b_vol)
            existing_meta["bybit_vol"] = max(existing_meta["bybit_vol"], by_vol)
            existing_meta["exchanges"].update(exchs)
            targets_meta[sym] = existing_meta

    # Обновляем биржевые теги для уже существующих в карте пулов
    for key, p_info in dex_map.items():
        sym_u = (p_info.get("symbol") or "").upper()
        if sym_u in targets_meta:
            meta = targets_meta[sym_u]
            curr_exchs = set(p_info.get("exchanges") or (["binance"] if p_info.get("binance_vol", 0) > 0 else ["bybit"]))
            curr_exchs.update(meta["exchanges"])
            p_info["exchanges"] = sorted(list(curr_exchs))
            if meta["binance_vol"] > 0:
                p_info["binance_vol"] = meta["binance_vol"]
            if meta["bybit_vol"] > 0:
                p_info["bybit_vol"] = meta["bybit_vol"]

    # Формируем список тех, кого ещё нет в карте
    normalized_targets: List[Tuple[str, float, float, List[str]]] = []
    for sym_u, meta in targets_meta.items():
        if sym_u not in already_found_symbols:
            normalized_targets.append((
                sym_u,
                meta["binance_vol"],
                meta["bybit_vol"],
                sorted(list(meta["exchanges"])),
            ))

    if already_found_symbols:
        logger.info(
            f"Уже известно {len(already_found_symbols)} пулов. "
            f"Осталось проверить {len(normalized_targets)} токенов."
        )

    if not normalized_targets:
        return dex_map

    new_pools_count = 0

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
        ) as progress:
            task = progress.add_task(
                f"[cyan]Поиск DEX-пулов ({len(normalized_targets)} токенов)...",
                total=len(normalized_targets),
            )

            for sym, b_vol, by_vol, exchs in normalized_targets:
                pool_info = search_best_pool(
                    sym,
                    min_liq_usd=min_liq_usd,
                    min_vol_24h_usd=min_vol_24h_usd,
                    binance_vol=b_vol,
                    bybit_vol=by_vol,
                    exchanges=exchs,
                )
                if pool_info:
                    key = f"{pool_info['chain']}:{pool_info['pair_address']}"
                    dex_map[key] = pool_info
                    new_pools_count += 1
                    progress.console.print(
                        f"  [green]+[/green] {sym:10} -> ${pool_info['liquidity_usd']:>11,.0f} "
                        f"({pool_info['chain']}/{pool_info['dex']} | {pool_info['quote_symbol']})"
                    )
                    if save_callback and new_pools_count % 5 == 0:
                        save_callback(dex_map)
                else:
                    progress.console.print(
                        f"  [dim]- {sym:10} -> пул не найден (или < ${min_liq_usd:,.0f})[/dim]"
                    )

                progress.update(task, advance=1)
                time.sleep(rate_limit_delay)
    else:
        for sym, b_vol, by_vol, exchs in normalized_targets:
            pool_info = search_best_pool(
                sym,
                min_liq_usd=min_liq_usd,
                min_vol_24h_usd=min_vol_24h_usd,
                binance_vol=b_vol,
                bybit_vol=by_vol,
                exchanges=exchs,
            )
            if pool_info:
                key = f"{pool_info['chain']}:{pool_info['pair_address']}"
                dex_map[key] = pool_info
                new_pools_count += 1
                if save_callback and new_pools_count % 5 == 0:
                    save_callback(dex_map)
            time.sleep(rate_limit_delay)

    if save_callback and new_pools_count > 0:
        save_callback(dex_map)

    return dex_map


def fetch_batches(
    dex_map: Dict[str, Dict[str, Any]],
    batch_size: int = 30,
    batch_delay: float = 0.3,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    """
    Опрашивает пулы пачками до batch_size адресов на сеть за один HTTP-запрос.
    """
    by_chain: Dict[str, List[str]] = defaultdict(list)
    for info in dex_map.values():
        by_chain[info["chain"]].append(info["pair_address"])

    results: List[Dict[str, Any]] = []

    for chain, addresses in by_chain.items():
        for i in range(0, len(addresses), batch_size):
            chunk = ",".join(addresses[i : i + batch_size])
            url = f"{DEXSCREENER_PAIRS_URL}/{chain}/{chunk}"
            try:
                resp = requests.get(url, timeout=timeout)
                if resp.status_code == 200:
                    pairs = resp.json().get("pairs") or []
                    results.extend(pairs)
                else:
                    logger.warning(f"DexScreener batch {chain} вернул HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"Ошибка запроса батча {chain}: {e}")

            if batch_delay > 0:
                time.sleep(batch_delay)

    return results