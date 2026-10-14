import math
from typing import Any, Dict, List, Literal, Sequence, Tuple


def calc_mean(values: Sequence[float]) -> float:
    """Вычисляет среднее арифметическое списка чисел."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def calc_std(values: Sequence[float], ddof: int = 1) -> float:
    """
    Вычисляет выборочное стандартное отклонение (sample standard deviation).
    ddof=1 для несмещенной оценки (Bessel's correction).
    """
    n = len(values)
    if n <= ddof:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - ddof)
    return math.sqrt(max(0.0, variance))


def calc_median(values: Sequence[float]) -> float:
    """Вычисляет медиану списка чисел."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    else:
        return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def calc_mad(values: Sequence[float]) -> float:
    """
    Вычисляет медианное абсолютное отклонение (Median Absolute Deviation).
    MAD = median(|x_i - median(x)|).
    """
    if not values:
        return 0.0
    med = calc_median(values)
    abs_deviations = [abs(x - med) for x in values]
    return calc_median(abs_deviations)


def calc_z_score(
    history_values: Sequence[float],
    current_value: float,
    min_samples: int = 5,
    eps: float = 1e-9,
) -> float:
    """
    Вычисляет классический Z-Score: (current - mean) / std.

    Параметры:
        history_values: исторический ряд наблюдений (например, за последние 1-3 часа)
        current_value: текущее значение метрики
        min_samples: минимальное количество наблюдений для статистической достоверности
        eps: порог для защиты от деления на ноль при нулевой волатильности

    Возвращает:
        float: Z-score (число стандартных отклонений от среднего).
        0.0 если данных недостаточно или дисперсия нулевая.
    """
    if len(history_values) < min_samples:
        return 0.0

    mean = calc_mean(history_values)
    std = calc_std(history_values, ddof=1)

    if std < eps:
        return 0.0

    return (current_value - mean) / std


def calc_robust_z_score(
    history_values: Sequence[float],
    current_value: float,
    min_samples: int = 5,
    eps: float = 1e-9,
) -> float:
    """
    Вычисляет робастный Z-score на основе медианы и MAD:
    Z_robust = 0.6745 * (current - median) / MAD

    Устойчив к одиночным экстремальным выбросам и ступенчатым наборам позиции в истории пула.
    Если более половины точек в окне одинаковы (MAD == 0), автоматически откатывается на классический Z-Score.
    """
    if len(history_values) < min_samples:
        return 0.0

    med = calc_median(history_values)
    mad = calc_mad(history_values)

    if mad < eps:
        return calc_z_score(history_values, current_value, min_samples=min_samples, eps=eps)

    # 0.6745 - константа нормализации для соответствия нормальному распределению
    return 0.6745 * (current_value - med) / mad


def extract_snapshot_series(
    snapshots: Sequence[Dict[str, Any]], field: str
) -> List[float]:
    """Извлекает числовую последовательность поля из списка слепков пула."""
    series = []
    for s in snapshots:
        val = s.get(field)
        if val is not None:
            try:
                series.append(float(val))
            except (ValueError, TypeError):
                continue
    return series



# АРХИТЕКТУРНОЕ РЕШЕНИЕ — Унификация SHORT и LONG на Robust Z-Score (MAD):
# Обе функции (eval_short_z_anomaly и eval_long_z_anomaly) по умолчанию используют
# робастный Z-Score (медиана + MAD, k=0.6745), а не классический (среднее + std Бесселя).
# Причины:
# 1. Защита от ступенчатых действий китов: при выводе LP или выкупе в 2-3 транша
#    первый импульс раздувает классическую std, занижая Z-Score основного движения.
#    MAD игнорирует выбросы — второй (главный) транш получает корректный высокий Z-Score.
# 2. Иммунитет к MEV / сэндвич-атакам: одиночные арбитражные прострелы не искажают
#    оценку нормальной волатильности пула (breakdown point MAD = 50%).
# 3. Автооткат: при MAD = 0 (пул стоял мертво > 50% окна) calc_robust_z_score
#    автоматически переключается на классический calc_z_score.
def eval_short_z_anomaly(
    history_snapshots: Sequence[Dict[str, Any]],
    current_quote_res: float,
    current_price_usd: float,
    current_tvl_usd: float,
    z_short_threshold: float = -2.2,
    z_price_lag_max: float = 1.2,
    max_price_drop_pct: float = -4.5,
    max_price_pump_pct: float = 4.0,
    max_price_lag_pct: float = 2.0,
    min_economic_drop_pct: float = -6.0,
    min_samples: int = 8,
    track_mode: Literal["quote", "usd", "both"] = "quote",
    use_robust: bool = True,
) -> Tuple[bool, Dict[str, float]]:
    """
    Оценивает наличие аномального шорт-сигнала (отток ликвидности с запаздыванием цены)
    на основе Z-Score нормализации (по умолчанию робастной через MAD).

    Возвращает:
        (is_signal, metrics_dict)
    """
    quote_series = extract_snapshot_series(history_snapshots, "quote_res")
    price_series = extract_snapshot_series(history_snapshots, "price_usd")
    tvl_series = extract_snapshot_series(history_snapshots, "liq_usd")

    z_fn = calc_robust_z_score if use_robust else calc_z_score

    z_quote = z_fn(quote_series, current_quote_res, min_samples=min_samples)
    z_price = z_fn(price_series, current_price_usd, min_samples=min_samples)
    z_tvl = z_fn(tvl_series, current_tvl_usd, min_samples=min_samples)

    # Находим референсный слепок пика ликвидности в окне (хронологически точная привязка)
    if history_snapshots:
        if track_mode == "usd":
            ref_snap = max(history_snapshots, key=lambda x: float(x.get("liq_usd") or 0.0))
        else:
            ref_snap = max(history_snapshots, key=lambda x: float(x.get("quote_res") or 0.0))
        max_q = float(ref_snap.get("quote_res") or current_quote_res)
        max_tvl = float(ref_snap.get("liq_usd") or current_tvl_usd)
        ref_p = float(ref_snap.get("price_usd") or current_price_usd)
    else:
        max_q = current_quote_res
        max_tvl = current_tvl_usd
        ref_p = current_price_usd

    quote_drop_pct = ((current_quote_res - max_q) / max_q * 100.0) if max_q > 0 else 0.0
    tvl_drop_pct = ((current_tvl_usd - max_tvl) / max_tvl * 100.0) if max_tvl > 0 else 0.0
    price_drop_pct = ((current_price_usd - ref_p) / ref_p * 100.0) if ref_p > 0 else 0.0

    # Учет режима отслеживания (quote / usd / both)
    if track_mode == "usd":
        is_stat_drain = z_tvl <= z_short_threshold
        primary_drop_pct = tvl_drop_pct
        primary_z = z_tvl
    elif track_mode == "both":
        is_stat_drain = (z_quote <= z_short_threshold) and (z_tvl <= z_short_threshold)
        primary_drop_pct = min(quote_drop_pct, tvl_drop_pct)
        primary_z = max(z_quote, z_tvl)
    else:
        is_stat_drain = z_quote <= z_short_threshold
        primary_drop_pct = quote_drop_pct
        primary_z = z_quote

    # Условия по цене:
    # 1. Цена запаздывает: либо z_price >= -|z_price_lag_max|, либо просадка в пределах max_price_lag_pct
    # 2. Цена остается в допустимом коридоре [max_price_drop_pct, max_price_pump_pct]
    is_lagging = (z_price >= -abs(z_price_lag_max)) or (abs(price_drop_pct) <= max_price_lag_pct)
    in_price_corridor = max_price_drop_pct <= price_drop_pct <= max_price_pump_pct
    is_price_lag = is_lagging and in_price_corridor

    # Экономический sanity floor: отток составил хотя бы min_economic_drop_pct (напр. -6%),
    # чтобы не триггерить аномалии на микро-колебаниях в сверхтихих пулах
    is_economic = primary_drop_pct <= min_economic_drop_pct

    is_signal = is_stat_drain and is_price_lag and is_economic

    # Направленная дивергенция: насколько сильно цена держится/растет относительно глубины слива резервов.
    # Например, при z_quote = -2.5 и z_price = +0.5 дивергенция составит +3.0 сигмы.
    z_div = z_price - primary_z

    metrics = {
        "z_quote": round(z_quote, 3),
        "z_price": round(z_price, 3),
        "z_tvl": round(z_tvl, 3),
        "quote_drop_pct": round(quote_drop_pct, 2),
        "price_drop_pct": round(price_drop_pct, 2),
        "z_divergence": round(z_div, 3),
    }

    return is_signal, metrics


def eval_long_z_anomaly(
    history_snapshots: Sequence[Dict[str, Any]],
    current_quote_res: float,
    current_base_res: float,
    current_price_usd: float,
    z_quote_threshold: float = 2.0,
    z_base_threshold: float = -1.8,
    z_price_max: float = 1.5,
    z_price_min: float = -1.0,
    min_price_gain_pct: float = -0.5,
    max_price_gain_pct: float = 4.0,
    min_economic_gain_pct: float = 5.0,
    min_samples: int = 8,
    use_robust: bool = True,
) -> Tuple[bool, Dict[str, float]]:
    """
    Оценивает наличие аномального лонг-сигнала (приток стейблов + выкуп токенов)
    на основе Z-Score нормализации (по умолчанию робастной через MAD).

    Возвращает:
        (is_signal, metrics_dict)
    """
    quote_series = extract_snapshot_series(history_snapshots, "quote_res")
    base_series = extract_snapshot_series(history_snapshots, "base_res")
    price_series = extract_snapshot_series(history_snapshots, "price_usd")

    z_fn = calc_robust_z_score if use_robust else calc_z_score

    z_quote = z_fn(quote_series, current_quote_res, min_samples=min_samples)
    z_base = z_fn(base_series, current_base_res, min_samples=min_samples)
    z_price = z_fn(price_series, current_price_usd, min_samples=min_samples)

    # Привязываем отсчет к моменту минимума стейблов в пуле (началу фазы выкупа)
    if history_snapshots:
        ref_snap = min(history_snapshots, key=lambda x: float(x.get("quote_res") or current_quote_res))
        min_q = float(ref_snap.get("quote_res") or current_quote_res)
        ref_p = float(ref_snap.get("price_usd") or current_price_usd)
    else:
        min_q = current_quote_res
        ref_p = current_price_usd

    quote_gain_pct = ((current_quote_res - min_q) / min_q * 100.0) if min_q > 0 else 0.0
    price_gain_pct = ((current_price_usd - ref_p) / ref_p * 100.0) if ref_p > 0 else 0.0

    # Условия сигнала:
    # 1. Приток стейблов статистически аномален: z_quote >= z_quote_threshold
    # 2. Выгребание токенов статистически значимо: z_base <= z_base_threshold
    # 3. Цена находится в ранней фазе накопления:
    #    - не улетела вверх (z_price <= z_price_max или price_gain_pct <= 2.0%)
    #    - НЕ летит камнем вниз (z_price >= z_price_min и price_gain_pct >= min_price_gain_pct)
    #    - не превысила абсолютный потолок старта (price_gain_pct <= max_price_gain_pct)
    is_quote_accum = z_quote >= z_quote_threshold
    is_base_drain = z_base <= z_base_threshold
    is_not_dumping = (z_price >= z_price_min) and (price_gain_pct >= min_price_gain_pct)
    is_not_overpumped = (z_price <= z_price_max or price_gain_pct <= 2.0) and (price_gain_pct <= max_price_gain_pct)
    is_price_early = is_not_dumping and is_not_overpumped

    # 4. Экономический sanity floor: приток >= min_economic_gain_pct
    is_economic = quote_gain_pct >= min_economic_gain_pct

    is_signal = is_quote_accum and is_base_drain and is_price_early and is_economic

    metrics = {
        "z_quote": round(z_quote, 3),
        "z_base": round(z_base, 3),
        "z_price": round(z_price, 3),
        "quote_gain_pct": round(quote_gain_pct, 2),
        "price_gain_pct": round(price_gain_pct, 2),
    }

    return is_signal, metrics