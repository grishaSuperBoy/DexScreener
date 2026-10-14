import html
import logging
from typing import Any, Dict
import requests
from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    timeout: int = 10,
) -> bool:
    """Отправляет сообщение в Telegram с обработкой ошибок."""
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"Telegram API вернул ошибку {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление в Telegram: {e}")
        return False


def format_signal_html(sig: Dict[str, Any]) -> str:
    """Формирует профессиональное красивое HTML-сообщение для Telegram."""
    direction = sig.get("direction", "SHORT")
    pattern = sig.get("pattern", "")
    raw_sym = str(sig.get("symbol", "")).upper()
    esc_symbol = html.escape(raw_sym)
    esc_chain = html.escape(str(sig.get("chain", "")).upper())
    esc_dex = html.escape(str(sig.get("dex_id") or "DEX").upper())
    esc_quote = html.escape(str(sig.get("quote_sym") or "USDT").upper())
    esc_url = html.escape(str(sig.get("dex_url") or ""))

    quote_chg = float(sig.get("quote_chg", 0.0))
    base_chg = float(sig.get("base_chg", 0.0))
    prev_q = float(sig.get("prev_quote_res", 0.0))
    curr_q = float(sig.get("curr_quote_res", 0.0))
    prev_tvl = float(sig.get("prev_tvl_usd", 0.0))
    curr_tvl = float(sig.get("curr_tvl_usd", 0.0))
    price_chg = float(sig.get("price_chg", 0.0))
    price_usd = float(sig.get("price_usd", 0.0))
    
    funding_rate = sig.get("funding_rate")
    binance_fr = sig.get("binance_funding")
    bybit_fr = sig.get("bybit_funding")
    exchanges = sig.get("exchanges") or ["binance"]

    buys_h1 = int(sig.get("buys_h1", 0))
    sells_h1 = int(sig.get("sells_h1", 0))
    is_squeeze_fuel = bool(sig.get("is_squeeze_fuel", False))
    vol_h1 = float(sig.get("vol_h1_usd", 0.0))

    # Формирование строки фандинга
    funding_parts = []
    if binance_fr is not None:
        funding_parts.append(f"Binance: <code>{binance_fr:+.4f}%</code>")
    if bybit_fr is not None:
        funding_parts.append(f"Bybit: <code>{bybit_fr:+.4f}%</code>")
    
    if funding_parts:
        funding_display = " • ".join(funding_parts)
    elif funding_rate is not None:
        funding_display = f"<code>{funding_rate:+.4f}%</code>"
    else:
        funding_display = "<code>N/A</code>"

    # Ссылки на биржи и DEX
    links = []
    if esc_url:
        links.append(f'<a href="{esc_url}"><b>📈 DexScreener</b></a>')
    if "binance" in exchanges:
        links.append(f'<a href="https://www.binance.com/en/futures/{esc_symbol}USDT"><b>🟡 Binance Futures</b></a>')
    if "bybit" in exchanges:
        links.append(f'<a href="https://www.bybit.com/trade/usdt/{esc_symbol}USDT"><b>🟠 Bybit Futures</b></a>')
    links_str = " | ".join(links)

    # Биржевые теги
    exch_badges = []
    if "binance" in exchanges:
        exch_badges.append("🟡 Binance")
    if "bybit" in exchanges:
        exch_badges.append("🟠 Bybit")
    exch_str = " + ".join(exch_badges) if exch_badges else "🟡 Binance"

    timeframe = sig.get("timeframe", "1h")
    if timeframe == "3h":
        tf_badge = "⏳ <b>3 ЧАСА (Устойчивый тренд)</b>"
        tf_label = "3ч"
    else:
        tf_badge = "⚡️ <b>1 ЧАС (Импульсный сдвиг)</b>"
        tf_label = "1ч"

    tx_ratio = (buys_h1 / sells_h1) if sells_h1 > 0 else float(buys_h1)

    coherence = float(sig.get("coherence_ratio", 1.0))
    real_drain = float(sig.get("real_drain_usd", 0.0))
    impact_pct = float(sig.get("impact_ratio_pct", 0.0))
    total_txns = int(sig.get("total_txns_h1", buys_h1 + sells_h1))

    z_quote = sig.get("z_quote")
    z_base = sig.get("z_base")
    z_price = sig.get("z_price")
    z_div = sig.get("z_divergence")

    if direction == "SHORT":
        z_block = ""
        if z_quote is not None and z_price is not None:
            z_block = (
                f"📐 <b>Статистический Z-Score:</b>\n"
                f"• Аномалия резервов: <b>{z_quote:+.2f}σ</b> (стат. выброс)\n"
                f"• Лаг спот-цены: <b>{z_price:+.2f}σ</b>\n"
                f"• Z-Дивергенция: <b>+{z_div:.2f}σ</b> (опережение цены)\n\n"
            )

        return (
            f"🚨 <b>DEX RADAR | SHORT ALERT ({tf_label})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>${esc_symbol} / {esc_quote}</b> ({exch_str})\n"
            f"⏱ Таймфрейм: {tf_badge}\n"
            f"🌐 Сеть: <code>{esc_chain}</code> • DEX: <b>{esc_dex}</b>\n"
            f"💵 Спот DEX: <code>${price_usd:.6g}</code> (<b>{price_chg:+.2f}%</b> за {tf_label})\n\n"
            f"💧 <b>Слив ликвидности из пула ({tf_label}):</b>\n"
            f"• Резерв {esc_quote}: <b>{quote_chg:+.1f}%</b> ({prev_q:,.0f} → {curr_q:,.0f})\n"
            f"• TVL пула: <b>${prev_tvl:,.0f} ➔ ${curr_tvl:,.0f}</b>\n"
            f"• Токены в пуле: <b>{base_chg:+.1f}%</b>\n\n"
            f"{z_block}"
            f"🛡️ <b>Качество ликвидности & Импакт:</b>\n"
            f"• Реальный отток: <b>${real_drain:,.0f}</b> (<b>{impact_pct:.1f}%</b> от оборота за {tf_label})\n"
            f"• Синхронность TVL: <b>{coherence:.2f}</b> (когерентность оттока)\n"
            f"• Активность DEX (1ч): <b>{total_txns}</b> сделок (🛒 {buys_h1} / 🏷️ {sells_h1})\n\n"
            f"📊 <b>Деривативы:</b>\n"
            f"• Фандинг: {funding_display}\n\n"
            f"⚠️ <i>Стейблы выведены из пула за {tf_label}, цена на фьючерсах ещё держится. Высокая вероятность дампа.</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 {links_str}"
        )
    else:
        if pattern == "SILENT_LP_ADD":
            pattern_title = "ТИХАЯ ЗАЛИВКА LP (Инъекция ликвидности)"
            strategy_tip = "Маркетмейкер расширил глубину пула без пампа цены. Подготовка к разгону."
        else:
            pattern_title = "СКРЫТЫЙ ВЫКУП (Пылесос токенов)"
            strategy_tip = f"Базовые токены выкупаются из пула за {tf_label}. Перевес покупателей x{tx_ratio:.1f}."

        z_block = ""
        if z_quote is not None:
            base_line = f"• Токены в пуле: <b>{z_base:+.2f}σ</b> (выгребание)\n" if z_base is not None else ""
            price_line = f"• Спот-цена: <b>{z_price:+.2f}σ</b> (ранняя точка)\n" if z_price is not None else ""
            z_block = (
                f"📐 <b>Статистический Z-Score:</b>\n"
                f"• Приток стейблов: <b>{z_quote:+.2f}σ</b>\n"
                f"{base_line}"
                f"{price_line}\n"
            )

        squeeze_block = ""
        if is_squeeze_fuel:
            best_squeeze_fr = funding_rate if funding_rate is not None else (binance_fr if binance_fr is not None else bybit_fr)
            best_fr_str = f"{best_squeeze_fr:+.4f}%" if best_squeeze_fr is not None else ""
            target_exchs = " / ".join(e.capitalize() for e in exchanges) if exchanges else "CEX"
            squeeze_block = (
                f"\n🔥 <b>ШОРТ-СКВИЗ ТОПЛИВО:</b> <code>{best_fr_str}</code>\n"
                f"<i>Толпа агрессивно шортит на {target_exchs} — идеальный сетап для выноса!</i>\n"
            )

        return (
            f"🟢 <b>DEX RADAR | PUMP & ACCUMULATION ({tf_label})</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>${esc_symbol} / {esc_quote}</b> ({exch_str})\n"
            f"⏱ Таймфрейм: {tf_badge}\n"
            f"📌 Паттерн: <b>{pattern_title}</b>\n"
            f"🌐 Сеть: <code>{esc_chain}</code> • DEX: <b>{esc_dex}</b>\n"
            f"💵 Спот DEX: <code>${price_usd:.6g}</code> (<b>{price_chg:+.2f}%</b> за {tf_label})\n\n"
            f"💧 <b>Динамика пула ({tf_label}):</b>\n"
            f"• Приток стейблов: <b>{quote_chg:+.1f}%</b> ({prev_q:,.0f} → {curr_q:,.0f})\n"
            f"• Токены в пуле: <b>{base_chg:+.1f}%</b>\n"
            f"• TVL пула: <b>${prev_tvl:,.0f} ➔ ${curr_tvl:,.0f}</b>\n\n"
            f"{z_block}"
            f"⚡ <b>Активность & Фандинг:</b>\n"
            f"• Сделки (1ч): 🛒 <b>{buys_h1}</b> пок. / 🏷️ <b>{sells_h1}</b> прод. (x{tx_ratio:.1f})\n"
            f"• Объём (1ч): <b>${vol_h1:,.0f}</b>\n"
            f"• Фандинг: {funding_display}"
            f"{squeeze_block}\n"
            f"💡 <i>{strategy_tip}</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔗 {links_str}"
        )


def notify_signal(bot_token: str, chat_id: str, sig: Dict[str, Any]):
    """Выводит алерт в консоль, отправляет в Telegram и сохраняет в MongoDB."""
    direction = sig.get("direction", "SHORT")
    sym = sig.get("symbol", "UNKNOWN")
    tf = sig.get("timeframe", "1h")
    chain = sig.get("chain", "")
    dex_id = sig.get("dex_id", "")
    quote_sym = sig.get("quote_sym", "")
    quote_chg = float(sig.get("quote_chg", 0.0))
    base_chg = float(sig.get("base_chg", 0.0))
    prev_q = float(sig.get("prev_quote_res", 0.0))
    curr_q = float(sig.get("curr_quote_res", 0.0))
    prev_tvl = float(sig.get("prev_tvl_usd", 0.0))
    curr_tvl = float(sig.get("curr_tvl_usd", 0.0))
    price_chg = float(sig.get("price_chg", 0.0))
    funding = sig.get("funding_rate")
    binance_fr = sig.get("binance_funding")
    bybit_fr = sig.get("bybit_funding")
    exchanges = sig.get("exchanges") or ["binance"]
    is_squeeze = bool(sig.get("is_squeeze_fuel", False))

    f_parts = []
    if binance_fr is not None:
        f_parts.append(f"Binance: {binance_fr:+.4f}%")
    if bybit_fr is not None:
        f_parts.append(f"Bybit: {bybit_fr:+.4f}%")
    funding_str = " | ".join(f_parts) if f_parts else (f"{funding:+.4f}%" if funding is not None else "н/д")

    exch_str = "+".join(e.capitalize() for e in exchanges)

    if direction == "SHORT":
        title = f"🔥 SHORT SIGNAL ({tf}): ВЫВОД ЛИКВИДНОСТИ [{exch_str}] 🔥"
        border = "red"
        header = f"[bold red]🚨 СБРОС ЛИКВИДНОСТИ ({tf}): {sym} ({exch_str})[/bold red]"
    else:
        title = f"🚀 LONG SIGNAL ({tf}): НАКОПЛЕНИЕ / ПАМП [{exch_str}] 🚀"
        border = "green"
        pattern_str = sig.get("pattern", "")
        if is_squeeze:
            pattern_str += " | 🔥 SQUEEZE FUEL"
        header = f"[bold green]🟢 НАКОПЛЕНИЕ ({pattern_str}) [{tf}]: {sym} ({exch_str})[/bold green]"

    console_text = (
        f"{header}\n"
        f"Таймфрейм: [bold magenta]{tf}[/bold magenta] | Пул: [cyan]{chain}/{dex_id}[/cyan]\n"
        f"Резерв {quote_sym}: [bold yellow]{quote_chg:+.1f}%[/bold yellow] ({prev_q:,.1f} -> {curr_q:,.1f}) | "
        f"Токены в пуле: {base_chg:+.1f}%\n"
        f"TVL пула: ${prev_tvl:,.0f} -> ${curr_tvl:,.0f}\n"
        f"Изменение цены: [bold]{price_chg:+.2f}%[/bold] | Фандинг: [bold cyan]{funding_str}[/bold cyan]\n"
        f"DexScreener: {sig.get('dex_url')}"
    )
    console.print(Panel(console_text, title=title, border_style=border))

    # 1. Отправляем в Telegram
    if bot_token and chat_id:
        tg_html = format_signal_html(sig)
        send_telegram_message(bot_token, chat_id, tg_html)

    # 2. Сохраняем в MongoDB (для квант-анализа и бэктестинга)
    try:
        from dex_screener.mongo import save_signal_to_mongo
        save_signal_to_mongo(sig)
    except Exception as e:
        logger.warning(f"Ошибка вызова сохранения в MongoDB: {e}")