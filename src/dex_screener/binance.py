import logging
import re
from typing import Dict, List, Tuple
import requests

logger = logging.getLogger(__name__)

BINANCE_FUTURES_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BINANCE_EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_PREMIUM_INDEX_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

# Пары стейблкоинов или фиата, которые не имеет смысла искать на DEX
EXCLUDED_SYMBOLS = {
    "USDC",
    "FDUSD",
    "TUSD",
    "BUSD",
    "EUR",
    "DAI",
    "USDD",
    "USTC",
}

# Префиксы-множители Binance Futures: 1000PEPE, 1000000MOG, 1MBABYDOGE
MULTIPLIER_PREFIX_REGEX = re.compile(r"^(1000000|100000|10000|1000|1M)", re.IGNORECASE)


def clean_symbol(binance_symbol: str) -> str:
    """
    Очищает символ Binance Futures до чистого тикера токена.
    Примеры:
        1000PEPEUSDT -> PEPE
        1MBABYDOGEUSDT -> BABYDOGE
        1000000MOGUSDT -> MOG
        BTCUSDT -> BTC
        1INCHUSDT -> 1INCH (не затрагивается)
    """
    if not binance_symbol.endswith("USDT"):
        return binance_symbol

    base = binance_symbol[:-4]
    cleaned = MULTIPLIER_PREFIX_REGEX.sub("", base)
    return cleaned.upper()


def get_binance_all_crypto_symbols(timeout: int = 10) -> set:
    """
    Получает множество ВСЕХ очищенных крипто-тикеров, торгуемых на Binance Futures.
    Исключает TradFi акции/сырьё/форекс/индексы (TRADIFI_PERPETUAL, underlyingType != 'COIN').
    """
    try:
        resp = requests.get(BINANCE_EXCHANGE_INFO_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        crypto_syms = set()
        for item in data.get("symbols", []):
            if (
                item.get("status") == "TRADING"
                and item.get("contractType") == "PERPETUAL"
                and item.get("underlyingType") == "COIN"
            ):
                raw = item.get("symbol", "")
                if raw.endswith("USDT") and "_" not in raw:
                    c = clean_symbol(raw)
                    if c and c not in EXCLUDED_SYMBOLS:
                        crypto_syms.add(c)
        return crypto_syms
    except Exception as e:
        logger.warning(f"Не удалось получить exchangeInfo Binance: {e}")
        return set()


def get_binance_target_symbols(
    min_vol_usd: float = 1_000_000.0,
    max_vol_usd: float = 50_000_000.0,
    timeout: int = 10,
) -> List[Tuple[str, float]]:
    """
    Получает список USDT-фьючерсов с суточным оборотом в диапазоне [min_vol_usd, max_vol_usd].
    Строго отфильтровывает TradFi акции, индексы и сырьё (оставляет только реальную крипту).
    Возвращает список кортежей: (очищенный_символ, суточный_объем_usd), отсортированный по объему.
    """
    valid_crypto = get_binance_all_crypto_symbols(timeout=timeout)

    try:
        resp = requests.get(BINANCE_FUTURES_TICKER_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Ошибка получения тикеров Binance Futures: {e}")
        return []

    target_symbols: Dict[str, float] = {}

    for item in data:
        raw_symbol = item.get("symbol", "")
        if not raw_symbol.endswith("USDT") or "_" in raw_symbol:
            continue

        cleaned = clean_symbol(raw_symbol)
        if not cleaned or cleaned in EXCLUDED_SYMBOLS:
            continue

        # Строгий фильтр: токен должен быть реальной криптовалютой по exchangeInfo
        if valid_crypto and cleaned not in valid_crypto:
            continue

        try:
            vol_usd = float(item.get("quoteVolume") or 0.0)
        except (ValueError, TypeError):
            continue

        if min_vol_usd <= vol_usd <= max_vol_usd:
            target_symbols[cleaned] = max(target_symbols.get(cleaned, 0.0), vol_usd)

    sorted_list = sorted(target_symbols.items(), key=lambda x: x[1], reverse=True)
    logger.info(
        f"Найдено {len(sorted_list)} подходящих крипто-токенов на Binance Futures "
        f"с объемом ${min_vol_usd:,.0f} - ${max_vol_usd:,.0f}"
    )
    return sorted_list


def get_binance_funding_rates(timeout: int = 10) -> Dict[str, float]:
    """
    За 1 запрос получает текущий Funding Rate (в процентах, например -0.05 означает -0.05%)
    по всем USDT-фьючерсам Binance.
    Возвращает словарь {CLEAN_SYMBOL: funding_rate_pct}.
    """
    try:
        resp = requests.get(BINANCE_PREMIUM_INDEX_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning(f"Не удалось получить фандинг с Binance: {e}")
        return {}

    funding_map: Dict[str, float] = {}
    for item in data:
        raw_symbol = item.get("symbol", "")
        if not raw_symbol.endswith("USDT") or "_" in raw_symbol:
            continue
        try:
            # lastFundingRate возвращается в долях (0.0001 = 0.01%), умножаем на 100
            rate_pct = float(item.get("lastFundingRate") or 0.0) * 100.0
            cleaned = clean_symbol(raw_symbol)
            if cleaned:
                funding_map[cleaned] = rate_pct
        except (ValueError, TypeError):
            continue

    return funding_map