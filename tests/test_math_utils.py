import unittest
from dex_screener.math_utils import (
    calc_mean,
    calc_std,
    calc_median,
    calc_mad,
    calc_z_score,
    calc_robust_z_score,
    extract_snapshot_series,
    eval_short_z_anomaly,
    eval_long_z_anomaly,
)


class TestMathUtils(unittest.TestCase):
    """Юнит-тесты для математического модуля Z-Score и статистических аномалий."""

    def test_calc_mean(self):
        self.assertEqual(calc_mean([]), 0.0)
        self.assertAlmostEqual(calc_mean([10.0]), 10.0)
        self.assertAlmostEqual(calc_mean([10.0, 20.0, 30.0]), 20.0)
        self.assertAlmostEqual(calc_mean([-5.0, 5.0]), 0.0)

    def test_calc_std(self):
        # Пустой список или 1 элемент
        self.assertEqual(calc_std([]), 0.0)
        self.assertEqual(calc_std([42.0]), 0.0)
        # Одинаковые элементы -> дисперсия 0
        self.assertEqual(calc_std([10.0, 10.0, 10.0]), 0.0)
        # Известный пример: [2, 4, 4, 4, 5, 5, 7, 9] (mean=5.0, sample std=2.138...)
        sample = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        self.assertAlmostEqual(calc_std(sample), 2.13808993, places=5)

    def test_calc_median(self):
        self.assertEqual(calc_median([]), 0.0)
        self.assertEqual(calc_median([5.0]), 5.0)
        # Нечетное количество
        self.assertEqual(calc_median([7.0, 1.0, 3.0]), 3.0)
        # Четное количество
        self.assertEqual(calc_median([1.0, 2.0, 3.0, 4.0]), 2.5)

    def test_calc_mad(self):
        self.assertEqual(calc_mad([]), 0.0)
        # Для одинаковых элементов MAD = 0
        self.assertEqual(calc_mad([10.0, 10.0, 10.0]), 0.0)
        # [1, 2, 3, 4, 5, 6, 7] -> median = 4, |deviations| = [3, 2, 1, 0, 1, 2, 3], sorted = [0, 1, 1, 2, 2, 3, 3] -> median = 2.0
        self.assertEqual(calc_mad([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]), 2.0)

    def test_calc_z_score_basic(self):
        # 10 точек со средним 100 и std 10
        # Генерируем выборку: [90, 90, 95, 95, 100, 100, 105, 105, 110, 110]
        history = [90.0, 90.0, 95.0, 95.0, 100.0, 100.0, 105.0, 105.0, 110.0, 110.0]
        mean = calc_mean(history)
        std = calc_std(history)

        # Проверяем точку на +2 сигмы
        target_val = mean + 2.0 * std
        z = calc_z_score(history, target_val, min_samples=5)
        self.assertAlmostEqual(z, 2.0, places=4)

        # Проверяем точку на -2.5 сигмы
        target_drop = mean - 2.5 * std
        z_drop = calc_z_score(history, target_drop, min_samples=5)
        self.assertAlmostEqual(z_drop, -2.5, places=4)

    def test_calc_z_score_edge_cases(self):
        # Меньше min_samples -> 0.0 (холодный старт)
        self.assertEqual(calc_z_score([100.0, 102.0], 50.0, min_samples=5), 0.0)

        # Нулевая дисперсия (мертвый пул) -> 0.0 (без деления на ноль)
        self.assertEqual(calc_z_score([50.0, 50.0, 50.0, 50.0, 50.0], 10.0, min_samples=5), 0.0)

    def test_calc_robust_z_score_outlier_resistance(self):
        # История с одним безумным выбросом (памп на 10x)
        history = [100.0, 101.0, 99.0, 100.5, 99.5, 1000.0, 100.0, 101.0]
        # Для значения 90.0:
        # Обычный Z-Score искажен выбросом 1000:
        z_std = calc_z_score(history, 90.0, min_samples=5)
        # Робастный Z-score использует медиану (~100) и не поддается искажению 1000:
        z_rob = calc_robust_z_score(history, 90.0, min_samples=5)

        self.assertLess(z_rob, -5.0)  # Робастный четко видит просадку от реальной медианы 100
        self.assertGreater(z_std, z_rob)  # Обычный Z-score занижен из-за раздутой дисперсии

    def test_extract_snapshot_series(self):
        snapshots = [
            {"quote_res": 100.0, "price_usd": "1.5"},
            {"quote_res": 105.5, "price_usd": 1.55},
            {"quote_res": None, "price_usd": "bad_float"},
            {"price_usd": 1.6},
        ]
        q_series = extract_snapshot_series(snapshots, "quote_res")
        self.assertEqual(q_series, [100.0, 105.5])

        p_series = extract_snapshot_series(snapshots, "price_usd")
        self.assertEqual(p_series, [1.5, 1.55, 1.6])

    def test_eval_short_z_anomaly_signal_fired(self):
        # Эмуляция пула: 15 снимков, где quote держался около 100,000 +- 1000
        # Цена держалась около $2.00 +- $0.01
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 100_000.0 + (i % 3 - 1) * 800.0,
                "price_usd": 2.00 + (i % 2 - 0.5) * 0.01,
                "liq_usd": 200_000.0 + (i % 3 - 1) * 1600.0,
            })

        # Внезапный слив: из пула ушло 18,000 стейблов (осталось 82,000),
        # но спотовая цена еще держится ($1.99, просадка всего -0.5%)
        curr_quote = 82_000.0
        curr_price = 1.99
        curr_tvl = 164_000.0

        is_sig, metrics = eval_short_z_anomaly(
            snapshots,
            curr_quote,
            curr_price,
            curr_tvl,
            z_short_threshold=-2.2,
            z_price_lag_max=1.2,
            min_economic_drop_pct=-6.0,
            min_samples=8,
        )

        self.assertTrue(is_sig)
        self.assertLess(metrics["z_quote"], -5.0)  # Колоссальное отклонение
        self.assertGreater(metrics["z_divergence"], 3.0)  # Резервы улетели намного сильнее цены

    def test_eval_short_z_anomaly_rejected_if_price_already_dumped(self):
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 100_000.0 + (i % 3 - 1) * 800.0,
                "price_usd": 2.00 + (i % 2 - 0.5) * 0.01,
                "liq_usd": 200_000.0,
            })

        # Ликвидность вывели, но цена УЖЕ обвалилась до $1.60 (-20%)
        curr_quote = 82_000.0
        curr_price = 1.60
        curr_tvl = 150_000.0

        is_sig, metrics = eval_short_z_anomaly(
            snapshots,
            curr_quote,
            curr_price,
            curr_tvl,
            z_short_threshold=-2.2,
            z_price_lag_max=1.2,
            min_economic_drop_pct=-6.0,
            min_samples=8,
        )

        # Сигнал должен быть отклонен, так как цена уже укатана (нет фронтраннинга)
        self.assertFalse(is_sig)

    def test_eval_short_z_anomaly_rejected_on_micro_noise(self):
        # Сверх-тихий пул, где колебания составляли +- $2
        snapshots = [{"quote_res": 1000.0 + (i % 2) * 1.0, "price_usd": 1.0, "liq_usd": 2000.0} for i in range(12)]
        
        # Резерв упал на $10 (1%), статистически это может быть 5 сигм,
        # но экономически это всего -1%, что ниже min_economic_drop_pct (-6%)
        curr_quote = 990.0
        curr_price = 1.0
        curr_tvl = 1990.0

        is_sig, metrics = eval_short_z_anomaly(
            snapshots,
            curr_quote,
            curr_price,
            curr_tvl,
            z_short_threshold=-2.2,
            min_economic_drop_pct=-6.0,
        )

        self.assertFalse(is_sig)

    def test_eval_long_z_anomaly_signal_fired(self):
        # Эмуляция тихого накопления
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 50_000.0 + (i % 3 - 1) * 400.0,
                "base_res": 100_000.0 + (i % 3 - 1) * 800.0,
                "price_usd": 0.50 + (i % 2 - 0.5) * 0.002,
            })

        # Приток стейблов (+15%) и выгребание токенов (-10%), цена на старте (+1.0%)
        curr_quote = 57_500.0
        curr_base = 90_000.0
        curr_price = 0.505

        is_sig, metrics = eval_long_z_anomaly(
            snapshots,
            curr_quote,
            curr_base,
            curr_price,
            z_quote_threshold=2.0,
            z_base_threshold=-1.8,
            z_price_max=1.5,
            min_economic_gain_pct=5.0,
            min_samples=8,
        )

        self.assertTrue(is_sig)
        self.assertGreater(metrics["z_quote"], 3.0)
        self.assertLess(metrics["z_base"], -3.0)

    def test_eval_long_z_anomaly_rejected_if_price_spiked(self):
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 50_000.0 + (i % 3 - 1) * 400.0,
                "base_res": 100_000.0 + (i % 3 - 1) * 800.0,
                "price_usd": 0.50 + (i % 2 - 0.5) * 0.002,
            })

        # Токены выгребли, но цена УЖЕ улетела на $0.75 (+50%)
        curr_quote = 57_500.0
        curr_base = 90_000.0
        curr_price = 0.75

        is_sig, metrics = eval_long_z_anomaly(
            snapshots,
            curr_quote,
            curr_base,
            curr_price,
            z_price_max=1.5,
        )

        self.assertFalse(is_sig)

    def test_eval_z_anomaly_cold_start(self):
        # Если слепков меньше min_samples, сигнал не должен стрелять
        few_snapshots = [
            {"quote_res": 100_000.0, "base_res": 50_000.0, "price_usd": 1.0, "liq_usd": 200_000.0}
            for _ in range(3)
        ]
        is_short, s_metrics = eval_short_z_anomaly(
            few_snapshots, 50_000.0, 1.0, 100_000.0, min_samples=8
        )
        self.assertFalse(is_short)
        self.assertEqual(s_metrics["z_quote"], 0.0)

        is_long, l_metrics = eval_long_z_anomaly(
            few_snapshots, 150_000.0, 30_000.0, 1.0, min_samples=8
        )
        self.assertFalse(is_long)
        self.assertEqual(l_metrics["z_quote"], 0.0)

    def test_eval_short_z_anomaly_track_modes(self):
        # 15 снимков стабильного пула
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 100_000.0 + (i % 3 - 1) * 500.0,
                "price_usd": 2.00,
                "liq_usd": 200_000.0 + (i % 3 - 1) * 1000.0,
            })

        # Сценарий A: quote_res упал, но TVL не упал (например, base токен подорожал)
        # При track_mode="usd" сигнала быть не должно
        is_sig_usd, _ = eval_short_z_anomaly(
            snapshots,
            current_quote_res=80_000.0,  # -20%
            current_price_usd=2.00,
            current_tvl_usd=200_000.0,   # TVL на месте
            track_mode="usd",
            min_economic_drop_pct=-6.0,
        )
        self.assertFalse(is_sig_usd)

        # При track_mode="both" оба должны просесть
        is_sig_both, _ = eval_short_z_anomaly(
            snapshots,
            current_quote_res=80_000.0,  # -20%
            current_price_usd=2.00,
            current_tvl_usd=200_000.0,   # TVL на месте
            track_mode="both",
            min_economic_drop_pct=-6.0,
        )
        self.assertFalse(is_sig_both)

        # Если оба просели:
        is_sig_both_ok, m = eval_short_z_anomaly(
            snapshots,
            current_quote_res=80_000.0,
            current_price_usd=2.00,
            current_tvl_usd=160_000.0,
            track_mode="both",
            min_economic_drop_pct=-6.0,
        )
        self.assertTrue(is_sig_both_ok)

    def test_eval_short_z_anomaly_rejected_if_price_pumped(self):
        snapshots = [{"quote_res": 100_000.0, "price_usd": 2.00, "liq_usd": 200_000.0} for _ in range(15)]
        # Резерв упал, но цена улетела вверх на +10% (> max_price_pump_pct=4.0)
        is_sig, _ = eval_short_z_anomaly(
            snapshots,
            current_quote_res=80_000.0,
            current_price_usd=2.20,
            current_tvl_usd=160_000.0,
            max_price_pump_pct=4.0,
        )
        self.assertFalse(is_sig)

    def test_eval_long_z_anomaly_rejected_if_price_dumping(self):
        # Токены выгребают и стейблы заливают, но цена камнем летит вниз (< min_price_gain_pct=-0.5%)
        snapshots = []
        for i in range(15):
            snapshots.append({
                "quote_res": 50_000.0 + (i % 3 - 1) * 400.0,
                "base_res": 100_000.0 + (i % 3 - 1) * 800.0,
                "price_usd": 1.00 + (i % 2 - 0.5) * 0.002,
            })

        curr_quote = 60_000.0  # +20%
        curr_base = 85_000.0   # -15%
        curr_price = 0.85      # -15% дамп цены

        is_sig, metrics = eval_long_z_anomaly(
            snapshots,
            curr_quote,
            curr_base,
            curr_price,
            min_price_gain_pct=-0.5,
        )
        self.assertFalse(is_sig)
    def test_corrupt_snapshots_graceful_handling(self):
        # Наличие None, нечисловых строк, пустых словарей не ломает расчет
        dirty_snapshots = [
            {"quote_res": "not_a_number", "price_usd": None},
            {},
            {"quote_res": 100_000.0, "price_usd": 2.0, "liq_usd": 200_000.0},
            {"quote_res": 100_500.0, "price_usd": 2.01, "liq_usd": 201_000.0},
            {"quote_res": 99_500.0, "price_usd": 1.99, "liq_usd": 199_000.0},
        ]
        q_series = extract_snapshot_series(dirty_snapshots, "quote_res")
        self.assertEqual(len(q_series), 3)
        self.assertEqual(q_series, [100_000.0, 100_500.0, 99_500.0])


if __name__ == "__main__":
    unittest.main()
