"""
Модуль для захвата скриншотов 1H графиков с CoinGlass через Headless Chromium (Playwright).
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def capture_coinglass_chart(symbol: str, exchange: str = "binance", timeout_sec: int = 18) -> Optional[bytes]:
    """
    Открывает интерактивный график CoinGlass для указанной монеты на таймфрейме 1H
    и возвращает байты скриншота PNG.
    В случае ошибки или таймаута возвращает None (не блокируя отправку алерта).
    """
    if not symbol:
        return None

    sym = symbol.strip().upper()
    exch_capitalized = "Bybit" if (exchange and "bybit" in exchange.lower()) else "Binance"
    url = f"https://www.coinglass.com/tv/{exch_capitalized}_{sym}USDT"

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
            context = browser.new_context(
                viewport={"width": 1600, "height": 900},
                device_scale_factor=1,
            )
            page = context.new_page()

            # Загружаем DOM
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)

            # Ожидаем отрисовки холста графика внутри фрейма TradingView
            canvas_ready = False
            for _ in range(16):
                for frame in page.frames:
                    if frame != page.main_frame and frame.locator("canvas").count() > 0:
                        canvas_ready = True
                        break
                if canvas_ready:
                    break
                page.wait_for_timeout(500)

            # Дополнительная пауза 1 сек для завершения рендеринга свечей и индикаторов
            page.wait_for_timeout(1000)

            screenshot_bytes = page.screenshot(type="png")
            context.close()
            browser.close()
            return screenshot_bytes

    except Exception as e:
        logger.warning(f"Не удалось захватить скриншот CoinGlass для {symbol}: {e}")
        return None
