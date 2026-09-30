# ==============================================================================
# person_generator.py — Генерация тестовых данных персонала
# ==============================================================================
# Используется в окне «Массовое добавление элементов».
#
# ИЗМЕНЕНИЯ относительно предыдущей версии (важно):
#   - Имена больше НЕ склеиваются с индексом ("Иванов42" → теперь просто "Иванов").
#     Уникальность гарантируется табельным номером (TAB000042) и email.
#   - Это даёт реалистичный поиск и фильтрацию по фамилии/имени.
# ==============================================================================

import random
from datetime import datetime, timedelta


_FIRST_NAMES = [
    "Иванов", "Петров", "Сидоров", "Козлов", "Новиков",
    "Кузнецов", "Попов", "Васильев", "Соколов", "Михайлов",
    "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев",
]

_NAMES = [
    "Александр", "Дмитрий", "Максим", "Сергей", "Андрей",
    "Алексей", "Артём", "Илья", "Кирилл", "Михаил",
    "Никита", "Иван", "Егор", "Роман", "Денис",
]

_MID_NAMES = [
    "Александрович", "Дмитриевич", "Максимович", "Сергеевич", "Андреевич",
    "Алексеевич", "Артёмович", "Ильич", "Кириллович", "Михайлович",
    "Никитич", "Иванович", "Егорович", "Романович", "Денисович",
]


def generate_person_data(index: int) -> dict:
    """
    Сгенерировать словарь данных для одного сотрудника.

    Параметры:
        index (int): порядковый номер (используется в табельном номере и email).

    Возвращает:
        dict, готовый к передаче в DBConnector.add_person / batch_add_persons.

    Уникальность:
        - tab_number: TAB000001, TAB000002, ...
        - email: user1@example.com, user2@example.com, ...
        - id вычисляется на стороне БД (MAX(id)+1)
    """
    return {
        'first_name': random.choice(_FIRST_NAMES),
        'name': random.choice(_NAMES),
        'mid_name': random.choice(_MID_NAMES),
        'tab_number': f"TAB{index:06d}",
        'work_phone': (
            f"+7(495){random.randint(100, 999)}"
            f"-{random.randint(10, 99)}-{random.randint(10, 99)}"
        ),
        'home_phone': (
            f"+7(812){random.randint(100, 999)}"
            f"-{random.randint(10, 99)}-{random.randint(10, 99)}"
        ),
        'email': f"user{index}@example.com",
        'birth_date': datetime.now() - timedelta(
            days=random.randint(25 * 365, 60 * 365)
        ),
        'address': (
            f"г. Москва, ул. Примерная, "
            f"д. {random.randint(1, 100)}, "
            f"кв. {random.randint(1, 500)}"
        ),
        'status': 1,
        # 'picture' добавляется отдельно вызывающим кодом, если нужны фото.
    }


# =============================================================================
# ГЕНЕРАЦИЯ КОДОВ ИДЕНТИФИКАТОРОВ
# =============================================================================
# Разные типы идентификаторов в Орионе имеют разную «правильную» длину кода:
#   - Proximity: обычно 4-8 байт HEX (например "1A2B3C4D")
#   - QR-код: длинная строка (32-64 символа base64-подобных)
#   - Брелок TouchMemory: 6 байт + контрольная сумма (12-14 HEX)
#   - Автомобильный номер: текст ("А123БВ77")
#   - Пин-код: цифры (4-8)
#
# Названия типов в pTypePasswords могут варьироваться от версии к версии,
# поэтому распознаём по подстрокам (case-insensitive).

import string


# Длина кода в HEX-символах для разных типов
_CODE_LENGTHS_BY_NAME = [
    # (подстрока в имени типа, длина HEX, формат)
    ('proximity',     8,  'hex'),
    ('touchmemory',   12, 'hex'),
    ('touch memory',  12, 'hex'),
    ('брелок',        12, 'hex'),
    ('пин',           6,  'digits'),
    ('pin',           6,  'digits'),
    ('qr',            32, 'alnum'),
    ('автомоб',       8,  'plate_ru'),
    ('номер',         8,  'plate_ru'),
    ('пароль для прог', 16, 'alnum'),
    ('отпечаток',     128, 'hex'),
    ('finger',        128, 'hex'),
    ('лицо',          256, 'hex'),
    ('face',          256, 'hex'),
    ('ладон',         256, 'hex'),
    ('palm',          256, 'hex'),
]

_RU_LETTERS_FOR_PLATE = "АВЕКМНОРСТУХ"  # допустимые в номерах РФ


def generate_mark_code(person_id: int, type_id: int,
                        type_name: str = '') -> bytes:
    """
    Сгенерировать код идентификатора.

    Параметры:
        person_id: ID сотрудника (для уникальности)
        type_id:   ID типа (резерв)
        type_name: имя типа (для определения формата)

    Возвращает:
        bytes — код идентификатора (для PG bytea / MSSQL varchar после
                перекодирования в hex-строку на стороне коннектора).

    Уникальность: гарантируется примесью person_id в код, чтобы не было
    дубликатов при unique-constraint на codep.
    """
    type_name_lower = (type_name or '').lower()

    # Определяем длину и формат
    length = 8
    fmt = 'hex'
    for substr, ln, f in _CODE_LENGTHS_BY_NAME:
        if substr in type_name_lower:
            length = ln
            fmt = f
            break

    if fmt == 'hex':
        # HEX-строка: первые символы — person_id в hex для уникальности,
        # остальное — случайные hex.
        prefix = f"{person_id:08X}"[-min(8, length):]
        rest_len = length - len(prefix)
        rest = ''.join(random.choices(string.hexdigits[:16].upper(), k=rest_len))
        code = (prefix + rest)[:length]
        return code.encode('ascii')

    elif fmt == 'digits':
        # Цифровой пин-код. Уникальность через person_id в начале.
        prefix = str(person_id)[-min(4, length):]
        rest_len = length - len(prefix)
        rest = ''.join(random.choices(string.digits, k=rest_len))
        code = (prefix + rest)[:length]
        return code.encode('ascii')

    elif fmt == 'alnum':
        prefix = f"P{person_id}"[:min(6, length)]
        rest_len = length - len(prefix)
        rest = ''.join(random.choices(
            string.ascii_uppercase + string.digits, k=rest_len))
        code = (prefix + rest)[:length]
        return code.encode('ascii')

    elif fmt == 'plate_ru':
        # Российский автомобильный номер: А123ВС77
        # Используем person_id для цифровой части (с обрезанием)
        digits = f"{person_id:03d}"[-3:]
        L1 = random.choice(_RU_LETTERS_FOR_PLATE)
        L2 = random.choice(_RU_LETTERS_FOR_PLATE)
        L3 = random.choice(_RU_LETTERS_FOR_PLATE)
        region = f"{random.randint(1, 199):02d}"
        plate = f"{L1}{digits}{L2}{L3}{region}"
        return plate.encode('utf-8')

    # Fallback — случайные hex
    return ''.join(random.choices(string.hexdigits[:16].upper(),
                                   k=length)).encode('ascii')


def person_data_iter(count: int):
    """
    Генератор словарей person_data — экономит память при больших count.

    Используется в batch_add_persons:
        connector.batch_add_persons(
            db_type, params,
            person_data_iter(1_000_000),
            ...
        )
    """
    for i in range(1, count + 1):
        yield generate_person_data(i)
