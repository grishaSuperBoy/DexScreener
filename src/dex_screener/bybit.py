import logging
import re
from typing import Dict, List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

BYBIT_TICKERS_URL = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"

# Исключаем традиционные акции, индексы и сырьё, которые Bybit торгует в виде фьючерсов
EXCLUDED_BYBIT_SYMBOLS = {
    "AAPL", "AMD", "AMZN", "ARM", "ASML", "AVGO", "COIN", "CRWD", "DELL", "GOOGL",
    "INTC", "META", "MSFT", "NVDA", "ORCL", "PLTR", "QCOM", "SMCI", "TSLA", "TSM",
    "SPY", "QQQ", "IWM", "GLD", "SLV", "USO", "JNJ", "FUTU", "ARKK", "ISRG", "TSEM",
    "GIGADEVICE", "LAOPU", "SOXX", "APP", "CRM", "CIEN", "TER",
    "VRT", "ADBE", "TMF", "STRC", "TENCENT", "HYUNDAI", "ONDS", "WMT",
    "VST", "CRML", "NXPC", "FWDI", "NAORIS", "TAIKO", "ANIME", "EWT", "TXN", "LISTA",
    "CSCO", "SHELL", "KO", "SPORTFUN", "POW", "OKLO", "CATI", "MDB", "SNPS", "BRKB",
    "FHE", "AGLD", "KUAISHOU", "GLM", "MTL", "LAYER", "HOT", "TEM", "AUCTION",
    "KERNEL", "URNM", "HK0992", "UBER", "ASTR", "HMSTR", "PDD", "TREE", "PHAROS",
    "AGPU", "BX", "BEL", "RPL", "EWJ", "MAV", "GEV", "GTLB", "USDEX", "FLEX", "BBX",
    "SMH", "DRIFT", "BAND", "KSM", "BROCCOLIF3B", "UNH", "BWET", "HK0625", "FOGO",
    "SOLV", "SAMSUNGEM", "LGELECTRONICS", "EBAY", "SANTOS", "SLP", "DKNG", "TWST",
    "MSTU", "WAXP", "WEN", "PUNDIX", "OG", "EWZ", "WET", "SONIC", "HANMI",
    "MRVL", "MUU", "LITE", "NBIS", "CBRS", "MVLL", "AAOI", "WDC", "TQQQ", "AXTI",
    "ZHIPU", "COPPER", "INTW", "MINIMAX", "RAYSOL", "HK1810", "COHR", "SKDD",
    "CSOPSKHYNIX2L", "ZHONGJI", "XPT", "CXMT", "STXX", "BMNR", "GLW", "UNITREE",
    "ASTS", "AMAT", "CRDO", "DODOX", "MAGMA", "DATAIP", "FLNC", "SKUU", "XPD",
    "JPM", "ALAB", "HPE", "GUA", "POWER", "AVAAI", "CLO", "GIGADEV", "QNTX", "HK0700",
    "KSTR", "CSOPSAMSUNG2L", "KLAC", "GS", "BTCDOM", "UVXY", "LRCX", "SHAZ", "LUNA2",
    "SATS",
}


def clean_bybit_symbol(raw_symbol: str) -> str:
    """
    Очищает тикер Bybit: удаляет суффикс USDT и префиксы/суффиксы 1000/10000/1000000.
    1000PEPEUSDT -> PEPE
    SHIB1000USDT -> SHIB
    1000000BABYDOGEUSDT -> BABYDOGE
    """
    if not raw_symbol.endswith("USDT"):
        return ""
    base = raw_symbol[:-4]
    # Удаляем множители в начале и конце
    base_clean = re.sub(r"^(1000000|100000|10000|1000|1M)+", "", base, flags=re.IGNORECASE)
    base_clean = re.sub(r"(1000000|100000|10000|1000|1M)$", "", base_clean, flags=re.IGNORECASE)
    # Удаляем суффиксы типа STOCK
    base_clean = re.sub(r"STOCK$", "", base_clean, flags=re.IGNORECASE)
    return base_clean.upper()


def get_bybit_instruments_map(timeout: int = 10) -> Dict[str, dict]:
    """Запрашивает метаданные всех инструментов Bybit с постраничной пагинацией (крипта vs акции/сырье)."""
    inst_map = {}
    cursor = ""
    try:
        while True:
            url = "https://api.bybit.com/v5/market/instruments-info?category=linear&limit=500"
            if cursor:
                url += f"&cursor={cursor}"
            resp = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("result", {}).get("list", [])
            for it in items:
                sym = it.get("symbol", "")
                if sym:
                    inst_map[sym] = it
            cursor = data.get("result", {}).get("nextPageCursor")
            if not cursor:
                break
        return inst_map
    except Exception as e:
        logger.warning(f"Не удалось получить instruments-info Bybit: {e}")
        return inst_map


def get_bybit_target_symbols(
    min_vol_usd: float = 400_000.0,
    max_vol_usd: float = 100_000_000.0,
    exclude_symbols: Optional[set] = None,
    timeout: int = 10,
) -> List[Tuple[str, float]]:
    """
    Получает список USDT-фьючерсов Bybit, отфильтрованный строго под крипто-токены,
    которых НЕТ на Binance (если передан exclude_symbols).
    Возвращает список кортежей: (очищенный_тикер, суточный_оборот_usd).
    """
    inst_map = get_bybit_instruments_map(timeout=timeout)

    try:
        resp = requests.get(
            BYBIT_TICKERS_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("result", {}).get("list", [])
    except Exception as e:
        logger.error(f"Ошибка получения тикеров Bybit: {e}")
        return []

    target_map: Dict[str, float] = {}
    excluded_set = set(exclude_symbols or [])

    for item in items:
        raw_symbol = item.get("symbol", "")
        if not raw_symbol.endswith("USDT"):
            continue

        # Фильтр по метаданным инструмента (исключаем акции, сырьё, форекс, ETF и регионы акций)
        inst = inst_map.get(raw_symbol)
        if not inst:
            continue

        stype = inst.get("symbolType", "")
        region = inst.get("marketRegion", "")
        # Только криптотокены ('' или 'innovation') без региональных фондовых привязок
        if stype not in ("", "innovation") or region != "":
            continue

        # Пропускаем токенизированные акции и фонды по префиксам
        if any(raw_symbol.startswith(prefix) for prefix in ("HK", "CSOP", "CSOPS")):
            continue

        try:
            turnover = float(item.get("turnover24h") or 0.0)
        except (ValueError, TypeError):
            continue

        if min_vol_usd <= turnover <= max_vol_usd:
            cleaned = clean_bybit_symbol(raw_symbol)
            if not cleaned or len(cleaned) < 2 or re.match(r"^\d+$", cleaned):
                continue

            if cleaned in EXCLUDED_BYBIT_SYMBOLS:
                continue

            # СТРОГИЙ ФИЛЬТР: ТОЛЬКО ТОКЕНЫ, КОТОРЫХ НЕТ НА BINANCE
            if cleaned in excluded_set:
                continue

            target_map[cleaned] = max(target_map.get(cleaned, 0.0), turnover)

    sorted_targets = sorted(target_map.items(), key=lambda x: x[1], reverse=True)
    logger.info(
        f"Найдено {len(sorted_targets)} уникальных эксклюзивных крипто-токенов на Bybit (отсутствующих на Binance) "
        f"с оборотом ${min_vol_usd:,.0f} - ${max_vol_usd:,.0f}"
    )
    return sorted_targets



def get_bybit_funding_rates(timeout: int = 10) -> Dict[str, float]:
    """
    За 1 запрос получает текущий Funding Rate (в процентах, например -0.015 означает -0.015%)
    по всем линейным контрактам Bybit.
    Возвращает словарь {CLEAN_SYMBOL: funding_rate_pct}.
    """
    try:
        resp = requests.get(
            BYBIT_TICKERS_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("result", {}).get("list", [])
    except Exception as e:
        logger.warning(f"Не удалось получить фандинг с Bybit: {e}")
        return {}

    funding_map: Dict[str, float] = {}
    for item in items:
        raw_symbol = item.get("symbol", "")
        if not raw_symbol.endswith("USDT"):
            continue

        fr_str = item.get("fundingRate")
        if not fr_str:
            continue

        try:
            # fundingRate на Bybit отдается в долях (0.0001 = 0.01%), умножаем на 100
            rate_pct = float(fr_str) * 100.0
            cleaned = clean_bybit_symbol(raw_symbol)
            if cleaned:
                funding_map[cleaned] = rate_pct
        except (ValueError, TypeError):
            continue

    return funding_map
