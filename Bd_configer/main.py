# ==============================================================================
# main.py — Точка входа в приложение
# ==============================================================================
# Это единственный файл, который запускается напрямую: python main.py
#
# Структура проекта:
#   main.py           ← ВЫ ЗДЕСЬ (точка входа)
#   app.py            ← Главный класс GUI (контроллер)
#   db_connector.py   ← Подключение к БД и выполнение запросов
#   server_scanner.py ← Поиск серверов в локальной сети
#   config_manager.py ← Чтение/запись config.ini
#   ui_widgets.py     ← Переиспользуемые виджеты (списки, прогресс)
#   constants.py      ← Все константы (цвета, размеры, SQL-запросы)
#   config.ini        ← Файл конфигурации (создаётся при первом сохранении)
#   main.spec         ← Спецификация для PyInstaller (сборка .exe)
#
# Зависимости (pip install):
#   - psycopg2 или psycopg2-binary  (PostgreSQL)
#   - pyodbc                         (MSSQL через ODBC)
#
# Сборка в .exe:
#   pyinstaller main.spec
# ==============================================================================

import tkinter as tk
import traceback


def main():
    """Запуск приложения."""
    try:
        from app import DBConnectionApp
        root = tk.Tk()
        app = DBConnectionApp(root)
        root.mainloop()
    except Exception as e:
        # Если что-то упало до показа окна — покажем ошибку в messagebox
        import tkinter.messagebox as mb
        try:
            err_root = tk.Tk()
            err_root.withdraw()
        except Exception:
            pass
        mb.showerror("Ошибка запуска", f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")
        raise


if __name__ == "__main__":
    main()