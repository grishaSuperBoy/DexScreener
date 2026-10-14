from pathlib import Path
from typing import Literal
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Binance Filters
    min_binance_vol_usd: float = 500_000.0  # От $500 тыс. оборота в сутки на Binance
    max_binance_vol_usd: float = 100_000_000.0  # До $100 млн оборота в сутки на Binance

    # Bybit Filters
    enable_bybit: bool = True
    min_bybit_vol_usd: float = 400_000.0  # От $400 тыс. оборота в сутки на Bybit
    max_bybit_vol_usd: float = 100_000_000.0  # До $100 млн оборота в сутки на Bybit

    # DexScreener Filters
    min_dex_liq_usd: float = 80_000.0  # Минимальный размер DEX-пула ($80k)
    min_dex_vol_24h_usd: float = 10_000.0  # Минимальный суточный объем на DEX

    # Monitor Intervals & Window
    check_interval_sec: int = 180  # Проверка каждые 3 минуты
    window_sec: int = 3600  # Скользящее окно памяти (1 час)
    signals_retention_sec: int = 3 * 86400  # Хранение сигналов: 3 суток (259200 сек)
    alert_cooldown_sec: int = 3600  # Кулдаун между алертами на один пул (1 час)

    # Пороги для ШОРТА (Вывод ликвидности)
    liq_drop_threshold_pct: float = -12.0  # Падение стейблов в пуле (-12% и сильнее)
    max_price_drop_pct: float = -2.5  # Нижний порог цены (еще не упала ниже -2.5%)
    max_price_pump_pct: float = 4.0  # Верхний порог цены (не улетела выше +4.0%)
    track_mode: Literal["quote", "usd", "both"] = "quote"

    # Пороги для ЛОНГА / НАКОПЛЕНИЯ (Памп-радар)
    enable_long_signals: bool = True
    lp_inject_quote_pct: float = 12.0  # Тихая заливка LP: стейблы выросли на +12%+
    lp_inject_base_pct: float = 8.0  # Тихая заливка LP: токены тоже выросли на +8%+
    stealth_buy_quote_pct: float = 8.0  # Скрытый выкуп: стейблы +8%+
    stealth_buy_base_drop_pct: float = -6.0  # Скрытый выкуп: токены выгребли из пула на -6%+
    stealth_buy_max_price_pct: float = 5.5  # Цена еще на старте (не выше +5.5%)
    stealth_buy_min_tx_ratio: float = 1.4  # Перевес покупок над продажами (buys / sells >= 1.4)
    stealth_buy_min_buys_h1: int = 25  # Минимум 25 покупок за час (живой пул)
    squeeze_funding_threshold_pct: float = -0.02  # Отрицательный фандинг (< -0.02%) = топливо для шорт-сквиза

    # Telegram
    tg_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("tg_bot_token", "TG_BOT_TOKEN", "telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
    )
    tg_chat_id: str = Field(
        default="",
        validation_alias=AliasChoices("tg_chat_id", "TG_CHAT_ID", "telegram_chat_id", "TELEGRAM_CHAT_ID"),
    )

    # MongoDB Storage
    mongo_db_url: str = Field(
        default="",
        validation_alias=AliasChoices("mongo_db_url", "MONGO_DB_URL"),
    )
    mongo_db_name: str = Field(
        default="dex_radar",
        validation_alias=AliasChoices("mongo_db_name", "MONGO_DB_NAME"),
    )
    mongo_collection: str = Field(
        default="signals",
        validation_alias=AliasChoices("mongo_collection", "MONGO_COLLECTION"),
    )

    # Dual Timeframe Windows
    window_1h_sec: int = 3600    # Окно 1 час (импульсный сдвиг)
    window_3h_sec: int = 10800   # Окно 3 часа (устойчивый тренд)
    max_history_window_sec: int = 10800  # Максимальная глубина хранения точек пула (3 часа)

    # File paths
    pools_map_file: Path = Path("data/binance_dex_map.json")
    state_file: Path = Path("data/pools_state.json")
    signals_file: Path = Path("data/signals_history.json")

    # Rate Limiting
    dexscreener_rate_limit_delay: float = 0.35
    dexscreener_batch_size: int = 30

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()