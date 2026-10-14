import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


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
    
    Устойчив к одиночным экстремальным выбросам в истории пула.
    """
    if len(history_values) < min_samples:
        return 0.0

    med = calc_median(history_values)
    mad = calc_mad(history_values)

    if mad < eps:
        # Если MAD равен нулю, пробуем классический Z-score
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


def eval_short_z_anomaly(
    history_snapshots: Sequence[Dict[str, Any]],
    current_quote_res: float,
    current_price_usd: float,
    current_tvl_usd: float,
    z_short_threshold: float = -2.2,
    z_price_lag_max: float = 1.2,
    max_price_drop_pct: float = -4.5,
    max_price_lag_pct: float = 2.0,
    min_economic_drop_pct: float = -6.0,
    min_samples: int = 8,
) -> Tuple[bool, Dict[str, float]]:
    """
    Оценивает наличие аномального шорт-сигнала (отток ликвидности с запаздыванием цены)
    на основе Z-Score нормализации.
    
    Возвращает:
        (is_signal, metrics_dict)
    """
    quote_series = extract_snapshot_series(history_snapshots, "quote_res")
    price_series = extract_snapshot_series(history_snapshots, "price_usd")
    tvl_series = extract_snapshot_series(history_snapshots, "liq_usd")

    z_quote = calc_z_score(quote_series, current_quote_res, min_samples=min_samples)
    z_price = calc_z_score(price_series, current_price_usd, min_samples=min_samples)
    z_tvl = calc_z_score(tvl_series, current_tvl_usd, min_samples=min_samples)

    # Расчет процента падения от максимума в окне (экономический sanity floor)
    max_q = max(quote_series) if quote_series else current_quote_res
    quote_drop_pct = ((current_quote_res - max_q) / max_q * 100.0) if max_q > 0 else 0.0

    max_p = max(price_series) if price_series else current_price_usd
    price_drop_pct = ((current_price_usd - max_p) / max_p * 100.0) if max_p > 0 else 0.0

    # Условия сигнала:
    # 1. Z-Score оттока ликвидности статистически экстремален (<= z_short_threshold)
    # 2. Цена еще не испытала экстремального падения (запаздывает):
    #    - либо z_price в пределах коридора (z_price >= -z_price_lag_max),
    #    - либо абсолютная просадка незначительна (abs(price_drop_pct) <= max_price_lag_pct)
    #    И при этом цена не пробила нижний предел (price_drop_pct >= max_price_drop_pct)
    is_stat_drain = z_quote <= z_short_threshold
    is_lagging = (z_price >= -abs(z_price_lag_max)) or (abs(price_drop_pct) <= max_price_lag_pct)
    not_over_dumped = price_drop_pct >= max_price_drop_pct
    is_price_lag = is_lagging and not_over_dumped

    # 3. Экономический sanity floor: отток составил хотя бы min_economic_drop_pct (напр. -6%),
    #    чтобы не триггерить аномалии на микро-колебаниях в сверхтихих пулах
    is_economic = quote_drop_pct <= min_economic_drop_pct

    is_signal = is_stat_drain and is_price_lag and is_economic

    metrics = {
        "z_quote": round(z_quote, 3),
        "z_price": round(z_price, 3),
        "z_tvl": round(z_tvl, 3),
        "quote_drop_pct": round(quote_drop_pct, 2),
        "price_drop_pct": round(price_drop_pct, 2),
        "z_divergence": round(abs(z_quote) - abs(z_price), 3),
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
    max_price_gain_pct: float = 4.0,
    min_economic_gain_pct: float = 5.0,
    min_samples: int = 8,
) -> Tuple[bool, Dict[str, float]]:
    """
    Оценивает наличие аномального лонг-сигнала (приток стейблов + выкуп токенов)
    на основе Z-Score нормализации.
    
    Возвращает:
        (is_signal, metrics_dict)
    """
    quote_series = extract_snapshot_series(history_snapshots, "quote_res")
    base_series = extract_snapshot_series(history_snapshots, "base_res")
    price_series = extract_snapshot_series(history_snapshots, "price_usd")

    z_quote = calc_z_score(quote_series, current_quote_res, min_samples=min_samples)
    z_base = calc_z_score(base_series, current_base_res, min_samples=min_samples)
    z_price = calc_z_score(price_series, current_price_usd, min_samples=min_samples)

    min_q = min(quote_series) if quote_series else current_quote_res
    quote_gain_pct = ((current_quote_res - min_q) / min_q * 100.0) if min_q > 0 else 0.0

    min_p = min(price_series) if price_series else current_price_usd
    price_gain_pct = ((current_price_usd - min_p) / min_p * 100.0) if min_p > 0 else 0.0

    # Условия сигнала:
    # 1. Приток стейблов статистически аномален: z_quote >= z_quote_threshold
    # 2. Выгребание токенов статистически значимо: z_base <= z_base_threshold
    # 3. Цена еще не улетела в космос (находится в ранней фазе накопления):
    #    (z_price <= z_price_max или price_gain_pct <= 2.0%) и общий рост <= max_price_gain_pct
    is_quote_accum = z_quote >= z_quote_threshold
    is_base_drain = z_base <= z_base_threshold
    is_price_early = (z_price <= z_price_max or price_gain_pct <= 2.0) and (price_gain_pct <= max_price_gain_pct)
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
