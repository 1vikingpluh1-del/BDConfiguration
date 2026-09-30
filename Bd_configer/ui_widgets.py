# ==============================================================================
# ui_widgets.py — Переиспользуемые виджеты интерфейса
# ==============================================================================
# Отвечает за:
#   - Всплывающее окно со списком (ListPickerWindow) — для выбора сервера/БД
#   - Окно прогресса (ProgressWindow) — индикатор загрузки
#   - Вспомогательные функции для работы с GUI
#
# Вынесено из главного класса, чтобы не раздувать его UI-логикой.
# Каждый виджет — самостоятельный класс, который можно переиспользовать.
# ==============================================================================

import tkinter as tk
from tkinter import ttk


class ListPickerWindow:
    """
    Всплывающее окно со списком для выбора элемента.

    Используется для:
      - Выбора сервера из найденных (кнопка "..." рядом с полем "Сервер")
      - Выбора базы данных из списка (кнопка "..." рядом с полем "БД")

    Параметры:
        parent (tk.Tk): родительское окно (для центрирования)
        title (str): заголовок окна
        items (list[str]): список элементов для отображения
        on_select (callable): callback-функция, вызывается при выборе элемента.
            Принимает один аргумент — выбранную строку.

    Пример:
        def handle_selection(selected_item):
            print(f"Выбрано: {selected_item}")

        ListPickerWindow(root, "Выберите сервер", ["localhost", "192.168.1.10"], handle_selection)
    """

    def __init__(self, parent: tk.Tk, title: str, items: list[str], on_select: callable):
        self._parent = parent
        self._on_select = on_select

        # Создаём Toplevel — дочернее окно, привязанное к родительскому
        self._window = tk.Toplevel(parent)
        self._window.title(title)
        self._window.geometry("300x200")
        self._window.resizable(False, False)

        # Создаём Listbox — список с одиночным выбором
        # selectmode=SINGLE — можно выбрать только один элемент
        # activestyle="dotbox" — выделение пунктиром при наведении
        self._listbox = tk.Listbox(
            self._window,
            selectmode=tk.SINGLE,
            background="white",
            selectbackground="blue",
            selectforeground="white",
            activestyle="dotbox",
        )
        self._listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Заполняем список элементами
        for item in items:
            self._listbox.insert(tk.END, item)

        # Привязываем события:
        # <<ListboxSelect>> — клик по элементу
        self._listbox.bind("<<ListboxSelect>>", self._on_item_selected)
        # <Motion> — наведение мыши (подсвечиваем элемент под курсором)
        self._listbox.bind("<Motion>", self._on_hover)

        # Центрируем окно относительно родительского
        center_window(self._window, self._parent)

    def _on_item_selected(self, event):
        """Обработчик выбора элемента из списка."""
        if not self._listbox.curselection():
            return

        # Получаем текст выбранного элемента
        selected = self._listbox.get(self._listbox.curselection()[0])

        # Закрываем окно списка
        self._window.destroy()

        # Вызываем callback с выбранным значением
        self._on_select(selected)

    def _on_hover(self, event):
        """
        Подсветка элемента при наведении мыши.

        nearest(event.y) — находит ближайший элемент к позиции курсора.
        Это даёт ощущение интерактивности, как в обычных выпадающих списках.
        """
        index = self._listbox.nearest(event.y)
        self._listbox.selection_clear(0, tk.END)
        self._listbox.selection_set(index)
        self._listbox.activate(index)

    def destroy(self):
        """Принудительно закрыть окно."""
        if self._window and self._window.winfo_exists():
            self._window.destroy()


class ProgressWindow:
    """
    Окно с индикатором прогресса (indeterminate «бегунок»).

    Показывается во время длительных операций:
      - Поиск серверов в сети
      - Получение списка баз данных

    Параметры:
        parent (tk.Tk): родительское окно
        title (str): заголовок (например, "Поиск серверов...")
        message (str): текст сообщения

    Пример:
        progress = ProgressWindow(root, "Загрузка...", "Получение списка БД...")
        # ... выполняем операцию в фоновом потоке ...
        progress.close()
    """

    def __init__(self, parent: tk.Tk, title: str, message: str):
        self._window = tk.Toplevel(parent)
        self._window.title(title)
        self._window.geometry("300x100")
        self._window.resizable(False, False)

        # Текстовое сообщение
        tk.Label(self._window, text=message, font=("Arial", 10)).pack(pady=10)

        # Прогресс-бар в режиме indeterminate — бесконечный «бегунок»
        # (мы не знаем точное время выполнения, поэтому анимация бегает туда-сюда)
        self._progress = ttk.Progressbar(self._window, mode='indeterminate')
        self._progress.pack(padx=20, pady=10, fill='x')
        self._progress.start()

        # Центрируем окно
        center_window(self._window, parent)

    def close(self):
        """Остановить прогресс-бар и закрыть окно."""
        self._progress.stop()
        if self._window and self._window.winfo_exists():
            self._window.destroy()


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =============================================================================

def center_window(window: tk.Toplevel, parent: tk.Tk) -> None:
    """
    Центрировать дочернее окно относительно родительского.

    Механизм:
        1. update_idletasks() — заставляет Tkinter пересчитать размеры окна
        2. Вычисляем позицию: центр родителя минус половина размера дочернего
        3. Применяем через geometry("+x+y")

    Параметры:
        window: окно, которое нужно отцентрировать
        parent: родительское окно (относительно которого центрируем)
    """
    window.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() // 2) - (window.winfo_width() // 2)
    y = parent.winfo_y() + (parent.winfo_height() // 2) - (window.winfo_height() // 2)
    window.geometry(f"+{x}+{y}")
