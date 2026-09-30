# ==============================================================================
# maraprc_parser.py — Парсер файла maraprC.xml (конфигурация АРМ Орион Про)
# ==============================================================================
# Полностью совместим со структурой Delphi-версии See_maraprC:
#   - Состояния: 10 типов объектов (device, shleif, outkey, reader, door,
#                camera, keybox, kbpenal, razdel, group_razdel)
#   - События:   8 категорий (все, прибор, вход, выход, считыватель,
#                камера, зона, группа зон)
#   - Типы:      входы (shleifs) и выходы (outkeys) с набором команд
# ==============================================================================

import xml.etree.ElementTree as ET
import os
from tkinter import PhotoImage
from PIL import Image, ImageTk


# =============================================================================
# КОНСТАНТЫ (соответствуют uConst.pas и uSee_maraprC.pas)
# =============================================================================

# --- Индексы типов объектов для состояний ---
ST_DEVICE = 0
ST_INPUT = 1
ST_OUTPUT = 2
ST_READER = 3
ST_DOOR = 4
ST_CAMERA = 5
ST_BOX = 6       # ключница
ST_PENAL = 7     # пенал
ST_ZONE = 8      # зона
ST_GROUPZONE = 9 # группа зон

# Названия объектов для комбобокса «Объект» в режиме «Состояния»
STATE_OBJECT_NAMES = [
    "Прибор", "Вход", "Выход", "Считыватель", "Точка доступа",
    "Камера", "Ключница", "Пенал", "Зона", "Группа зон",
]

# XML-теги секций состояний (порядок соответствует индексам выше)
STATE_XML_TAGS = [
    'status_devices', 'status_shleifs', 'status_outkeys', 'status_readers',
    'status_doors', 'status_camerases', 'status_keyboxes', 'status_kbpenals',
    'status_razdels', 'status_group_razdels',
]

# --- Индексы категорий событий ---
EV_ALL = 0
EV_DEVICE = 1
EV_INPUT = 2
EV_OUTPUT = 3
EV_READER = 4
EV_CAMERA = 5
EV_ZONE = 6
EV_GROUPZONE = 7

# Названия для комбобокса «Объект» в режиме «События»
EVENT_OBJECT_NAMES = [
    "Все события", "Прибор", "Вход", "Выход",
    "Считыватель", "Камера", "Зона", "Группа зон",
]

# XML-теги секций событий
EVENT_XML_TAGS = [
    'rs_event', 'event_devices', 'event_shleifs', 'event_outkeys',
    'event_readers', 'event_camers', 'event_razdel', 'event_group_razdels',
]

# --- Типы событий ---
EVENT_TYPE_MAP = {
    '': 'Стандарт', '-': 'Стандарт',
    'fire': 'Пожар', 'alarm': 'Тревога', 'fault': 'Неисправность',
    'warning': 'Внимание', 'Сработка': 'Сработка',
}

# --- Индексы типов входов/выходов ---
TP_INPUT = 0
TP_OUTPUT = 1

TYPE_OBJECT_NAMES = ["Вход", "Выход"]
TYPE_XML_TAGS = ['type_shleifs', 'type_outkeys']

# Команды для типов входов
INPUT_COMMANDS = [
    'arm', 'disarm', 'faultalarm', 'control', 'confirmation',
    'onfireguard', 'offfireguard', 'block_launch', 'launch', 'fault',
]
INPUT_COMMAND_NAMES = [
    'Взятие', 'Снятие', 'Н.тревога', 'Управл.', 'Подтв.',
    'Вкл.пож.охр.', 'Выкл.пож.охр.', 'Блок.пуска', 'Пуск', 'Неиспр.',
]

# Команды для типов выходов
OUTPUT_COMMANDS = ['control', 'confirmation', 'launch', 'fault', 'l_s_RO']
OUTPUT_COMMAND_NAMES = ['Управл.', 'Подтв.', 'Пуск', 'Неиспр.', 'ЛС РО']

# --- Префиксы файлов иконок ---
# Индекс в массиве = индекс типа объекта (ST_DEVICE, ST_INPUT, ...)
# Только для объектов, у которых есть иконки
ICON_PREFIXES = {
    ST_DEVICE: ('Device', 8),    # Device1.bmp — Device8.bmp
    ST_INPUT: ('Input', 9),      # Input1.bmp — Input9.bmp
    ST_OUTPUT: ('Output', 8),    # Output1.bmp — Output8.bmp
    ST_READER: ('Reader', 8),    # Reader1.bmp — Reader8.bmp
    ST_DOOR: ('Door', 9),        # Door1.bmp — Door9.bmp
    ST_CAMERA: ('Camera', 8),    # Camera1.bmp — Camera8.bmp
    # !!!! TODO: Добавить Reader1-8.bmp и Output1-2.bmp когда будут файлы !!!!
}


# =============================================================================
# ПАРСЕР
# =============================================================================

class MaraprCParser:
    """
    Парсер файла maraprC.xml — полный аналог Delphi-версии.
    """

    def parse(self, filepath: str) -> dict:
        """
        Разобрать файл и вернуть все данные.

        Возвращает dict:
            'filename', 'configuration',
            'states'  — list[list[dict]] (10 типов объектов)
            'events'  — list[list[dict]] (8 категорий)
            'types'   — list[list[dict]] (2: входы и выходы)
        """
        with open(filepath, 'r', encoding='windows-1251') as f:
            content = f.read()

        root = ET.fromstring(content)

        return {
            'filename': os.path.basename(filepath),
            'configuration': root.get('configuration', '???'),
            'states': self._parse_all_states(root),
            'events': self._parse_all_events(root),
            'types': self._parse_all_types(root),
        }

    # --- Состояния ---
    def _parse_all_states(self, root):
        """Разобрать все 10 типов состояний."""
        result = []
        for tag in STATE_XML_TAGS:
            states = []
            for section in root.iter(tag):
                for elem in section.findall('state'):
                    states.append({
                        'number': int(elem.get('number', '0')),
                        'name': elem.get('name', ''),
                        'group': int(elem.get('pultpriority', '0')),
                        'priority': int(elem.get('priority', '0')),
                        'color': elem.get('color', '0.0.0'),
                        'icon': int(elem.get('state', '0')),
                        'event': int(elem.get('event', '-1')),
                        'type': elem.get('type', ''),
                    })
            result.append(states)
        return result

    # --- События ---
    def _parse_all_events(self, root):
        """Разобрать все 8 категорий событий."""
        result = []
        for i, tag in enumerate(EVENT_XML_TAGS):
            events = []
            for section in root.iter(tag):
                for elem in section.findall('event'):
                    ev = {
                        'number': int(elem.get('number', '0')),
                        'name': elem.get('name', ''),
                        'type': EVENT_TYPE_MAP.get(
                            elem.get('type', ''), 'Неизвестный'
                        ),
                    }
                    # Цвет только для категории «Все события»
                    if i == EV_ALL:
                        ev['color'] = elem.get('color', '0.0.0')
                    else:
                        ev['color'] = ''
                    events.append(ev)
            result.append(events)
        return result

    # --- Типы входов/выходов ---
    def _parse_all_types(self, root):
        """Разобрать типы входов и выходов с командами."""
        result = []
        for i, tag in enumerate(TYPE_XML_TAGS):
            types = []
            for section in root.iter(tag):
                for elem in section.findall('type'):
                    cmds = OUTPUT_COMMANDS if i == TP_OUTPUT else INPUT_COMMANDS
                    type_data = {
                        'number': int(elem.get('value', '0')),
                        'name': elem.get('name', ''),
                        'commands': [
                            elem.get(cmd) is not None for cmd in cmds
                        ],
                    }
                    types.append(type_data)
            result.append(types)
        return result


# =============================================================================
# ЗАГРУЗКА ИКОНОК
# =============================================================================

def load_icons(bmp_dir: str) -> dict:
    """
    Загрузить BMP-иконки из папки bmp/.

    Параметры:
        bmp_dir (str): путь к папке с BMP-файлами

    Возвращает:
        dict[int, list[ImageTk.PhotoImage]] — иконки по типам объектов.
        Ключ = индекс типа (ST_DEVICE, ST_INPUT, ...).
        Значение = список PhotoImage, индекс = номер иконки - 1.

    Если файл не найден — вместо него будет None.
    """
    icons = {}
    for obj_type, (prefix, count) in ICON_PREFIXES.items():
        icon_list = []
        for i in range(1, count + 1):
            filepath = os.path.join(bmp_dir, f"{prefix}{i}.bmp")
            if os.path.exists(filepath):
                try:
                    img = Image.open(filepath)
                    icon_list.append(ImageTk.PhotoImage(img))
                except Exception:
                    icon_list.append(None)
            else:
                icon_list.append(None)
        icons[obj_type] = icon_list
    return icons


def color_str_to_rgb(color_str: str) -> tuple:
    """
    Преобразовать строку цвета '255.0.128' в кортеж (255, 0, 128).
    """
    try:
        parts = color_str.split('.')
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except Exception:
        return (0, 0, 0)


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Преобразовать RGB в hex-строку '#FF0080'."""
    return f"#{r:02x}{g:02x}{b:02x}"