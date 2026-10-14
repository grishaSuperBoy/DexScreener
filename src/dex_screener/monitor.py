import logging
import time
import uuid
from typing import Any, Dict, List
from rich.console import Console
from rich.table import Table

from dex_screener.config import Settings
from dex_screener.dexscreener import safe_float
from dex_screener.notifier import notify_signal
from dex_screener.storage import append_and_prune_signals

logger = logging.getLogger(__name__)
console = Console()


class PoolMonitor:
    def __init__(self, settings: Settings, dex_map: Dict[str, Dict[str, Any]], state: Dict[str, Any]):
        self.settings = settings
        self.dex_map = dex_map
        self.state = state

    def check_cycle(
        self,
        live_pairs: List[Dict[str, Any]],
        funding_rates: Dict[str, float] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Проверяет пулы в скользящем часовом окне:
        1) Ищет вывод ликвидности от пика за час (SHORT).
        2) Ищет тихую заливку пула или скрытый выкуп от дна за час (LONG / ACCUMULATION).
        Сохраняет все сработавшие сигналы на 3 суток в signals_history.json.
        """
        now = time.time()
        funding_rates = funding_rates or {}
        window_sec = getattr(self.settings, "window_sec", 3600)
        max_pump_pct = getattr(self.settings, "max_price_pump_pct", 4.0)
        triggered_signals: List[Dict[str, Any]] = []

        for p in live_pairs:
            chain = p.get("chainId") or ""
            addr = p.get("pairAddress") or ""
            key = f"{chain}:{addr}"

            liq = p.get("liquidity") or {}
            quote_res = safe_float(liq.get("quote"))
            base_res = safe_float(liq.get("base"))
            liq_usd = safe_float(liq.get("usd"))
            price_usd = safe_float(p.get("priceUsd"))

            if quote_res <= 0 or price_usd <= 0:
                continue

            base_info = p.get("baseToken") or {}
            quote_info = p.get("quoteToken") or {}
            # Берем канонический тикер из нашей карты Binance (если есть), иначе из пула
            map_info = self.dex_map.get(key, {})
            sym = map_info.get("symbol") or base_info.get("symbol", "UNKNOWN")
            quote_sym = quote_info.get("symbol", "")
            dex_id = p.get("dexId", "")
            url = p.get("url", "")

            txns_h1 = (p.get("txns") or {}).get("h1") or {}
            buys_h1 = int(safe_float(txns_h1.get("buys")))
            sells_h1 = int(safe_float(txns_h1.get("sells")))
            vol_h1 = safe_float((p.get("volume") or {}).get("h1"))

            map_info = self.dex_map.get(key, {})
            exchanges = map_info.get("exchanges") or (["binance"] if map_info.get("binance_vol", 0) > 0 else ["bybit"])

            fr_raw = funding_rates.get(sym.upper())
            if isinstance(fr_raw, dict):
                funding_rate = fr_raw.get("rate")
                binance_fr = fr_raw.get("binance")
                bybit_fr = fr_raw.get("bybit")
            else:
                funding_rate = fr_raw
                binance_fr = fr_raw
                bybit_fr = None

            prev_state = self.state.get(key, {})
            history = prev_state.get("history", [])
            last_alert_time = prev_state.get("last_alert_at", 0)

            # Миграция старого стейта, если истории еще не было
            if not history and prev_state.get("quote_res", 0) > 0:
                history = [
                    {
                        "ts": prev_state.get("updated_at", now - 60),
                        "quote_res": prev_state["quote_res"],
                        "base_res": prev_state.get("base_res", base_res),
                        "liq_usd": prev_state.get("liq_usd", liq_usd),
                        "price_usd": prev_state.get("price_usd", price_usd),
                    }
                ]

            # Оставляем в истории только точки за максимальное окно (max_history_window_sec = 10800, 3 часа)
            max_window_sec = getattr(self.settings, "max_history_window_sec", 10800)
            history = [snap for snap in history if (now - snap.get("ts", 0)) <= max_window_sec]

            ref_quote, ref_liq_usd, ref_price_usd = quote_res, liq_usd, price_usd

            last_alert_map = prev_state.get("last_alert_map")
            if not isinstance(last_alert_map, dict):
                old_alert_ts = prev_state.get("last_alert_at", 0)
                last_alert_map = {"1h": old_alert_ts, "3h": old_alert_ts}

            timeframes = [
                ("1h", getattr(self.settings, "window_1h_sec", 3600)),
                ("3h", getattr(self.settings, "window_3h_sec", 10800)),
            ]

            if history:
                for tf_name, tf_seconds in timeframes:
                    tf_history = [snap for snap in history if (now - snap.get("ts", 0)) <= tf_seconds]
                    if not tf_history:
                        continue

                    oldest_snap = min(tf_history, key=lambda x: x.get("ts", 0))
                    span_sec = now - oldest_snap.get("ts", 0)
                    # Для 3h окна требуем минимум 70 минут реальной истории, чтобы оно не дублировало 1h окно
                    if tf_name == "3h" and span_sec < 4200:
                        continue

                    # Множитель порогов для 3-часового окна (требуем более весомого изменения за 3 часа)
                    tf_mult = getattr(self.settings, "tf_3h_threshold_multiplier", 1.45) if tf_name == "3h" else 1.0
                    tf_hours = tf_seconds / 3600.0

                    is_cooldown_expired = (now - last_alert_map.get(tf_name, 0)) >= self.settings.alert_cooldown_sec

                    # Общие проверки здоровья пула (для SHORT и LONG)
                    is_stable = quote_sym in {"USDT", "USDC", "BUSD", "USD", "DAI", "FDUSD", "TUSD"}
                    total_txns_h1 = buys_h1 + sells_h1
                    is_active_pool = total_txns_h1 >= self.settings.min_pool_txns_h1

                    quote_val_usd = quote_res if is_stable else (liq_usd * 0.5)
                    quote_tvl_share = (quote_val_usd / liq_usd * 100.0) if liq_usd > 0 else 0.0
                    is_balanced_pool = quote_tvl_share >= self.settings.min_quote_tvl_share_pct if is_stable else True

                    # =========================================================================
                    # 1. ПРОВЕРКА НА ШОРТ (Сравниваем с ПИКОМ ликвидности за окно)
                    # =========================================================================
                    if self.settings.track_mode == "usd":
                        max_snap = max(tf_history, key=lambda x: x.get("liq_usd", 0.0))
                    else:
                        max_snap = max(tf_history, key=lambda x: x.get("quote_res", 0.0))

                    # Если мы проверяем 3h окно, но пик ликвидности был менее 1 часа назад,
                    # значит это 1-часовой импульс (его уже обработало окно 1h) -> пропускаем дубль!
                    if tf_name == "3h" and (now - max_snap.get("ts", now)) <= 3600:
                        short_quote_chg = 0.0
                        primary_drop = 0.0
                        is_liq_drained = False
                        is_price_lagging = False
                    else:
                        max_quote = safe_float(max_snap.get("quote_res"), quote_res)
                        max_base = safe_float(max_snap.get("base_res"), base_res)
                        max_liq_usd = safe_float(max_snap.get("liq_usd"), liq_usd)
                        max_price_usd = safe_float(max_snap.get("price_usd"), price_usd)

                        short_quote_chg = ((quote_res - max_quote) / max_quote) * 100.0 if max_quote > 0 else 0.0
                        short_base_chg = ((base_res - max_base) / max_base) * 100.0 if max_base > 0 else 0.0
                        short_usd_chg = ((liq_usd - max_liq_usd) / max_liq_usd) * 100.0 if max_liq_usd > 0 else 0.0
                        short_price_chg = ((price_usd - max_price_usd) / max_price_usd) * 100.0 if max_price_usd > 0 else 0.0

                        if tf_name == "1h":
                            ref_quote, ref_liq_usd, ref_price_usd = max_quote, max_liq_usd, max_price_usd

                        if self.settings.track_mode == "usd":
                            primary_drop = short_usd_chg
                        elif self.settings.track_mode == "both":
                            primary_drop = min(short_quote_chg, short_usd_chg)
                        else:
                            primary_drop = short_quote_chg

                        is_liq_drained = primary_drop <= (self.settings.liq_drop_threshold_pct * tf_mult)
                        is_price_lagging = self.settings.max_price_drop_pct <= short_price_chg <= max_pump_pct

                    if is_liq_drained and is_price_lagging:
                        coherence_ratio = (
                            abs(short_usd_chg) / abs(short_quote_chg)
                            if abs(short_quote_chg) > 0.001
                            else 1.0
                        )
                        is_coherent = coherence_ratio >= self.settings.min_drain_coherence_ratio

                        quote_drain_usd = abs(max_quote - quote_res) if is_stable else 0.0
                        tvl_drain_usd = abs(max_liq_usd - liq_usd)
                        real_drain_usd = max(tvl_drain_usd, quote_drain_usd)

                        binance_vol = map_info.get("binance_vol", 0.0)
                        bybit_vol = map_info.get("bybit_vol", 0.0)
                        cex_hourly_vol = (binance_vol + bybit_vol) / 24.0
                        # Масштабируем оборот под длительность окна (1ч или 3ч)
                        total_window_vol = (cex_hourly_vol + vol_h1) * tf_hours

                        impact_ratio = (real_drain_usd / total_window_vol) if total_window_vol > 0 else 0.0
                        is_impactful = impact_ratio >= self.settings.min_drain_impact_pct

                        if not (is_coherent and is_impactful and is_active_pool and is_balanced_pool):
                            logger.info(
                                f"Фильтр шума [{tf_name}] {sym}: Coh={coherence_ratio:.3f} (нужно >={self.settings.min_drain_coherence_ratio}), "
                                f"Impact={impact_ratio*100:.2f}% (нужно >={self.settings.min_drain_impact_pct*100:.2f}%), "
                                f"Txns={total_txns_h1} (нужно >={self.settings.min_pool_txns_h1}), "
                                f"QuoteShare={quote_tvl_share:.2f}% (нужно >={self.settings.min_quote_tvl_share_pct}%)"
                            )
                        else:
                            sig_obj = {
                                "id": f"{sym.lower()}_{tf_name}_{uuid.uuid4().hex[:6]}",
                                "timestamp": now,
                                "timeframe": tf_name,
                                "timeframe_sec": tf_seconds,
                                "direction": "SHORT",
                                "pattern": "LIQ_DRAIN",
                                "symbol": sym,
                                "chain": chain,
                                "pair_address": addr,
                                "dex_id": dex_id,
                                "quote_sym": quote_sym,
                                "quote_chg": round(short_quote_chg, 2),
                                "base_chg": round(short_base_chg, 2),
                                "usd_chg": round(short_usd_chg, 2),
                                "prev_quote_res": max_quote,
                                "curr_quote_res": quote_res,
                                "prev_tvl_usd": max_liq_usd,
                                "curr_tvl_usd": liq_usd,
                                "price_usd": price_usd,
                                "price_chg": round(short_price_chg, 2),
                                "buys_h1": buys_h1,
                                "sells_h1": sells_h1,
                                "vol_h1_usd": vol_h1,
                                "funding_rate": funding_rate,
                                "binance_funding": binance_fr,
                                "bybit_funding": bybit_fr,
                                "exchanges": exchanges,
                                "coherence_ratio": round(coherence_ratio, 3),
                                "real_drain_usd": round(real_drain_usd, 2),
                                "impact_ratio_pct": round(impact_ratio * 100.0, 2),
                                "total_txns_h1": total_txns_h1,
                                "quote_tvl_share_pct": round(quote_tvl_share, 2),
                                "is_squeeze_fuel": False,
                                "dex_url": url,
                            }

                            if is_cooldown_expired:
                                triggered_signals.append(sig_obj)
                                notify_signal(self.settings.tg_bot_token, self.settings.tg_chat_id, sig_obj)
                                last_alert_map[tf_name] = now
                                last_alert_map["3h" if tf_name == "1h" else "1h"] = now
                            else:
                                logger.info(f"SHORT [{tf_name}] сигнал по {sym} на кулдауне.")

                    # =========================================================================
                    # 2. ПРОВЕРКА НА ЛОНГ / НАКОПЛЕНИЕ (Сравниваем с МИНИМУМОМ за окно)
                    # =========================================================================
                    elif self.settings.enable_long_signals and is_active_pool and is_balanced_pool:
                        min_snap = min(tf_history, key=lambda x: x.get("quote_res", quote_res))

                        # Защита от дубля 1h внутри 3h окна
                        if tf_name == "3h" and (now - min_snap.get("ts", now)) <= 3600:
                            continue

                        min_quote = safe_float(min_snap.get("quote_res"), quote_res)
                        min_base = safe_float(min_snap.get("base_res"), base_res)
                        min_liq_usd = safe_float(min_snap.get("liq_usd"), liq_usd)
                        min_price_usd = safe_float(min_snap.get("price_usd"), price_usd)

                        long_quote_chg = ((quote_res - min_quote) / min_quote) * 100.0 if min_quote > 0 else 0.0
                        long_base_chg = ((base_res - min_base) / min_base) * 100.0 if min_base > 0 else 0.0
                        long_usd_chg = ((liq_usd - min_liq_usd) / min_liq_usd) * 100.0 if min_liq_usd > 0 else 0.0
                        long_price_chg = ((price_usd - min_price_usd) / min_price_usd) * 100.0 if min_price_usd > 0 else 0.0

                        # Паттерн 1: Тихая заливка LP (с учетом множителя окна tf_mult)
                        is_silent_lp_add = (
                            long_quote_chg >= (self.settings.lp_inject_quote_pct * tf_mult)
                            and long_base_chg >= (self.settings.lp_inject_base_pct * tf_mult)
                            and -2.5 <= long_price_chg <= 3.5
                        )

                        # Паттерн 2: Скрытый пылесос (с учетом множителя окна tf_mult)
                        tx_ratio = (buys_h1 / sells_h1) if sells_h1 > 0 else float(buys_h1)
                        is_stealth_buy = (
                            long_quote_chg >= (self.settings.stealth_buy_quote_pct * tf_mult)
                            and long_base_chg <= (self.settings.stealth_buy_base_drop_pct * tf_mult)
                            and 0.2 <= long_price_chg <= self.settings.stealth_buy_max_price_pct
                            and tx_ratio >= self.settings.stealth_buy_min_tx_ratio
                            and buys_h1 >= self.settings.stealth_buy_min_buys_h1
                        )

                        if is_silent_lp_add or is_stealth_buy:
                            pattern_code = "SILENT_LP_ADD" if is_silent_lp_add else "STEALTH_ACCUMULATION"
                            is_squeeze = (
                                funding_rate is not None
                                and funding_rate <= self.settings.squeeze_funding_threshold_pct
                            )

                            sig_obj = {
                                "id": f"{sym.lower()}_{tf_name}_{uuid.uuid4().hex[:6]}",
                                "timestamp": now,
                                "timeframe": tf_name,
                                "timeframe_sec": tf_seconds,
                                "direction": "LONG",
                                "pattern": pattern_code,
                                "symbol": sym,
                                "chain": chain,
                                "pair_address": addr,
                                "dex_id": dex_id,
                                "quote_sym": quote_sym,
                                "quote_chg": round(long_quote_chg, 2),
                                "base_chg": round(long_base_chg, 2),
                                "usd_chg": round(long_usd_chg, 2),
                                "prev_quote_res": min_quote,
                                "curr_quote_res": quote_res,
                                "prev_tvl_usd": min_liq_usd,
                                "curr_tvl_usd": liq_usd,
                                "price_usd": price_usd,
                                "price_chg": round(long_price_chg, 2),
                                "buys_h1": buys_h1,
                                "sells_h1": sells_h1,
                                "vol_h1_usd": vol_h1,
                                "funding_rate": funding_rate,
                                "binance_funding": binance_fr,
                                "bybit_funding": bybit_fr,
                                "exchanges": exchanges,
                                "total_txns_h1": total_txns_h1,
                                "quote_tvl_share_pct": round(quote_tvl_share, 2),
                                "is_squeeze_fuel": is_squeeze,
                                "dex_url": url,
                            }

                            if is_cooldown_expired:
                                triggered_signals.append(sig_obj)
                                notify_signal(self.settings.tg_bot_token, self.settings.tg_chat_id, sig_obj)
                                last_alert_map[tf_name] = now
                                last_alert_map["3h" if tf_name == "1h" else "1h"] = now
                            else:
                                logger.info(f"LONG [{tf_name}] сигнал по {sym} на кулдауне.")

            # Записываем текущий слепок в историю
            history.append(
                {
                    "ts": now,
                    "quote_res": quote_res,
                    "base_res": base_res,
                    "liq_usd": liq_usd,
                    "price_usd": price_usd,
                }
            )

            self.state[key] = {
                "quote_res": quote_res,
                "base_res": base_res,
                "liq_usd": liq_usd,
                "price_usd": price_usd,
                "prev_quote_res": ref_quote,
                "prev_liq_usd": ref_liq_usd,
                "prev_price_usd": ref_price_usd,
                "buys_h1": buys_h1,
                "sells_h1": sells_h1,
                "funding_rate": funding_rate,
                "binance_funding": binance_fr,
                "bybit_funding": bybit_fr,
                "exchanges": exchanges,
                "history": history,
                "updated_at": now,
                "last_alert_map": last_alert_map,
                "last_alert_at": max(last_alert_map.values()) if last_alert_map else 0,
            }

        # Сохраняем все новые сигналы и чистим те, что старше 3 суток
        append_and_prune_signals(
            new_signals=triggered_signals,
            file_path=self.settings.signals_file,
            retention_sec=self.settings.signals_retention_sec,
        )

        return triggered_signals

    def print_status_summary(self, live_pairs: List[Dict[str, Any]], top_n: int = 12):
        """Выводит информационную таблицу состояния пулов в консоль."""
        table = Table(title=f"Мониторинг DEX-пулов (Всего: {len(live_pairs)})", border_style="blue")
        table.add_column("Символ", style="bold cyan")
        table.add_column("Сеть / DEX", style="dim")
        table.add_column("TVL ($)", justify="right")
        table.add_column("Резерв квоты", justify="right")
        table.add_column("Δ Квоты", justify="right")
        table.add_column("Фандинг", justify="right")
        table.add_column("Цена ($)", justify="right")

        sorted_pairs = sorted(
            live_pairs,
            key=lambda x: safe_float((x.get("liquidity") or {}).get("usd")),
            reverse=True,
        )[:top_n]

        for p in sorted_pairs:
            chain = p.get("chainId", "")
            addr = p.get("pairAddress", "")
            key = f"{chain}:{addr}"
            base_sym = (self.dex_map.get(key) or {}).get("symbol") or (p.get("baseToken") or {}).get("symbol", "")
            quote_sym = (p.get("quoteToken") or {}).get("symbol", "")
            dex = p.get("dexId", "")
            liq = p.get("liquidity") or {}
            liq_usd = safe_float(liq.get("usd"))
            quote_res = safe_float(liq.get("quote"))
            price_usd = safe_float(p.get("priceUsd"))

            st = self.state.get(key, {})
            prev_q = safe_float(st.get("prev_quote_res"), quote_res)
            q_delta = ((quote_res - prev_q) / prev_q * 100.0) if prev_q > 0 else 0.0
            funding = st.get("funding_rate")
            f_str = f"{funding:+.4f}%" if funding is not None else "-"

            table.add_row(
                base_sym,
                f"{chain}/{dex}",
                f"${liq_usd:,.0f}",
                f"{quote_res:,.0f} {quote_sym}",
                f"{q_delta:+.2f}%",
                f_str,
                f"${price_usd:.6g}",
            )

        console.print(table)