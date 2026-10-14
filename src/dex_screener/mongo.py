from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import pymongo
from pymongo.collection import Collection
from pymongo.database import Database

from dex_screener.config import settings

logger = logging.getLogger(__name__)

_mongo_client: Optional[pymongo.MongoClient] = None
_mongo_db: Optional[Database] = None
_signals_collection: Optional[Collection] = None


def get_mongo_collection() -> Optional[Collection]:
    """
    Лениво подключается к MongoDB и возвращает коллекцию для сохранения сигналов.
    При ошибке подключения логирует предупреждение и возвращает None (не роняя процесс).
    """
    global _mongo_client, _mongo_db, _signals_collection

    if _signals_collection is not None:
        return _signals_collection

    if not settings.mongo_db_url:
        return None

    try:
        _mongo_client = pymongo.MongoClient(
            settings.mongo_db_url,
            serverSelectionTimeoutMS=4000,
            connectTimeoutMS=4000,
        )
        db_name = settings.mongo_db_name or "dex_radar"
        _mongo_db = _mongo_client[db_name]
        _signals_collection = _mongo_db[settings.mongo_collection or "signals"]

        # Создаем индексы для быстрого последующего анализа и поиска
        _signals_collection.create_index([("timestamp_epoch", pymongo.DESCENDING)])
        _signals_collection.create_index([("symbol", pymongo.ASCENDING)])
        _signals_collection.create_index([("timeframe", pymongo.ASCENDING)])
        _signals_collection.create_index([("direction", pymongo.ASCENDING)])
        _signals_collection.create_index([("pattern", pymongo.ASCENDING)])

        logger.info(f"Успешное подключение к MongoDB (База: {db_name}, Коллекция: {settings.mongo_collection})")
        return _signals_collection
    except Exception as e:
        logger.warning(f"Не удалось подключиться к MongoDB: {e}")
        return None


def format_signal_for_mongo(sig: Dict[str, Any]) -> Dict[str, Any]:
    """
    Формирует полный, структурированный JSON-документ сигнала для глубокого квант-анализа.
    Включает таймфрейм окна (1h/3h), время, дельты резервов, TVL, объемы, фандинг и ссылки.
    """
    ts_epoch = float(sig.get("timestamp") or 0.0)
    created_dt = datetime.fromtimestamp(ts_epoch, tz=timezone.utc) if ts_epoch > 0 else datetime.now(timezone.utc)

    timeframe = sig.get("timeframe", "1h")
    timeframe_sec = int(sig.get("timeframe_sec") or (3600 if timeframe == "1h" else 10800))
    direction = sig.get("direction", "LONG").upper()
    pattern = sig.get("pattern", "UNKNOWN")

    pattern_titles = {
        "STEALTH_ACCUMULATION": "СКРЫТЫЙ ВЫКУП (Пылесос токенов)",
        "SILENT_LP_ADD": "ТИХАЯ ЗАЛИВКА LP (Инъекция ликвидности)",
        "LIQ_DRAIN": "СЛИВ ЛИКВИДНОСТИ (Вывод стейблов)",
    }
    pattern_title = pattern_titles.get(pattern, pattern)

    symbol = sig.get("symbol", "UNKNOWN")
    base_usdt = f"{symbol}USDT"

    doc = {
        "signal_id": sig.get("id"),
        "created_at": created_dt.isoformat(),
        "created_at_dt": created_dt,
        "timestamp_epoch": ts_epoch,
        "timeframe": timeframe,
        "timeframe_sec": timeframe_sec,
        "direction": direction,
        "pattern": pattern,
        "pattern_title": pattern_title,
        "symbol": symbol,
        "exchanges": sig.get("exchanges", ["binance"]),
        "chain": sig.get("chain", ""),
        "dex": sig.get("dex_id", ""),
        "pair_address": sig.get("pair_address", ""),
        "quote_symbol": sig.get("quote_sym", ""),
        "urls": {
            "dexscreener": sig.get("dex_url", ""),
            "binance_futures": f"https://www.binance.com/en/futures/{base_usdt}" if "binance" in sig.get("exchanges", []) else "",
            "bybit_linear": f"https://www.bybit.com/trade/usdt/{base_usdt}" if "bybit" in sig.get("exchanges", []) else "",
        },
        "metrics": {
            "price": {
                "current_usd": sig.get("price_usd"),
                "change_pct": sig.get("price_chg"),
            },
            "quote_reserve": {
                "symbol": sig.get("quote_sym"),
                "current": sig.get("curr_quote_res"),
                "reference_in_window": sig.get("prev_quote_res"),
                "change_pct": sig.get("quote_chg"),
            },
            "base_reserve": {
                "symbol": symbol,
                "change_pct": sig.get("base_chg"),
            },
            "tvl_usd": {
                "current": sig.get("curr_tvl_usd"),
                "reference_in_window": sig.get("prev_tvl_usd"),
                "change_pct": sig.get("usd_chg") if sig.get("usd_chg") is not None else (
                    round(((float(sig.get("curr_tvl_usd") or 0) - float(sig.get("prev_tvl_usd") or 0)) / float(sig.get("prev_tvl_usd") or 1)) * 100.0, 2)
                    if (sig.get("prev_tvl_usd") or 0) > 0 else 0.0
                ),
            },
            "activity_1h": {
                "buys": sig.get("buys_h1"),
                "sells": sig.get("sells_h1"),
                "tx_ratio": round(sig.get("buys_h1", 0) / sig.get("sells_h1", 1), 2) if (sig.get("sells_h1") or 0) > 0 else float(sig.get("buys_h1") or 0),
                "volume_usd": sig.get("vol_h1_usd"),
            },
            "funding": {
                "primary_rate_pct": sig.get("funding_rate"),
                "binance_rate_pct": sig.get("binance_funding"),
                "bybit_rate_pct": sig.get("bybit_funding"),
                "is_squeeze_fuel": bool(sig.get("is_squeeze_fuel")),
            },
        },
        "raw_snapshot": sig.get("raw_snapshot") or {},
    }

    return doc


def save_signal_to_mongo(sig: Dict[str, Any]) -> bool:
    """
    Безопасно сохраняет структурированный документ сигнала в MongoDB.
    Возвращает True в случае успеха, False при ошибке (без выброса исключений наружу).
    """
    col = get_mongo_collection()
    if col is None:
        return False

    try:
        doc = format_signal_for_mongo(sig)
        col.insert_one(doc)
        logger.info(f"[MongoDB] Успешно сохранен {doc['direction']} сигнал {doc['symbol']} ({doc['timeframe']})")
        return True
    except Exception as e:
        logger.error(f"[MongoDB] Ошибка сохранения сигнала: {e}")
        return False


def get_recent_mongo_signals(limit: int = 50) -> List[Dict[str, Any]]:
    """Возвращает последние сигналы из MongoDB для проверки или аналитики."""
    col = get_mongo_collection()
    if col is None:
        return []
    try:
        cursor = col.find({}, {"_id": 0}).sort("timestamp_epoch", pymongo.DESCENDING).limit(limit)
        return list(cursor)
    except Exception as e:
        logger.error(f"[MongoDB] Ошибка чтения сигналов: {e}")
        return []
