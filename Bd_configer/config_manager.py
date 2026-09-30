# ==============================================================================
# config_manager.py — Менеджер конфигурации (чтение/запись config.ini)
# ==============================================================================
# Отвечает за:
#   - Сохранение настроек подключения в config.ini
#   - Загрузку настроек из config.ini
#   - Управление флагами подсказок (HINTS)
#
# БЕЗОПАСНОСТЬ: пароли НЕ сохраняются в конфиг. Метод save() явно отбрасывает
# поля 'pg_pass' и 'ms_pass', даже если они переданы. Если нужно сохранять
# пароли — рекомендуется использовать модуль keyring (ОС-зависимое хранилище)
# вместо открытого хранения в ini.
# ==============================================================================

import configparser
import os

from constants import CONFIG_FILE


# Поля, которые ЗАПРЕЩЕНО сохранять в открытом виде в config.ini.
_FORBIDDEN_KEYS = {'pg_pass', 'ms_pass', 'password'}


class ConfigManager:
    """
    Менеджер конфигурации.

    Инкапсулирует всю работу с config.ini:
    - save()  — запись текущих настроек (пароли не сохраняются)
    - load()  — чтение сохранённых настроек
    - is_hint_shown() / mark_hint_shown() — управление подсказками
    """

    def __init__(self, config_path: str = CONFIG_FILE):
        # Путь к файлу конфигурации (по умолчанию config.ini в текущей папке)
        self._config_path = config_path

    # -------------------------------------------------------------------------
    # Сохранение настроек
    # -------------------------------------------------------------------------
    def save(self, settings: dict) -> None:
        """
        Сохранить словарь настроек в config.ini.

        - Пароли (pg_pass, ms_pass, password) НЕ сохраняются.
        - Существующая секция [HINTS] не затирается — читаем существующий
          конфиг и обновляем только секцию [DB].
        """
        config = configparser.ConfigParser()

        # ВАЖНО: читаем существующий конфиг, чтобы не потерять [HINTS]
        # и любые другие секции, которые могли там быть.
        if os.path.exists(self._config_path):
            try:
                config.read(self._config_path, encoding='utf-8')
            except Exception:
                # Битый конфиг — продолжаем с пустого
                config = configparser.ConfigParser()

        # Фильтруем настройки от запрещённых полей (паролей)
        safe_settings = {
            k: ('' if v is None else str(v))
            for k, v in settings.items()
            if k not in _FORBIDDEN_KEYS
        }

        # Перезаписываем секцию [DB]
        config['DB'] = safe_settings

        # Записываем в файл с кодировкой UTF-8
        with open(self._config_path, 'w', encoding='utf-8') as f:
            config.write(f)

    # -------------------------------------------------------------------------
    # Загрузка настроек
    # -------------------------------------------------------------------------
    def load(self) -> dict | None:
        """
        Загрузить настройки из config.ini.

        Возвращает:
            dict — словарь настроек из секции [DB], или None если файл не найден.
        """
        if not os.path.exists(self._config_path):
            return None

        config = configparser.ConfigParser()
        try:
            config.read(self._config_path, encoding='utf-8')
        except Exception:
            return None

        if 'DB' not in config:
            return None

        return dict(config['DB'])

    # -------------------------------------------------------------------------
    # Управление подсказками (HINTS)
    # -------------------------------------------------------------------------
    def is_hint_shown(self, hint_name: str = 'auth_hint_shown') -> bool:
        """Проверить, была ли подсказка уже показана пользователю."""
        if not os.path.exists(self._config_path):
            return False

        config = configparser.ConfigParser()
        try:
            config.read(self._config_path, encoding='utf-8')
        except Exception:
            return False
        return config.getboolean('HINTS', hint_name, fallback=False)

    def mark_hint_shown(self, hint_name: str = 'auth_hint_shown') -> None:
        """Отметить подсказку как показанную."""
        config = configparser.ConfigParser()

        if os.path.exists(self._config_path):
            try:
                config.read(self._config_path, encoding='utf-8')
            except Exception:
                config = configparser.ConfigParser()

        if not config.has_section('HINTS'):
            config.add_section('HINTS')

        config.set('HINTS', hint_name, 'True')

        with open(self._config_path, 'w', encoding='utf-8') as f:
            config.write(f)
