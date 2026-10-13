# ==============================================================================
# bolid_converter.py — Универсальный конвертер: Proximity (Болид) + iButton
# ==============================================================================
#
# 1. PROXIMITY (Болид):
#    Формат кода АБД (8 байт): [CRC] [6 байт номера карты] [01]
#    Формат в БД (codep, bytea): 08 01 + reverse(номер, с экранированием) + CRC
#
# 2. iButton (Touch Memory):
#    Формат кода (8 байт): [CRC] [B7][B6][B5][B4][B3][B2][B1]
#    где B1 = 0xF0 (family code), B2..B7 = номер в BCD (обратный порядок)
#    Формат в БД: 08 01 + reverse(B1..B7, с экранированием) + CRC
#
# ОБЩЕЕ ЭКРАНИРОВАНИЕ СПЕЦБАЙТОВ (схема Болида):
#   00 -> FE 01,  FE -> FE 02,  20 -> FE 03,  CC -> FE 04,  0A -> FE 05
# ==============================================================================

# ------------------------------------------------------------------------------
# ТАБЛИЦА ЭКРАНИРОВАНИЯ СПЕЦБАЙТОВ (общая для Proximity и iButton)
# ------------------------------------------------------------------------------
_ESCAPE = {0x00: 0x01, 0xFE: 0x02, 0x20: 0x03, 0xCC: 0x04, 0x0A: 0x05}
_UNESCAPE = {v: k for k, v in _ESCAPE.items()}


def _esc(b: int) -> bytes:
    """Байт -> пара FE xx если байт спец, иначе сам байт."""
    return bytes([0xFE, _ESCAPE[b]]) if b in _ESCAPE else bytes([b])


def _unescape_bytes(data: bytes) -> bytes:
    """Разэкранирование: FE xx -> спецбайт."""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0xFE and i + 1 < len(data) and data[i + 1] in _UNESCAPE:
            out.append(_UNESCAPE[data[i + 1]])
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


# ==============================================================================
# ЧАСТЬ 1: PROXIMITY (Болид)
# ==============================================================================

# КАЛИБРОВОЧНЫЕ ПАРЫ: (6 байт номера, контрольный байт)
_KNOWN = []
for _n, _c in [(1, 0x0A), (2, 0x53), (3, 0x64), (4, 0xE1), (5, 0xD6), (6, 0x8F)]:
    _KNOWN.append((_n.to_bytes(6, 'big'), _c))
for _h, _c in [
    ('0000C07E3A82', 0x6E), ('0000C0A2C652', 0x3E), ('0000C0A06AF2', 0x77),
    ('0000C0A332E2', 0x0F), ('0000C07723B2', 0x9F), ('0000C08FC992', 0x23),
    ('0000C065AD52', 0xE0), ('0000C06DF922', 0xEE),
]:
    _KNOWN.append((bytes.fromhex(_h), _c))


def _reflect8(b):
    r = 0
    for i in range(8):
        if (b >> i) & 1:
            r |= 1 << (7 - i)
    return r


def crc8(data, poly, init, refin, refout, xorout):
    reg = init
    for byte in data:
        if refin:
            byte = _reflect8(byte)
        reg ^= byte
        for _ in range(8):
            reg = ((reg << 1) ^ poly) & 0xFF if reg & 0x80 else (reg << 1) & 0xFF
    if refout:
        reg = _reflect8(reg)
    return reg ^ xorout


def _revsubst(p):
    out = bytearray()
    for b in reversed(p):
        out += bytes([0xFE, 0x01]) if b == 0 else bytes([b])
    return bytes(out)


def _variants(p):
    n = int.from_bytes(p, 'big')
    fc, cn = p[3], int.from_bytes(p[4:6], 'big')
    return {
        'p6': p, 'p6+01': p + b'\x01', 'revp6': bytes(reversed(p)),
        'revp6+01': bytes(reversed(p)) + b'\x01', '01+p6': b'\x01' + p,
        'rev(p6+01)': bytes(reversed(p + b'\x01')),
        'p3': p[3:], 'revp3': bytes(reversed(p[3:])),
        'p3+01': p[3:] + b'\x01', 'revp3+01': bytes(reversed(p[3:])) + b'\x01',
        'p4': p[2:], 'revp4': bytes(reversed(p[2:])),
        'ascii_dec': str(n).encode(), 'ascii_dec10': f"{n:010d}".encode(),
        'ascii_text': f"{fc}.{cn:05d}".encode(), 'ascii_hex': f"{n:06X}".encode(),
        'dbpre': b'\x08\x01' + _revsubst(p),
    }


def _calibrate():
    names = list(_variants(_KNOWN[0][0]).keys())
    for vname in names:
        datas = [_variants(p)[vname] for p, _ in _KNOWN]
        exps = [c for _, c in _KNOWN]
        for refin in (False, True):
            for refout in (False, True):
                for poly in range(256):
                    base = [crc8(d, poly, 0, refin, refout, 0) for d in datas]
                    diffs = {b ^ e for b, e in zip(base, exps)}
                    if len(diffs) == 1:
                        return {'variant': vname, 'poly': poly, 'init': 0,
                                'refin': refin, 'refout': refout,
                                'xorout': diffs.pop()}
    return None


PARAMS = _calibrate()


def crc_of_payload(payload: bytes) -> int:
    """Контрольный байт для 6-байтного номера карты (Proximity)."""
    if PARAMS is None:
        raise RuntimeError("Калибровка Proximity не найдена!")
    data = _variants(payload)[PARAMS['variant']]
    return crc8(data, PARAMS['poly'], PARAMS['init'],
                PARAMS['refin'], PARAMS['refout'], PARAMS['xorout'])


def number_to_abd_code(n: int) -> str:
    """Номер карты -> 8-байтный код Болида (как показывает АБД / ID Convert)."""
    p = n.to_bytes(6, 'big')
    return f"{crc_of_payload(p):02X}{p.hex().upper()}01"


def abd_to_db_bytes(abd_hex: str) -> bytes:
    """
    8-байтный код Proximity -> байты для колонки codep (bytea).
    Все спецбайты (00, FE, 20, CC, 0A) экранируются парами FE xx.
    """
    c = bytes.fromhex(abd_hex.strip())
    if len(c) != 8:
        raise ValueError(f"Ожидалось 8 байт, получено {len(c)}")
    ll, payload = c[0], c[1:7]

    out = bytearray([0x08, 0x01])
    for b in reversed(payload):
        out += _esc(b)
    out += _esc(ll)
    return bytes(out)


def db_hex_to_number(db_hex: str) -> int:
    """Обратное преобразование: hex из БД -> номер карты (Proximity)."""
    b = bytes.fromhex(db_hex)
    if not b.startswith(b'\x08\x01'):
        return -1
    body = b[2:]

    out = _unescape_bytes(body)
    if len(out) < 7:
        return -1
    payload = bytes(reversed(bytes(out[:-1])))
    crc = out[-1]
    if len(payload) != 6:
        return -1
    if crc_of_payload(payload) != crc:
        return -1
    return int.from_bytes(payload, 'big')


# ==============================================================================
# ЧАСТЬ 2: iButton (Touch Memory)
# ==============================================================================

# Таблица CRC-8 Dallas/Maxim (идентична Delphi-коду PinToTouchMemory)
_CRC_DALLAS = [
    0, 94, 188, 226, 97, 63, 221, 131, 194, 156, 126, 32, 163, 253, 31, 65,
    157, 195, 33, 127, 252, 162, 64, 30, 95, 1, 227, 189, 62, 96, 130, 220,
    35, 125, 159, 193, 66, 28, 254, 160, 225, 191, 93, 3, 128, 222, 60, 98,
    190, 224, 2, 92, 223, 129, 99, 61, 124, 34, 192, 158, 29, 67, 161, 255,
    70, 24, 250, 164, 39, 121, 155, 197, 132, 218, 56, 102, 229, 187, 89, 7,
    219, 133, 103, 57, 186, 228, 6, 88, 25, 71, 165, 251, 120, 38, 196, 154,
    101, 59, 217, 135, 4, 90, 184, 230, 167, 249, 27, 69, 198, 152, 122, 36,
    248, 166, 68, 26, 153, 199, 37, 123, 58, 100, 134, 216, 91, 5, 231, 185,
    140, 210, 48, 110, 237, 179, 81, 15, 78, 16, 242, 172, 47, 113, 147, 205,
    17, 79, 173, 243, 112, 46, 204, 146, 211, 141, 111, 49, 178, 236, 14, 80,
    175, 241, 19, 77, 206, 144, 114, 44, 109, 51, 209, 143, 12, 82, 176, 238,
    50, 108, 142, 208, 83, 13, 239, 177, 240, 174, 76, 18, 145, 207, 45, 115,
    202, 148, 118, 40, 171, 245, 23, 73, 8, 86, 180, 234, 105, 55, 213, 139,
    87, 9, 235, 181, 54, 104, 138, 212, 149, 203, 41, 119, 244, 170, 72, 22,
    233, 183, 85, 11, 136, 214, 52, 106, 43, 117, 151, 201, 74, 20, 246, 168,
    116, 42, 200, 150, 21, 75, 169, 247, 182, 232, 10, 84, 215, 137, 107, 53
]


def pin_to_touch_memory(pin_text: str) -> str:
    """
    Генерирует 8-байтный hex-код iButton (Touch Memory) из табельного номера.
    Рабочая версия из compare_ibutton_vs_proximity.py.
    """
    L = len(pin_text)
    if L == 0:
        return ""

    B1 = 0xF0

    def _make_byte(idx_from_end):
        """idx_from_end: 0 = последняя цифра, 1 = предпоследняя и т.д."""
        if idx_from_end >= L:
            return 0xFF
        if idx_from_end + 1 >= L:
            # Только одна цифра — старший полубайт = F
            return int(pin_text[L - 1 - idx_from_end]) + 0xF0
        return (int(pin_text[L - 2 - idx_from_end]) * 0x10 +
                int(pin_text[L - 1 - idx_from_end]))

    B2 = _make_byte(0)
    B3 = _make_byte(2)
    B4 = _make_byte(4)
    B5 = _make_byte(6)
    B6 = _make_byte(8)
    B7 = _make_byte(10)

    B = [B1, B2, B3, B4, B5, B6, B7]

    # CRC-8/Dallas-Maxim
    B8 = 0
    for b in B:
        B8 = _CRC_DALLAS[B8 ^ b]

    # Результат: CRC + байты в обратном порядке
    return f"{B8:02X}{B7:02X}{B6:02X}{B5:02X}{B4:02X}{B3:02X}{B2:02X}{B1:02X}"


def ibutton_to_db_bytes(ibutton_hex: str) -> bytes:
    """
    8-байтный iButton-код -> байты для колонки codep (bytea).
    Структура iButton: [CRC][B7][B6][B5][B4][B3][B2][B1]
    В БД: 08 01 + reverse(B1..B7) + CRC (все с экранированием спецбайтов)
    """
    c = bytes.fromhex(ibutton_hex.strip())
    if len(c) != 8:
        raise ValueError(f"Ожидалось 8 байт iButton, получено {len(c)}")

    crc = c[0]
    payload = c[1:]  # B7 B6 B5 B4 B3 B2 B1 (7 байт)

    out = bytearray([0x08, 0x01])
    # reverse(B1..B7) = B1 B2 B3 B4 B5 B6 B7 — то есть разворачиваем payload
    for b in reversed(payload):
        out += _esc(b)
    out += _esc(crc)
    return bytes(out)


def db_hex_to_ibutton_pin(db_hex: str) -> str:
    """
    Обратное преобразование: hex из БД -> табельный номер (PIN) iButton.
    Возвращает строку PIN или пустую строку, если не iButton.
    """
    b = bytes.fromhex(db_hex)
    if not b.startswith(b'\x08\x01'):
        return ""
    body = b[2:]

    out = _unescape_bytes(body)
    if len(out) < 8:
        return ""

    # Последний байт = CRC, предыдущие 7 = [B1, B2, B3, B4, B5, B6, B7]
    # (потому что в ibutton_to_db_bytes мы делали reversed(payload),
    # а payload был [B7, B6, B5, B4, B3, B2, B1])
    crc = out[-1]
    B_array = bytes(out[:-1])  # это [B1, B2, B3, B4, B5, B6, B7]

    # Проверяем CRC
    B8_calc = 0
    for bb in B_array:
        B8_calc = _CRC_DALLAS[B8_calc ^ bb]
    if B8_calc != crc:
        return ""

    # Проверяем family code B1 = 0xF0
    if B_array[0] != 0xF0:
        return ""

    # Извлекаем PIN из B2..B7 (BCD, порядок B7, B6, B5, B4, B3, B2)
    def _bcd_to_digits(b):
        hi = (b >> 4) & 0x0F
        lo = b & 0x0F
        digits = []
        if hi != 0x0F:
            digits.append(str(hi))
        if lo != 0x0F:
            digits.append(str(lo))
        return digits

    digits = []
    # B_array[6] = B7, B_array[5] = B6, ..., B_array[1] = B2
    for b in [B_array[6], B_array[5], B_array[4], B_array[3], B_array[2], B_array[1]]:
        digits.extend(_bcd_to_digits(b))

    return ''.join(digits)


class IButtonCodeGenerator:
    """
    Генератор iButton-кодов для Орион Про.
    Использует табельный номер сотрудника как PIN для генерации кода.
    """

    def __init__(self, start_tab: int = 100001):
        self._next_tab = start_tab
        self._used_tabs = set()

    def load_used_codes(self, code_hex_list: list) -> None:
        """Отметить занятые PIN-ы (извлекаем PIN из кодов в БД)."""
        for h in code_hex_list:
            pin = db_hex_to_ibutton_pin(h)
            if pin:
                try:
                    self._used_tabs.add(int(pin))
                except ValueError:
                    pass

    def generate(self, tab_number: int = None) -> bytes:
        """
        Генерирует iButton-код для табельного номера.
        Возвращает bytes (11 байт, готовые для codep в БД).
        """
        if tab_number is None:
            while self._next_tab in self._used_tabs:
                self._next_tab += 1
            tab_number = self._next_tab
            self._next_tab += 1

        pin = str(tab_number)
        hex_code = pin_to_touch_memory(pin)
        return ibutton_to_db_bytes(hex_code)

    @property
    def available_count(self) -> int:
        return 999999999  # iButton не ограничен как Proximity

    class IButtonProximityGenerator:
        """
        Генератор Proximity-карт по алгоритму iButton (PinToTouchMemory).

        Использует табельный номер как PIN для генерации кода через
        алгоритм Dallas/Maxim, но хранит в БД как обычную Proximity-карту.
        """

        def __init__(self, start_tab: int = 100001):
            self._next_tab = start_tab
            self._used_tabs = set()

        def load_used_codes(self, code_hex_list: list) -> None:
            """Отметить занятые PIN-ы (извлекаем PIN из кодов в БД)."""
            for h in code_hex_list:
                pin = db_hex_to_ibutton_pin(h)
                if pin:
                    try:
                        self._used_tabs.add(int(pin))
                    except ValueError:
                        pass

        def generate(self, tab_number: int = None) -> bytes:
            """
            Генерирует Proximity-код (для БД) по алгоритму iButton.
            Возвращает bytes (11 байт, готовые для codep в БД).
            """
            if tab_number is None:
                while self._next_tab in self._used_tabs:
                    self._next_tab += 1
                tab_number = self._next_tab
                self._next_tab += 1

            pin = str(tab_number)
            # Генерируем iButton-код
            ibutton_hex = pin_to_touch_memory(pin)
            # Упаковываем в формат Proximity для БД
            return ibutton_to_db_bytes(ibutton_hex)

        @property
        def available_count(self) -> int:
            return 999999999  # не ограничен

# ==============================================================================
# ТЕСТ
# ==============================================================================
if __name__ == "__main__":
    print("=" * 70)

    # --- PROXIMITY ---
    print("\n[PROXIMITY] Проверка CRC-8 Болид:")
    if PARAMS is None:
        print(" АЛГОРИТМ НЕ НАЙДЕН")
    else:
        print(f"✅ Параметры: variant={PARAMS['variant']}, poly={PARAMS['poly']}")

        print("\nПроверка на парах ID Convert:")
        ok = True
        for n, expect in [(1, '0A00000000000101'), (2, '5300000000000201'),
                          (3, '6400000000000301'), (4, 'E100000000000401'),
                          (5, 'D600000000000501'), (6, '8F00000000000601'),
                          (96, 'FE00000000006001'), (254, '5C0000000000FE01')]:
            got = number_to_abd_code(n)
            good = got == expect
            ok &= good
            print(f"   N={n:<4}: {got}  {'✅' if good else '❌ ожидалось ' + expect}")

        print("\nПроверка на реальных картах:")
        for p, c in _KNOWN[6:]:
            n = int.from_bytes(p, 'big')
            got = number_to_abd_code(n)
            expect = f"{c:02X}{p.hex().upper()}01"
            good = got == expect
            ok &= good
            print(f"   {got}  {'✅' if good else '❌ ожидалось ' + expect}")

        print(f"\n{'✅ PROXIMITY ВСЁ СХОДИТСЯ' if ok else '❌ PROXIMITY ЕСТЬ РАСХОЖДЕНИЯ'}")

    # --- iButton ---
    print("\n[iButton] Проверка PinToTouchMemory:")
    ibutton_ok = True

    # Проверяем что код корректный (16 hex-символов, заканчивается на F0)
    for pin in ["1", "10", "100", "1000", "10000", "100001"]:
        got = pin_to_touch_memory(pin)
        good = len(got) == 16 and got.endswith("F0")
        ibutton_ok &= good
        print(f"   PIN={pin:<10} → {got}  {'✅' if good else '❌'}")

    # Проверка упаковки iButton в БД и обратно (roundtrip)
    print("\n[iButton] Проверка упаковки в БД и обратно:")
    for pin in ["1", "10", "100001"]:
        hex_code = pin_to_touch_memory(pin)
        db_bytes = ibutton_to_db_bytes(hex_code)
        back_pin = db_hex_to_ibutton_pin(db_bytes.hex())
        good = back_pin == pin
        ibutton_ok &= good
        print(f"   PIN={pin:<6}: АБД={hex_code}  codep={db_bytes.hex()}  обратно={back_pin} {'✅' if good else '❌'}")

    print(f"\n{'✅ iButton ВСЁ СХОДИТСЯ' if ibutton_ok else '❌ iButton ЕСТЬ РАСХОЖДЕНИЯ'}")
    print("=" * 70)

