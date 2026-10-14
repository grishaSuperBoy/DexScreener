import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def safe_load_json(file_path: Path, default: Any = None) -> Any:
    """Безопасно загружает JSON файл. Возвращает пустой dict (или default) при отсутствии файла."""
    if default is None:
        default = {}
    if not file_path.exists():
        return default

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Не удалось прочитать {file_path}: {e}")
        return default


def safe_save_json(data: Any, file_path: Path) -> bool:
    """
    Атомарно сохраняет данные в JSON файл (запись во временный файл + os.replace).
    """
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = file_path.with_suffix(".tmp")

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        os.replace(temp_file, file_path)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения {file_path}: {e}")
        return False


def append_and_prune_signals(
    new_signals: List[Dict[str, Any]],
    file_path: Path,
    retention_sec: int = 3 * 86400,
) -> List[Dict[str, Any]]:
    """
    Загружает историю сигналов из JSON, удаляет записи старше retention_sec (по умолчанию 3 суток),
    добавляет новые сигналы и атомарно сохраняет обратно.
    Возвращает актуальный список сигналов за последние 3 суток (от новых к старым).
    """
    now = time.time()
    existing = safe_load_json(file_path, default=[])
    if not isinstance(existing, list):
        existing = []

    # Оставляем только сигналы моложе 3 суток
    valid_signals = [
        sig for sig in existing
        if (now - float(sig.get("timestamp", 0))) <= retention_sec
    ]

    if new_signals:
        valid_signals.extend(new_signals)

    # Сортируем от самых свежих к старым
    valid_signals.sort(key=lambda x: float(x.get("timestamp", 0)), reverse=True)

    safe_save_json(valid_signals, file_path)
    return valid_signals