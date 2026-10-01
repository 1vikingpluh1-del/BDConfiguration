# ==============================================================================
# bolid_converter.py — Встроенный конвертер номеров в коды Болид (как ID Convert)
# ==============================================================================
# При импорте модуль САМ находит алгоритм контрольного байта (CRC-8),
# калибруясь по известным парам: 6 пар из ID Convert + 8 реальных карт из БД.
#
# Формат кода Болида (8 байт, как показывает АБД):
#   [CRC] [6 байт номера карты] [01]
# Формат в БД (codep, bytea):
#   08 01 + reverse(номер, со экранированием спецбайтов) + CRC(тоже экранированный)
#
# ЭКРАНИРОВАНИЕ СПЕЦБАЙТОВ (схема Болида, из документации/статей):
#   00 -> FE 01
#   FE -> FE 02
#   20 -> FE 03   (пробел)
#   CC -> FE 04
#   0A -> FE 05
# Экранируются ВСЕ байты кода: и payload, и CRC. Иначе сырой спецбайт
# «съедается» парсером АБД и код становится невалидным.
# ==============================================================================

# ------------------------------------------------------------------------------
# КАЛИБРОВОЧНЫЕ ПАРЫ: (6 байт номера, контрольный байт)
# ------------------------------------------------------------------------------
_KNOWN = []
# Из ID Convert (вход 1..6):
for _n, _c in [(1, 0x0A), (2, 0x53), (3, 0x64), (4, 0xE1), (5, 0xD6), (6, 0x8F)]:
    _KNOWN.append((_n.to_bytes(6, 'big'), _c))
# Реальные карты из твоей БД (АБД-код без первого и последнего байта):
for _h, _c in [
    ('0000C07E3A82', 0x6E), ('0000C0A2C652', 0x3E), ('0000C0A06AF2', 0x77),
    ('0000C0A332E2', 0x0F), ('0000C07723B2', 0x9F), ('0000C08FC992', 0x23),
    ('0000C065AD52', 0xE0), ('0000C06DF922', 0xEE),
]:
    _KNOWN.append((bytes.fromhex(_h), _c))


# ------------------------------------------------------------------------------
# ТАБЛИЦА ЭКРАНИРОВАНИЯ СПЕЦБАЙТОВ
# ------------------------------------------------------------------------------
_ESCAPE = {0x00: 0x01, 0xFE: 0x02, 0x20: 0x03, 0xCC: 0x04, 0x0A: 0x05}
_UNESCAPE = {v: k for k, v in _ESCAPE.items()}


def _esc(b: int) -> bytes:
    """Байт -> пара FE xx если байт спец, иначе сам байт."""
    return bytes([0xFE, _ESCAPE[b]]) if b in _ESCAPE else bytes([b])


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
    """Только для калибровочного варианта 'dbpre' (старая схема с 00->FE01)."""
    out = bytearray()
    for b in reversed(p):
        out += bytes([0xFE, 0x01]) if b == 0 else bytes([b])
    return bytes(out)


def _variants(p):
    n = int.from_bytes(p, 'big')
    fc, cn = p[3], int.from_bytes(p[4:6], 'big')
    return {
        'p6': p,
        'p6+01': p + b'\x01',
        'revp6': bytes(reversed(p)),
        'revp6+01': bytes(reversed(p)) + b'\x01',
        '01+p6': b'\x01' + p,
        'rev(p6+01)': bytes(reversed(p + b'\x01')),
        'p3': p[3:],
        'revp3': bytes(reversed(p[3:])),
        'p3+01': p[3:] + b'\x01',
        'revp3+01': bytes(reversed(p[3:])) + b'\x01',
        'p4': p[2:],
        'revp4': bytes(reversed(p[2:])),
        'ascii_dec': str(n).encode(),
        'ascii_dec10': f"{n:010d}".encode(),
        'ascii_text': f"{fc}.{cn:05d}".encode(),
        'ascii_hex': f"{n:06X}".encode(),
        'dbpre': b'\x08\x01' + _revsubst(p),
    }


def _calibrate():
    """Перебирает полиномы/отражения по всем вариантам данных."""
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
    """Контрольный байт для 6-байтного номера карты."""
    if PARAMS is None:
        raise RuntimeError("Калибровка не найдена! Пришли вывод теста разработчику.")
    data = _variants(payload)[PARAMS['variant']]
    return crc8(data, PARAMS['poly'], PARAMS['init'],
                PARAMS['refin'], PARAMS['refout'], PARAMS['xorout'])


def number_to_abd_code(n: int) -> str:
    """Номер карты -> 8-байтный код Болида (как показывает АБД / ID Convert)."""
    p = n.to_bytes(6, 'big')
    return f"{crc_of_payload(p):02X}{p.hex().upper()}01"


def abd_to_db_bytes(abd_hex: str) -> bytes:
    """
    8-байтный код Болида -> байты для колонки codep (bytea).

    Все спецбайты (00, FE, 20, CC, 0A) экранируются парами FE xx —
    и в payload, и в CRC.
    """
    c = bytes.fromhex(abd_hex.strip())
    if len(c) != 8:
        raise ValueError(f"Ожидалось 8 байт, получено {len(c)}")
    ll, payload = c[0], c[1:7]

    out = bytearray([0x08, 0x01])
    for b in reversed(payload):
        out += _esc(b)
    out += _esc(ll)                      # CRC тоже экранируем!
    return bytes(out)


def db_hex_to_number(db_hex: str) -> int:
    """Обратное преобразование: hex из БД -> номер карты (для учёта занятых)."""
    b = bytes.fromhex(db_hex)
    if not b.startswith(b'\x08\x01'):
        return -1
    body = b[2:]

    # Полное разэкранирование: FE xx -> спецбайт
    out = bytearray()
    i = 0
    while i < len(body):
        if body[i] == 0xFE and i + 1 < len(body) and body[i + 1] in _UNESCAPE:
            out.append(_UNESCAPE[body[i + 1]])
            i += 2
        else:
            out.append(body[i])
            i += 1

    if len(out) < 7:                     # 6 байт payload + 1 байт CRC
        return -1
    payload = bytes(reversed(bytes(out[:-1])))
    crc = out[-1]
    if len(payload) != 6:
        return -1
    if crc_of_payload(payload) != crc:
        return -1
    return int.from_bytes(payload, 'big')


# ==============================================================================
# ТЕСТ
# ==============================================================================
if __name__ == "__main__":
    print("=" * 70)
    if PARAMS is None:
        print("❌ АЛГОРИТМ НЕ НАЙДЕН — пришли этот вывод разработчику")
    else:
        print("✅ НАЙДЕНЫ ПАРАМЕТРЫ CRC-8:")
        for k, v in PARAMS.items():
            print(f"   {k}: {v}")

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

        print("\nПроверка экранирования (упаковка в БД и обратно):")
        for n in (1, 96, 254):
            abd = number_to_abd_code(n)
            db = abd_to_db_bytes(abd)
            back = db_hex_to_number(db.hex())
            good = back == n
            ok &= good
            print(f"   N={n:<4}: АБД={abd}  codep={db.hex()}")
            print(f"           обратно N={back}  {'✅' if good else '❌'}")

        print(f"\n{'✅ ВСЁ СХОДИТСЯ' if ok else '❌ ЕСТЬ РАСХОЖДЕНИЯ'}")
    print("=" * 70)