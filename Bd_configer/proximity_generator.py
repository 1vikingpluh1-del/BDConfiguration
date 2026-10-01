# ==============================================================================
# proximity_generator.py — Генератор proximity-кодов (использует bolid_converter)
# ==============================================================================

from bolid_converter import number_to_abd_code, abd_to_db_bytes, db_hex_to_number


class ProximityCodeGenerator:
    """
    Генератор proximity-кодов для Орион Про.

    Использует встроенный конвертер (bolid_converter) для создания
    валидных кодов с правильной контрольной суммой.
    """

    MAX_N = 0xFFFFFFFFFFFF  # 6 байт = 281 474 976 710 655 кодов

    def __init__(self, start: int = 1):
        self._next = start
        self._used = set()

    def load_used_codes(self, code_hex_list: list) -> None:
        """Отметить занятые номера карт."""
        for h in code_hex_list:
            n = db_hex_to_number(h)
            if n >= 0:
                self._used.add(n)
                if n >= self._next:
                    self._next = n + 1

    def generate(self) -> bytes:
        """
        Генерирует следующий уникальный код (11 байт) для БД.

        Алгоритм:
          1. Номер карты -> 8-байтный код Болида (через bolid_converter)
          2. Код Болида -> байты для колонки codep (bytea)
        """
        while self._next in self._used:
            self._next += 1
        if self._next > self.MAX_N:
            raise RuntimeError("❌ Все коды использованы!")

        n = self._next
        self._next += 1

        abd_code = number_to_abd_code(n)
        return abd_to_db_bytes(abd_code)

    @property
    def available_count(self) -> int:
        return self.MAX_N + 1 - len(self._used)