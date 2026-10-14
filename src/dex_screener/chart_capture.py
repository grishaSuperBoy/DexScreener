"""
Модуль для захвата чистого графика цены (только свечи + объемы) через TradingView Embed.
Полностью удалены сторонние панели, индикаторы, подвалы, боковые меню и спиннеры.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Маппинг тикеров для деривативов Binance/Bybit, где используется префикс 1000/1000000
MEME_FUTURES_PREFIX_MAP = {
    "PEPE": "1000PEPE",
    "SHIB": "1000SHIB",
    "BONK": "1000BONK",
    "FLOKI": "1000FLOKI",
    "CHEEMS": "1000CHEEMS",
    "SATS": "1000SATS",
    "RATS": "1000RATS",
    "CAT": "1000CAT",
    "WHY": "1000WHY",
    "MOG": "1000000MOG",
    "LUNC": "1000LUNC",
    "XEC": "1000XEC",
    "NEIROCTO": "NEIRO",
}


def normalize_chart_symbol(symbol: str) -> str:
    """Приводит тикер к формату CEX Futures (1000PEPE, 1000BONK и т.д.)."""
    s = symbol.strip().upper()
    return MEME_FUTURES_PREFIX_MAP.get(s, s)


def capture_price_chart(
    symbol: str,
    exchange: str = "binance",
    timeframe: str = "1h",
    timeout_sec: int = 15,
) -> Optional[bytes]:
    """
    Открывает полноэкранный чистый график цены (Candlestick + Volume) для указанной монеты
    без боковых меню, без лишних индикаторов и панелей. Возвращает байты PNG.
    В случае ошибки или таймаута возвращает None, не блокируя отправку алерта.
    """
    if not symbol:
        return None

    raw_sym = symbol.strip().upper()
    mapped_sym = normalize_chart_symbol(raw_sym)

    primary_exch = "BYBIT" if (exchange and "bybit" in exchange.lower() and "binance" not in exchange.lower()) else "BINANCE"
    interval = "180" if timeframe == "3h" else "60"

    candidates = [
        f"{primary_exch}:{mapped_sym}USDT.P",
        f"{primary_exch}:{mapped_sym}USDT",
        f"BINANCE:{mapped_sym}USDT.P",
        f"BINANCE:{mapped_sym}USDT",
        f"BYBIT:{mapped_sym}USDT.P",
        f"BYBIT:{mapped_sym}USDT",
    ]

    seen = set()
    unique_candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            page = browser.new_page(
                viewport={"width": 1280, "height": 720},
                device_scale_factor=1,
            )

            for cand in unique_candidates:
                url = (
                    f"https://s.tradingview.com/widgetembed/"
                    f"?symbol={cand}&interval={interval}&theme=dark&style=1"
                    f"&hide_top_toolbar=1&hide_side_toolbar=1&symboledit=0&save_image=0&timezone=Etc%2FUTC"
                )
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)

                    # Ожидаем холста TradingView и проверяем отсутствие ошибки "This symbol doesn't exist"
                    canvas_ready = False
                    for _ in range(10):
                        page.wait_for_timeout(400)
                        if page.locator("text='This symbol doesn't exist'").count() > 0:
                            break
                        if page.locator("canvas").count() >= 5:
                            canvas_ready = True
                            break

                    if canvas_ready:
                        # 1.5 сек пауза для окончательной прорисовки тел свечей
                        page.wait_for_timeout(1500)
                        screenshot_bytes = page.screenshot(type="png")
                        page.close()
                        browser.close()
                        return screenshot_bytes

                except Exception as ex:
                    logger.debug(f"Кандидат {cand} не загрузился: {ex}")
                    continue

            page.close()
            browser.close()
            return None

    except Exception as e:
        logger.warning(f"Не удалось захватить график цены для {symbol}: {e}")
        return None


# Алиас для обратной совместимости
capture_coinglass_chart = capture_price_chart
