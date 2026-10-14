import argparse
from datetime import datetime
import logging
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table

from dex_screener.binance import (
    get_binance_all_crypto_symbols,
    get_binance_funding_rates,
    get_binance_target_symbols,
)
from dex_screener.bybit import get_bybit_funding_rates, get_bybit_target_symbols
from dex_screener.config import settings
from dex_screener.dexscreener import build_dex_map, fetch_batches
from dex_screener.monitor import PoolMonitor
from dex_screener.notifier import notify_signal, send_telegram_message
from dex_screener.storage import append_and_prune_signals, safe_load_json, safe_save_json

console = Console()


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def fetch_all_funding_rates() -> dict:
    """Собирает ставки фандинга с Binance и Bybit (если включен)."""
    binance_fr = get_binance_funding_rates()
    if not settings.enable_bybit:
        return binance_fr

    bybit_fr = get_bybit_funding_rates()
    all_symbols = set(binance_fr.keys()) | set(bybit_fr.keys())
    combined = {}
    for sym in all_symbols:
        b_rate = binance_fr.get(sym)
        by_rate = bybit_fr.get(sym)

        rates = [r for r in (b_rate, by_rate) if r is not None]
        min_r = min(rates) if rates else None
        combined[sym] = {
            "rate": min_r,
            "binance": b_rate,
            "bybit": by_rate,
        }
    return combined


def cmd_build_map(args):
    """Сканирует фьючерсы Binance и Bybit, строит и дополняет карту DEX-пулов."""
    binance_all_crypto = get_binance_all_crypto_symbols()
    console.print(
        f"[bold cyan][*] Получение фьючерсов Binance "
        f"(${settings.min_binance_vol_usd:,.0f} - ${settings.max_binance_vol_usd:,.0f})...[/bold cyan]"
    )
    binance_targets = get_binance_target_symbols(
        min_vol_usd=settings.min_binance_vol_usd,
        max_vol_usd=settings.max_binance_vol_usd,
    )
    console.print(f"[green][+] Binance: получено {len(binance_targets)} чистых крипто-токенов.[/green]")

    merged_targets = {}
    for sym, vol in binance_targets:
        merged_targets[sym] = {
            "symbol": sym,
            "binance_vol": vol,
            "bybit_vol": 0.0,
            "exchanges": ["binance"],
        }

    if settings.enable_bybit:
        console.print(
            f"[bold cyan][*] Получение фьючерсов Bybit "
            f"(${settings.min_bybit_vol_usd:,.0f} - ${settings.max_bybit_vol_usd:,.0f})...[/bold cyan]"
        )
        bybit_targets = get_bybit_target_symbols(
            min_vol_usd=settings.min_bybit_vol_usd,
            max_vol_usd=settings.max_bybit_vol_usd,
            exclude_symbols=None,
        )
        exclusive_bybit_count = 0
        shared_count = 0
        for sym, vol in bybit_targets:
            if sym in merged_targets:
                merged_targets[sym]["bybit_vol"] = vol
                if "bybit" not in merged_targets[sym]["exchanges"]:
                    merged_targets[sym]["exchanges"].append("bybit")
                shared_count += 1
            else:
                merged_targets[sym] = {
                    "symbol": sym,
                    "binance_vol": 0.0,
                    "bybit_vol": vol,
                    "exchanges": ["bybit"],
                }
                exclusive_bybit_count += 1

        console.print(
            f"[green][+] Bybit: {len(bybit_targets)} токенов обработано "
            f"({shared_count} общих с Binance, {exclusive_bybit_count} эксклюзивов Bybit).[/green]"
        )

    targets = list(merged_targets.values())
    if not targets:
        console.print("[bold red][!] Не удалось получить символы с бирж.[/bold red]")
        sys.exit(1)

    console.print(f"[green][+] Всего уникальных целей: {len(targets)}. Ищем/обновляем пулы на DexScreener...[/green]")

    existing = safe_load_json(settings.pools_map_file)
    valid_symbols_set = set(merged_targets.keys())
    existing_clean = {
        k: v for k, v in existing.items()
        if (v.get("symbol") or "").upper() in valid_symbols_set
    }
    pruned_count = len(existing) - len(existing_clean)
    if pruned_count > 0:
        console.print(
            f"[yellow][!] Удалено {pruned_count} невалидных/TradFi/устаревших пулов из карты.[/yellow]"
        )

    dex_map = build_dex_map(
        symbols=targets,
        min_liq_usd=settings.min_dex_liq_usd,
        min_vol_24h_usd=settings.min_dex_vol_24h_usd,
        rate_limit_delay=settings.dexscreener_rate_limit_delay,
        show_progress=True,
        existing_map=existing_clean,
        save_callback=lambda m: safe_save_json(m, settings.pools_map_file),
    )

    safe_save_json(dex_map, settings.pools_map_file)
    console.print(
        f"[bold green][✓] Карта пулов успешно сохранена ({len(dex_map)} пулов): {settings.pools_map_file}[/bold green]"
    )


def cmd_check_once(args):
    """Выполняет одну итерацию опроса пулов + фандинга и выводит сводку."""
    if not settings.pools_map_file.exists():
        console.print(f"[yellow][!] Файл карты {settings.pools_map_file} не найден. Запускаем build-map...[/yellow]")
        cmd_build_map(args)

    dex_map = safe_load_json(settings.pools_map_file)
    state = safe_load_json(settings.state_file)
    monitor = PoolMonitor(settings=settings, dex_map=dex_map, state=state)

    console.print(f"[*] Опрос {len(dex_map)} пулов и ставок фандинга (Binance + Bybit)...")
    funding_rates = fetch_all_funding_rates()
    live_pairs = fetch_batches(dex_map, batch_size=settings.dexscreener_batch_size)

    signals = monitor.check_cycle(live_pairs, funding_rates=funding_rates)
    safe_save_json(monitor.state, settings.state_file)

    monitor.print_status_summary(live_pairs)
    console.print(
        f"[bold green][✓] Проверено {len(live_pairs)} пулов. "
        f"Новых сигналов: {len(signals)}[/bold green]"
    )


def cmd_signals(args):
    """Выводит таблицу всех сохраненных сигналов за последние 3 суток."""
    signals = append_and_prune_signals(
        new_signals=[],
        file_path=settings.signals_file,
        retention_sec=settings.signals_retention_sec,
    )
    if not signals:
        console.print("[yellow][*] За последние 3 суток сигналов пока не зафиксировано.[/yellow]")
        return

    table = Table(title=f"История сигналов за 3 суток (Всего: {len(signals)})", border_style="magenta")
    table.add_column("Время", style="dim")
    table.add_column("ТФ", justify="center")
    table.add_column("Тип", justify="center")
    table.add_column("Символ", style="bold cyan")
    table.add_column("Биржи", style="yellow")
    table.add_column("Паттерн")
    table.add_column("Δ Квоты", justify="right")
    table.add_column("Δ Токенов", justify="right")
    table.add_column("Цена входа ($)", justify="right")
    table.add_column("Фандинг", justify="right")

    for s in signals:
        dt_str = datetime.fromtimestamp(float(s.get("timestamp", 0))).strftime("%d.%m %H:%M:%S")
        direction = s.get("direction", "SHORT")
        dir_badge = "[bold red]SHORT[/bold red]" if direction == "SHORT" else "[bold green]LONG[/bold green]"
        tf = s.get("timeframe", "1h")
        tf_badge = "[bold magenta]3h[/bold magenta]" if tf == "3h" else "[bold blue]1h[/bold blue]"
        fr = s.get("funding_rate")
        fr_str = f"{fr:+.4f}%" if fr is not None else "-"
        if s.get("is_squeeze_fuel"):
            fr_str += " 🔥"

        exchs = s.get("exchanges", ["binance"])
        ex_str = "+".join(e.capitalize() for e in exchs)

        table.add_row(
            dt_str,
            tf_badge,
            dir_badge,
            str(s.get("symbol", "")),
            ex_str,
            str(s.get("pattern", "")),
            f"{float(s.get('quote_chg', 0)):+.1f}%",
            f"{float(s.get('base_chg', 0)):+.1f}%",
            f"${float(s.get('price_usd', 0)):.6g}",
            fr_str,
        )

    console.print(table)


def cmd_test_tg(args):
    """Проверяет настройки Telegram бота и отправляет тестовый сигнал в новом дизайне."""
    if not settings.tg_bot_token or not settings.tg_chat_id:
        console.print("[bold red][!] Ошибка: TG_BOT_TOKEN или TG_CHAT_ID не заданы в .env файле.[/bold red]")
        sys.exit(1)

    console.print("[cyan][*] Отправка тестовых алертов (1ч и 3ч) в Telegram и MongoDB...[/cyan]")
    demo_signal_1h = {
        "id": "demo_pepe_1h",
        "timestamp": time.time(),
        "timeframe": "1h",
        "timeframe_sec": 3600,
        "direction": "LONG",
        "pattern": "STEALTH_ACCUMULATION",
        "symbol": "PEPE",
        "chain": "ethereum",
        "dex_id": "uniswap_v3",
        "quote_sym": "USDC",
        "quote_chg": 14.8,
        "base_chg": -8.4,
        "prev_quote_res": 320000.0,
        "curr_quote_res": 367360.0,
        "prev_tvl_usd": 640000.0,
        "curr_tvl_usd": 687360.0,
        "price_usd": 0.00001045,
        "price_chg": 1.25,
        "funding_rate": -0.0385,
        "binance_funding": -0.0385,
        "bybit_funding": -0.0210,
        "exchanges": ["binance", "bybit"],
        "buys_h1": 64,
        "sells_h1": 18,
        "vol_h1_usd": 412000.0,
        "is_squeeze_fuel": True,
        "dex_url": "https://dexscreener.com/ethereum/0xa43fe16908251ee70ef74718545e4fe6c5ccec9f",
    }
    notify_signal(settings.tg_bot_token, settings.tg_chat_id, demo_signal_1h)

    demo_signal_3h = {
        "id": "demo_mnt_3h",
        "timestamp": time.time(),
        "timeframe": "3h",
        "timeframe_sec": 10800,
        "direction": "LONG",
        "pattern": "STEALTH_ACCUMULATION",
        "symbol": "MNT",
        "chain": "solana",
        "dex_id": "raydium",
        "quote_sym": "USDC",
        "quote_chg": 28.4,
        "base_chg": -16.2,
        "prev_quote_res": 412000.0,
        "curr_quote_res": 529000.0,
        "prev_tvl_usd": 820000.0,
        "curr_tvl_usd": 965000.0,
        "price_usd": 0.884,
        "price_chg": 2.4,
        "funding_rate": -0.0150,
        "binance_funding": None,
        "bybit_funding": -0.0150,
        "exchanges": ["bybit"],
        "buys_h1": 142,
        "sells_h1": 38,
        "vol_h1_usd": 1250000.0,
        "is_squeeze_fuel": False,
        "dex_url": "https://dexscreener.com",
    }
    notify_signal(settings.tg_bot_token, settings.tg_chat_id, demo_signal_3h)
    console.print("[bold green][✓] Тестовые уведомления (1ч и 3ч) успешно отправлены в Telegram и сохранены в MongoDB![/bold green]")


def cmd_run(args):
    """Запускает непрерывный мониторинг в цикле."""
    if settings.pools_map_file.exists():
        dex_map = safe_load_json(settings.pools_map_file)
        console.print(f"[green][*] Загружена существующая карта из {len(dex_map)} DEX-пулов.[/green]")
    else:
        console.print(f"[yellow][*] Файл {settings.pools_map_file} не найден. Создаем новую карту...[/yellow]")
        cmd_build_map(args)
        dex_map = safe_load_json(settings.pools_map_file)

    if not dex_map:
        console.print("[bold red][!] Карта пулов пуста.[/bold red]")
        sys.exit(1)

    state = safe_load_json(settings.state_file)
    monitor = PoolMonitor(settings=settings, dex_map=dex_map, state=state)

    console.print(
        f"[bold blue]🚀 Радар запущен. Пулов: {len(dex_map)} | "
        f"Интервал: {settings.check_interval_sec}с | Окно: {settings.window_sec // 60}м | "
        f"Хранение сигналов: 72ч[/bold blue]"
    )

    try:
        while True:
            cycle_start = time.time()
            console.print(f"\n[cyan][{time.strftime('%H:%M:%S')}] Опрос {len(dex_map)} пулов и фандинга...[/cyan]")

            try:
                funding_rates = fetch_all_funding_rates()
                live_pairs = fetch_batches(dex_map, batch_size=settings.dexscreener_batch_size)
                signals = monitor.check_cycle(live_pairs, funding_rates=funding_rates)
                safe_save_json(monitor.state, settings.state_file)

                elapsed = time.time() - cycle_start
                console.print(
                    f"[green][{time.strftime('%H:%M:%S')}] Проверено {len(live_pairs)} пулов "
                    f"за {elapsed:.1f} сек. Сигналов: {len(signals)}. "
                    f"Спим {settings.check_interval_sec} сек...[/green]"
                )
            except Exception as e:
                console.print(f"[bold red][!] Ошибка в цикле мониторинга: {e}[/bold red]")

            time.sleep(settings.check_interval_sec)
    except KeyboardInterrupt:
        console.print("\n[yellow][*] Мониторинг остановлен. Сохранение состояния...[/yellow]")
        safe_save_json(monitor.state, settings.state_file)
        console.print("[green][✓] Завершено.[/green]")


def cmd_ui(args):
    """Запускает локальный веб-сервер с дашбордом (IndexedDB на 3 суток) и фоновым мониторингом."""
    import http.server
    import json
    import socketserver
    import threading
    import webbrowser
    from pathlib import Path

    web_dir = Path(__file__).resolve().parent.parent.parent / "web"
    port = getattr(args, "port", 8080) or 8080
    run_monitor = not getattr(args, "no_monitor", False)

    stop_event = threading.Event()
    if run_monitor:
        def background_monitor_worker():
            console.print("[dim]Запуск фонового потока проверки пулов...[/dim]")
            if not settings.pools_map_file.exists():
                return

            dex_map = safe_load_json(settings.pools_map_file)
            if not dex_map:
                return

            state = safe_load_json(settings.state_file)
            monitor = PoolMonitor(settings=settings, dex_map=dex_map, state=state)

            while not stop_event.is_set():
                try:
                    funding_rates = fetch_all_funding_rates()
                    live_pairs = fetch_batches(dex_map, batch_size=settings.dexscreener_batch_size)
                    monitor.check_cycle(live_pairs, funding_rates=funding_rates)
                    safe_save_json(monitor.state, settings.state_file)
                except Exception as e:
                    logging.getLogger(__name__).error(f"Ошибка фонового монитора: {e}")
                stop_event.wait(settings.check_interval_sec)

        monitor_thread = threading.Thread(target=background_monitor_worker, daemon=True)
        monitor_thread.start()

    class DashboardHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(web_dir), **kw)

        def _send_json(self, payload: dict | list):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            if self.path.startswith("/api/signals"):
                signals_3d = append_and_prune_signals(
                    new_signals=[],
                    file_path=settings.signals_file,
                    retention_sec=settings.signals_retention_sec,
                )
                self._send_json({"signals": signals_3d, "retention_days": 3})

            elif self.path.startswith("/api/state") or self.path.startswith("/api/pools"):
                dex_map = safe_load_json(settings.pools_map_file)
                state = safe_load_json(settings.state_file)
                signals_3d = append_and_prune_signals(
                    new_signals=[],
                    file_path=settings.signals_file,
                    retention_sec=settings.signals_retention_sec,
                )

                pools_list = []
                total_tvl = 0.0

                for key, info in dex_map.items():
                    st = state.get(key, {})
                    liq_usd = st.get("liq_usd", info.get("liquidity_usd", 0.0))
                    total_tvl += liq_usd
                    quote_res = st.get("quote_res", 0.0)
                    base_res = st.get("base_res", 0.0)
                    price_usd = st.get("price_usd", 0.0)
                    prev_quote = st.get("prev_quote_res", quote_res)
                    prev_liq = st.get("prev_liq_usd", liq_usd)
                    prev_price = st.get("prev_price_usd", price_usd)
                    buys_h1 = st.get("buys_h1", 0)
                    sells_h1 = st.get("sells_h1", 0)
                    funding_rate = st.get("funding_rate")
                    binance_fr = st.get("binance_funding")
                    bybit_fr = st.get("bybit_funding")
                    exchanges = info.get("exchanges") or (["binance"] if info.get("binance_vol", 0) > 0 else ["bybit"])

                    quote_delta = ((quote_res - prev_quote) / prev_quote * 100) if prev_quote > 0 else 0.0
                    price_delta = ((price_usd - prev_price) / prev_price * 100) if prev_price > 0 else 0.0
                    tx_ratio = (buys_h1 / sells_h1) if sells_h1 > 0 else float(buys_h1)

                    usd_delta = ((liq_usd - prev_liq) / prev_liq * 100) if prev_liq > 0 else 0.0
                    coherence = abs(usd_delta) / abs(quote_delta) if abs(quote_delta) > 0.001 else 1.0

                    is_stable = str(info.get("quote_symbol") or "USDC").upper() in {"USDT", "USDC", "BUSD", "USD", "DAI", "FDUSD", "TUSD"}
                    quote_drain_usd = abs(prev_quote - quote_res) if is_stable else 0.0
                    tvl_drain_usd = abs(prev_liq - liq_usd)
                    real_drain_usd = max(tvl_drain_usd, quote_drain_usd)
                    hourly_vol = ((info.get("binance_vol", 0.0) + info.get("bybit_vol", 0.0)) / 24.0) + float(st.get("vol_h1_usd") or 0.0)
                    impact_ratio = (real_drain_usd / hourly_vol) if hourly_vol > 0 else 0.0

                    quote_val_usd = quote_res if is_stable else (liq_usd * 0.5)
                    quote_tvl_share = (quote_val_usd / liq_usd * 100.0) if liq_usd > 0 else 0.0
                    total_txns = buys_h1 + sells_h1

                    is_short_raw = (
                        quote_delta <= settings.liq_drop_threshold_pct
                        and settings.max_price_drop_pct <= price_delta <= settings.max_price_pump_pct
                    )
                    is_short = (
                        is_short_raw
                        and coherence >= settings.min_drain_coherence_ratio
                        and impact_ratio >= settings.min_drain_impact_pct
                        and total_txns >= settings.min_pool_txns_h1
                        and (quote_tvl_share >= settings.min_quote_tvl_share_pct if is_stable else True)
                    )

                    is_squeeze = (
                        funding_rate is not None
                        and funding_rate <= settings.squeeze_funding_threshold_pct
                    )

                    # Ищем свежий сигнал по адресу пула в истории за 72ч (синхронизация с Z-Score алертами)
                    recent_sig = next(
                        (s for s in signals_3d if s.get("pair_address") == info.get("pair_address")),
                        None,
                    )
                    is_long = bool(recent_sig and recent_sig.get("direction") == "LONG")
                    is_short = is_short or bool(recent_sig and recent_sig.get("direction") == "SHORT")
                    sig_direction = recent_sig.get("direction") if recent_sig else ("SHORT" if is_short else None)
                    sig_pattern = recent_sig.get("pattern") if recent_sig else ("LIQ_DRAIN" if is_short else None)

                    pools_list.append({
                        "symbol": info.get("symbol"),
                        "chain": info.get("chain"),
                        "dex": info.get("dex"),
                        "quote_symbol": info.get("quote_symbol", "USDC"),
                        "liquidity_usd": liq_usd,
                        "prev_liquidity_usd": prev_liq,
                        "quote_res": quote_res,
                        "base_res": base_res,
                        "prev_quote_res": prev_quote,
                        "quote_delta_pct": round(quote_delta, 2),
                        "price_usd": price_usd,
                        "price_delta_pct": round(price_delta, 2),
                        "funding_rate": funding_rate,
                        "binance_funding": binance_fr,
                        "bybit_funding": bybit_fr,
                        "exchanges": exchanges,
                        "is_squeeze_fuel": is_squeeze,
                        "buys_h1": buys_h1,
                        "sells_h1": sells_h1,
                        "tx_ratio": round(tx_ratio, 2),
                        "binance_vol": info.get("binance_vol", 0),
                        "bybit_vol": info.get("bybit_vol", 0),
                        "coherence_ratio": round(coherence, 3),
                        "real_drain_usd": round(real_drain_usd, 2),
                        "impact_ratio_pct": round(impact_ratio * 100.0, 2),
                        "quote_tvl_share_pct": round(quote_tvl_share, 2),
                        "pair_address": info.get("pair_address"),
                        "url": info.get("url"),
                        "is_alert": is_short or is_long,
                        "is_short": is_short,
                        "is_long": is_long,
                        "signal_direction": sig_direction,
                        "signal_pattern": sig_pattern,
                    })

                self._send_json({
                    "status": "online",
                    "pools_count": len(dex_map),
                    "active_signals": len(signals_3d),
                    "total_tvl": total_tvl,
                    "pools": pools_list,
                    "signals": signals_3d,
                    "updated_at": time.time(),
                })
            else:
                super().do_GET()

    console.print(f"[bold green]🚀 Веб-дашборд запущен на http://localhost:{port}[/bold green]")
    try:
        webbrowser.open(f"http://localhost:{port}")
    except Exception:
        pass

    try:
        with socketserver.TCPServer(("", port), DashboardHandler) as httpd:
            console.print("[dim]Сервер активен. Для остановки нажмите Ctrl+C[/dim]")
            httpd.serve_forever()
    except KeyboardInterrupt:
        stop_event.set()
        console.print("\n[yellow]Сервер дашборда остановлен.[/yellow]")


def main():
    parser = argparse.ArgumentParser(description="DexScreener & Binance Futures Short/Pump Radar")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробные логи")

    subparsers = parser.add_subparsers(dest="command")

    p_run = subparsers.add_parser("run", help="Запустить постоянный цикл мониторинга")
    p_run.set_defaults(func=cmd_run)

    p_map = subparsers.add_parser("build-map", help="Заново перестроить карту пулов DEX")
    p_map.set_defaults(func=cmd_build_map)

    p_once = subparsers.add_parser("check-once", help="Выполнить одну разовую проверку")
    p_once.set_defaults(func=cmd_check_once)

    p_sig = subparsers.add_parser("signals", help="Показать историю сигналов за последние 3 суток")
    p_sig.set_defaults(func=cmd_signals)

    p_tg = subparsers.add_parser("test-tg", help="Отправить тестовое сообщение в Telegram")
    p_tg.set_defaults(func=cmd_test_tg)

    p_ui = subparsers.add_parser("ui", help="Открыть дашборд в браузере (с IndexedDB на 3 суток)")
    p_ui.add_argument("--port", type=int, default=8080)
    p_ui.add_argument("--no-monitor", action="store_true")
    p_ui.set_defaults(func=cmd_ui)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        cmd_run(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()