# compare_ibutton_vs_proximity.py — Сравнение iButton и Proximity кодов для 10 сотрудников
import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bolid_converter import number_to_abd_code, abd_to_db_bytes

# Таблица CRC-8 Dallas/Maxim (из Delphi-кода)
CRCTable = [
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
    """Генератор iButton-кода (Touch Memory) из Delphi."""
    L = len(pin_text)
    if L == 0:
        return ""

    B1 = 0xF0

    if L == 1:
        B2 = int(pin_text[0]) + 0xF0
    else:
        B2 = int(pin_text[L - 1]) + int(pin_text[L - 2]) * 0x10

    if L <= 2:
        B3 = 0xFF
    elif L == 3:
        B3 = int(pin_text[0]) + 0xF0
    else:
        B3 = int(pin_text[L - 3]) + int(pin_text[L - 4]) * 0x10

    if L <= 4:
        B4 = 0xFF
    elif L == 5:
        B4 = int(pin_text[0]) + 0xF0
    else:
        B4 = int(pin_text[L - 5]) + int(pin_text[L - 6]) * 0x10

    if L <= 6:
        B5 = 0xFF
    elif L == 7:
        B5 = int(pin_text[0]) + 0xF0
    else:
        B5 = int(pin_text[L - 7]) + int(pin_text[L - 8]) * 0x10

    if L <= 8:
        B6 = 0xFF
    elif L == 9:
        B6 = int(pin_text[0]) + 0xF0
    else:
        B6 = int(pin_text[L - 9]) + int(pin_text[L - 10]) * 0x10

    if L <= 10:
        B7 = 0xFF
    elif L == 11:
        B7 = int(pin_text[0]) + 0xF0
    else:
        B7 = int(pin_text[L - 11]) + int(pin_text[L - 12]) * 0x10

    B = [B1, B2, B3, B4, B5, B6, B7]

    B8 = 0
    for b in B:
        B8 = CRCTable[B8 ^ b]

    return f"{B8:02X}{B7:02X}{B6:02X}{B5:02X}{B4:02X}{B3:02X}{B2:02X}{B1:02X}"


def main():
    print("=" * 100)
    print("СРАВНЕНИЕ: iButton (Touch Memory) vs Proximity (Болид) для 10 сотрудников")
    print("=" * 100)

    # 10 табельных номеров
    test_pins = [f"10000{i}" for i in range(1, 11)]

    print(f"\n{'№':<3} | {'Таб. номер':<12} | {'iButton (Touch Memory)':<20} | {'Proximity (Болид)':<20}")
    print("-" * 100)

    results = []

    for i, pin in enumerate(test_pins, 1):
        # iButton-код
        ibutton_code = pin_to_touch_memory(pin)

        # Proximity-код (номер карты = табельный номер как число)
        card_number = int(pin)
        proximity_abd = number_to_abd_code(card_number)

        results.append({
            "№": i,
            "Табельный номер": pin,
            "iButton код": ibutton_code,
            "Proximity код (АБД)": proximity_abd,
        })

        print(f"{i:<3} | {pin:<12} | {ibutton_code:<20} | {proximity_abd:<20}")

    print("=" * 100)

    # Сохраняем в CSV
    filename = f"ibutton_vs_proximity_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=["№", "Табельный номер", "iButton код", "Proximity код (АБД)"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n✅ Результаты сохранены: {filepath}")
    print("\n📊 Различия:")
    print("  • iButton: 8 байт, семейный код 0xF0, BCD-кодировка номера, CRC от 7 байт")
    print("  • Proximity: 8 байт, чистый binary номер (6 байт), константа 0x01, CRC от 6 байт")
    print("  • Оба используют CRC-8/Dallas-Maxim (poly=0x8C/0x31)")


if __name__ == "__main__":
    main()