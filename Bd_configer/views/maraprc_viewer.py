# ==============================================================================
# views/maraprc_viewer.py — Окно просмотра файла maraprC.xml
# ==============================================================================
# Аналог Delphi-версии See_maraprC:
#   - Комбобокс «Тип»: Состояния / События / Типы
#   - Комбобокс «Объект»: зависит от выбранного типа
#   - Комбобокс «Сортировка»: зависит от типа
#   - Таблица с данными (три синхронизированных Treeview для цветной заливки)
#   - Поиск с навигацией
# ==============================================================================

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from constants import COLOR_CLOSE_BTN
from ui_widgets import center_window
from maraprc_parser import (
    MaraprCParser,
    load_icons,
    color_str_to_rgb,
    rgb_to_hex,
    STATE_OBJECT_NAMES,
    EVENT_OBJECT_NAMES,
    TYPE_OBJECT_NAMES,
    INPUT_COMMAND_NAMES,
    OUTPUT_COMMAND_NAMES,
    TP_OUTPUT,
)


def open_maraprc_viewer(parent):
    """
    Точка входа: показать диалог выбора файла → распарсить → открыть окно.
    parent: родительское окно (для центрирования).
    """
    filepath = filedialog.askopenfilename(
        title="Выберите файл maraprC.xml",
        filetypes=[("XML файлы", "*.xml"), ("Все файлы", "*.*")],
    )
    if not filepath:
        return

    try:
        parser = MaraprCParser()
        data = parser.parse(filepath)
    except Exception as e:
        messagebox.showerror("Ошибка", f"Не удалось разобрать файл:\n{str(e)}")
        return

    # Загружаем иконки из папки bmp/ рядом с exe/скриптом
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        # views/maraprc_viewer.py → ../
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bmp_dir = os.path.join(base_dir, 'bmp')
    icons = load_icons(bmp_dir) if os.path.isdir(bmp_dir) else {}

    MaraprCViewer(parent, data, icons)


class MaraprCViewer:
    """Окно просмотра данных распарсенного maraprC.xml."""

    def __init__(self, parent, data: dict, icons: dict):
        self._data = data
        self._icons = icons

        # === ОКНО ===
        self._win = tk.Toplevel(parent)
        self._win.title(
            f"Просмотр maraprC — {data['filename']} (v{data['configuration']})"
        )
        self._win.geometry("950x600")
        self._win.resizable(True, True)
        center_window(self._win, parent)

        # === КОМБОБОКСЫ ВВЕРХУ ===
        top_frame = tk.Frame(self._win)
        top_frame.pack(fill="x", padx=10, pady=5)

        tk.Label(top_frame, text="Тип:").pack(side="left")
        self._cb_type = ttk.Combobox(
            top_frame, values=["Состояния", "События", "Типы"],
            state="readonly", width=12,
        )
        self._cb_type.pack(side="left", padx=(2, 10))

        tk.Label(top_frame, text="Объект:").pack(side="left")
        self._cb_object = ttk.Combobox(top_frame, state="readonly", width=16)
        self._cb_object.pack(side="left", padx=(2, 10))

        tk.Label(top_frame, text="Сортировка:").pack(side="left")
        self._cb_sort = ttk.Combobox(top_frame, state="readonly", width=22)
        self._cb_sort.pack(side="left", padx=(2, 10))

        # === ПОИСК ===
        search_frame = tk.Frame(self._win)
        search_frame.pack(fill="x", padx=10, pady=(0, 5))
        tk.Label(search_frame, text="Поиск:").pack(side="left")
        self._search_var = tk.StringVar()
        self._search_entry = tk.Entry(
            search_frame, textvariable=self._search_var, width=30,
        )
        self._search_entry.pack(side="left", padx=5)
        self._search_count_label = tk.Label(
            search_frame, text="", font=("Arial", 9), fg="gray",
        )
        self._search_count_label.pack(side="left", padx=5)

        # === ТАБЛИЦЫ ===
        self._build_tables()

        # === СТАТУСНАЯ СТРОКА ===
        self._status_label = tk.Label(
            self._win, text="", font=("Arial", 9), fg="gray", anchor="w",
        )
        self._status_label.pack(fill="x", padx=10)

        # === КНОПКИ ВНИЗУ ===
        btn_frame = tk.Frame(self._win)
        btn_frame.pack(pady=8)
        tk.Button(
            btn_frame, text="Закрыть", command=self._win.destroy,
            bg=COLOR_CLOSE_BTN, fg="white", width=15, font=("Arial", 10),
        ).pack(side="left", padx=5)

        # === ПОИСК — биндинги ===
        self._search_results = []
        self._search_index = 0
        self._last_query = ''
        self._search_entry.bind("<Return>", self._do_search)
        tk.Button(
            search_frame, text="Найти", command=self._do_search,
            bg="#2196F3", fg="white", font=("Arial", 8),
        ).pack(side="left", padx=2)
        tk.Button(
            search_frame, text="✕", command=self._reset_search,
            font=("Arial", 8), width=3,
        ).pack(side="left")

        # === СВЯЗИ КОМБОБОКСОВ ===
        self._cb_type.bind("<<ComboboxSelected>>", self._on_type_change)
        self._cb_object.bind("<<ComboboxSelected>>", self._refresh_table)
        self._cb_sort.bind("<<ComboboxSelected>>", self._refresh_table)

        # === СТАРТОВОЕ СОСТОЯНИЕ ===
        self._cb_type.current(0)
        self._on_type_change()

    # -------------------------------------------------------------------------

    def _build_tables(self):
        """Создать три синхронизированных Treeview."""
        table_frame = tk.Frame(self._win)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        v_scroll = tk.Scrollbar(table_frame, orient=tk.VERTICAL)
        h_scroll = tk.Scrollbar(table_frame, orient=tk.HORIZONTAL)

        self._tree = ttk.Treeview(
            table_frame, show="tree headings",
            xscrollcommand=h_scroll.set,
        )

        # Treeview ТОЛЬКО для столбца «Цвет» (цветной фон)
        self._color_tree = ttk.Treeview(
            table_frame, show="headings",
            columns=("color_sample",), selectmode="none",
        )
        self._color_tree.heading("color_sample", text="Цвет")
        self._color_tree.column("color_sample", width=55, anchor="center")

        # Treeview для столбцов ПРАВЕЕ цвета
        self._right_tree = ttk.Treeview(
            table_frame, show="headings",
            columns=("event", "event_name"), selectmode="none",
        )
        self._right_tree.heading("event", text="Событие")
        self._right_tree.column("event", width=65, anchor="center")
        self._right_tree.heading("event_name", text="Название события")
        self._right_tree.column("event_name", width=200, anchor="w")

        # Синхронизация прокрутки
        self._syncing = False

        def _sync_all_from(source_args):
            if self._syncing:
                return
            self._syncing = True
            v_scroll.set(*source_args)
            pos = source_args[0]
            self._tree.yview_moveto(pos)
            self._color_tree.yview_moveto(pos)
            self._right_tree.yview_moveto(pos)
            self._syncing = False

        self._tree.configure(yscrollcommand=lambda *a: _sync_all_from(a))
        self._color_tree.configure(yscrollcommand=lambda *a: _sync_all_from(a))
        self._right_tree.configure(yscrollcommand=lambda *a: _sync_all_from(a))

        def _on_vscroll(*args):
            self._tree.yview(*args)
            self._color_tree.yview(*args)
            self._right_tree.yview(*args)
        v_scroll.config(command=_on_vscroll)
        h_scroll.config(command=self._tree.xview)

        def _on_mousewheel(event):
            self._tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self._color_tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            self._right_tree.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        def _on_mousewheel_linux(event):
            direction = -1 if event.num == 4 else 1
            self._tree.yview_scroll(direction, "units")
            self._color_tree.yview_scroll(direction, "units")
            self._right_tree.yview_scroll(direction, "units")
            return "break"

        for w in (self._tree, self._color_tree, self._right_tree):
            w.bind("<MouseWheel>", _on_mousewheel)
            w.bind("<Button-4>", _on_mousewheel_linux)
            w.bind("<Button-5>", _on_mousewheel_linux)

        self._tree.grid(row=0, column=0, sticky="nsew")
        self._color_tree.grid(row=0, column=1, sticky="ns")
        self._right_tree.grid(row=0, column=2, sticky="ns")
        self._color_tree.grid_remove()
        self._right_tree.grid_remove()
        v_scroll.grid(row=0, column=3, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

    # -------------------------------------------------------------------------

    def _on_type_change(self, *args):
        idx = self._cb_type.current()
        self._cb_object["values"] = []
        self._cb_sort["values"] = []

        if idx == 0:
            self._cb_object["values"] = STATE_OBJECT_NAMES
            self._cb_sort["values"] = [
                "Без сортировки", "По номеру", "По названию",
                "По группе", "По приоритету",
            ]
        elif idx == 1:
            self._cb_object["values"] = EVENT_OBJECT_NAMES
            self._cb_sort["values"] = ["Без сортировки", "По номеру", "По названию"]
        elif idx == 2:
            self._cb_object["values"] = TYPE_OBJECT_NAMES
            self._cb_sort["values"] = ["Без сортировки", "По номеру", "По названию"]

        self._cb_object.current(0)
        self._cb_sort.current(0)
        self._refresh_table()

    def _refresh_table(self, *args):
        type_idx = self._cb_type.current()
        obj_idx = self._cb_object.current()
        sort_idx = self._cb_sort.current()
        if type_idx < 0 or obj_idx < 0 or sort_idx < 0:
            return

        # Очищаем все три дерева
        for item in self._tree.get_children():
            self._tree.delete(item)
        for item in self._color_tree.get_children():
            self._color_tree.delete(item)
        for item in self._right_tree.get_children():
            self._right_tree.delete(item)

        # Сбрасываем поиск
        self._search_results = []
        self._search_index = 0
        self._last_query = ''
        self._search_count_label.config(text="")

        if type_idx == 0:
            self._render_states(obj_idx, sort_idx)
        elif type_idx == 1:
            self._render_events(obj_idx, sort_idx)
        elif type_idx == 2:
            self._render_types(obj_idx, sort_idx)

    # ---- СОСТОЯНИЯ ---------------------------------------------------------

    def _render_states(self, obj_idx, sort_idx):
        self._color_tree.grid(row=0, column=1, sticky="ns")
        self._right_tree.grid(row=0, column=2, sticky="ns")

        self._right_tree["columns"] = ("event", "event_name")
        self._right_tree.heading("event", text="Событие")
        self._right_tree.column("event", width=65, anchor="center")
        self._right_tree.heading("event_name", text="Название события")
        self._right_tree.column("event_name", width=220, anchor="w")

        self._tree.column("#0", width=40, minwidth=40, stretch=False)
        self._tree.heading("#0", text="")

        cols = ("number", "name", "group", "priority", "color_num")
        self._tree["columns"] = cols
        for c, t, w in [
            ("number", "Номер", 60), ("name", "Название", 200),
            ("group", "Группа", 60), ("priority", "Приоритет", 75),
            ("color_num", "Номер цвета", 90),
        ]:
            self._tree.heading(c, text=t)
            self._tree.column(c, width=w, anchor="center" if c != "name" else "w")

        rows = list(self._data['states'][obj_idx])
        if sort_idx == 1:
            rows.sort(key=lambda x: x['number'])
        elif sort_idx == 2:
            rows.sort(key=lambda x: x['name'])
        elif sort_idx == 3:
            rows.sort(key=lambda x: x['group'])
        elif sort_idx == 4:
            rows.sort(key=lambda x: x['priority'])

        for row in rows:
            ev_name = ""
            if self._data['events'][0]:
                for ev in self._data['events'][0]:
                    if ev['number'] == row['event']:
                        ev_name = ev['name']
                        break

            icon_img = None
            icon_idx = row['icon']
            if obj_idx in self._icons and icon_idx >= 1:
                icon_list = self._icons[obj_idx]
                if icon_idx <= len(icon_list):
                    icon_img = icon_list[icon_idx - 1]

            self._tree.insert(
                "", "end", text="",
                image=icon_img if icon_img else "",
                values=(row['number'], row['name'], row['group'],
                        row['priority'], row['color']),
            )

            self._insert_color_row(row['color'])
            self._right_tree.insert("", "end", values=(row['event'], ev_name))

        self._status_label.config(text=f"Записей: {len(rows)}")

    # ---- СОБЫТИЯ -----------------------------------------------------------

    def _render_events(self, obj_idx, sort_idx):
        self._color_tree.grid(row=0, column=1, sticky="ns")
        self._right_tree.grid(row=0, column=2, sticky="ns")

        self._right_tree["columns"] = ("type",)
        self._right_tree.heading("type", text="Тип")
        self._right_tree.column("type", width=120, anchor="center")

        self._tree.column("#0", width=0, minwidth=0, stretch=False)

        cols = ("number", "name", "color_num")
        self._tree["columns"] = cols
        for c, t, w in [
            ("number", "Номер", 60), ("name", "Название", 300),
            ("color_num", "Номер цвета", 90),
        ]:
            self._tree.heading(c, text=t)
            self._tree.column(c, width=w, anchor="center" if c != "name" else "w")

        rows = list(self._data['events'][obj_idx])
        if sort_idx == 1:
            rows.sort(key=lambda x: x['number'])
        elif sort_idx == 2:
            rows.sort(key=lambda x: x['name'])

        for row in rows:
            self._tree.insert(
                "", "end",
                values=(row['number'], row['name'], row.get('color', '')),
            )
            self._insert_color_row(row.get('color', ''))
            self._right_tree.insert("", "end", values=(row['type'],))

        self._status_label.config(text=f"Записей: {len(rows)}")

    # ---- ТИПЫ --------------------------------------------------------------

    def _render_types(self, obj_idx, sort_idx):
        self._color_tree.grid_remove()
        self._right_tree.grid_remove()

        self._tree.column("#0", width=0, minwidth=0, stretch=False)

        cmd_names = (OUTPUT_COMMAND_NAMES if obj_idx == TP_OUTPUT
                     else INPUT_COMMAND_NAMES)
        cols = ["number", "name"] + [f"cmd{i}" for i in range(len(cmd_names))]
        self._tree["columns"] = cols
        self._tree.heading("number", text="Номер")
        self._tree.heading("name", text="Название")
        self._tree.column("number", width=60, anchor="center")
        self._tree.column("name", width=200, anchor="w")
        for i, cn in enumerate(cmd_names):
            self._tree.heading(f"cmd{i}", text=cn)
            self._tree.column(f"cmd{i}", width=80, anchor="center")

        rows = list(self._data['types'][obj_idx])
        if sort_idx == 1:
            rows.sort(key=lambda x: x['number'])
        elif sort_idx == 2:
            rows.sort(key=lambda x: x['name'])

        for row in rows:
            vals = [row['number'], row['name']]
            vals += ["Да" if c else "" for c in row['commands']]
            self._tree.insert("", "end", values=vals)

        self._status_label.config(text=f"Записей: {len(rows)}")

    # ---- ВСПОМОГАТЕЛЬНОЕ ---------------------------------------------------

    def _insert_color_row(self, cs: str):
        """Вставить строку с цветным фоном в color_tree."""
        if cs and '.' in cs:
            r, g, b = color_str_to_rgb(cs)
            hex_color = rgb_to_hex(r, g, b)
            tag = f"c_{cs.replace('.', '_')}"
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            fg = "white" if brightness < 128 else "black"
            self._color_tree.tag_configure(tag, background=hex_color, foreground=fg)
            self._color_tree.insert("", "end", values=("",), tags=(tag,))
        else:
            self._color_tree.insert("", "end", values=("",))

    # ---- ПОИСК -------------------------------------------------------------

    def _do_search(self, *args):
        query = self._search_var.get().strip().lower()
        if not query:
            self._search_count_label.config(text="")
            return

        if query != self._last_query:
            self._last_query = query
            self._search_results = []
            self._search_index = 0
            self._tree.selection_remove(self._tree.selection())
            for item in self._tree.get_children():
                values = self._tree.item(item, "values")
                row_text = " ".join(str(v).lower() for v in values)
                if query in row_text:
                    self._search_results.append(item)
            if not self._search_results:
                self._search_count_label.config(text="Не найдено")
                return
            self._tree.selection_set(self._search_results)

        if self._search_results:
            idx = self._search_index % len(self._search_results)
            self._tree.see(self._search_results[idx])
            self._tree.focus(self._search_results[idx])
            self._search_count_label.config(
                text=f"{idx + 1} из {len(self._search_results)}"
            )
            self._search_index = idx + 1

    def _reset_search(self):
        self._search_var.set("")
        self._search_results = []
        self._search_index = 0
        self._last_query = ''
        self._tree.selection_remove(self._tree.selection())
        self._search_count_label.config(text="")
