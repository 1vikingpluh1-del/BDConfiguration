# ==============================================================================
# views/person_search_view.py — Поиск/выборка сотрудников по идентификаторам
# ==============================================================================
# Окно для фильтрации сотрудников по типам идентификаторов (записям в pMark).
#
# ИСПОЛЬЗОВАНИЕ:
#   - Открывается из меню «Взаимодействие с БД» → «Поиск/выборка сотрудников».
#   - Вверху: список типов идентификаторов с чекбоксами (мультивыбор).
#     По умолчанию все включены — показываем всех с любым идентификатором.
#   - Поле поиска по ФИО (LIKE %text%, без регистра).
#   - Кнопка «Найти» — выполняет запрос с фильтрами.
#   - Таблица результатов: ФИО, табельный номер, кол-во идентификаторов,
#     перечисление типов через запятую.
#   - Двойной клик по строке — копирует ID/ФИО в буфер обмена.
#   - «Экспорт CSV» — выгрузка результатов в файл.
#
# ОГРАНИЧЕНИЯ:
#   - Запрос ограничен 5000 записей (LIMIT/TOP в db_connector.py).
#     Если найдено больше — добавляйте фильтр.
# ==============================================================================

import csv
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from constants import COLOR_INTERACT_BTN, COLOR_CLOSE_BTN
from ui_widgets import center_window


class PersonSearchView:
    """Окно поиска сотрудников по идентификаторам."""

    def __init__(self, parent, connector, db_type, params, db_name):
        self._connector = connector
        self._db_type = db_type
        self._params = params
        self._db_name = db_name

        # Кэш типов: list[(id, name, comment)]
        self._password_types = []
        # Чекбоксы типов: list[tk.BooleanVar]
        self._type_vars = []
        # Текущие результаты поиска
        self._results = []

        # --- Окно ---
        self._win = tk.Toplevel(parent)
        self._win.title(f"Поиск сотрудников по идентификаторам — {db_name}")
        self._win.geometry("950x650")
        self._win.resizable(True, True)
        center_window(self._win, parent)

        self._build_filters_section()
        self._build_results_table()
        self._build_buttons()

        # Загружаем типы и сразу запускаем первичный поиск
        self._load_password_types_async()

    # =========================================================================
    # ПОСТРОЕНИЕ UI
    # =========================================================================

    def _build_filters_section(self):
        """Верхняя часть — фильтры."""
        filters_frame = tk.LabelFrame(
            self._win, text="Фильтры", font=("Arial", 9, "bold"),
            padx=10, pady=8,
        )
        filters_frame.pack(fill="x", padx=10, pady=(10, 5))

        # Поиск по ФИО
        name_row = tk.Frame(filters_frame)
        name_row.pack(fill="x", pady=(0, 5))

        tk.Label(name_row, text="Поиск по ФИО:",
                 font=("Arial", 9)).pack(side="left", padx=(0, 5))
        self._name_var = tk.StringVar()
        name_entry = tk.Entry(name_row, textvariable=self._name_var, width=40)
        name_entry.pack(side="left", padx=5)
        name_entry.bind("<Return>", lambda e: self._do_search())

        tk.Button(
            name_row, text="✕ Сброс",
            command=lambda: self._name_var.set(''),
            font=("Arial", 8), width=8,
        ).pack(side="left", padx=5)

        # Типы идентификаторов
        types_label_row = tk.Frame(filters_frame)
        types_label_row.pack(fill="x", pady=(8, 2))

        tk.Label(types_label_row, text="Типы идентификаторов:",
                 font=("Arial", 9)).pack(side="left")

        tk.Button(
            types_label_row, text="Все",
            command=self._select_all_types,
            font=("Arial", 8), width=8,
        ).pack(side="left", padx=(10, 2))
        tk.Button(
            types_label_row, text="Ничего",
            command=self._deselect_all_types,
            font=("Arial", 8), width=8,
        ).pack(side="left", padx=2)

        # Скроллируемая область с чекбоксами типов
        types_canvas = tk.Canvas(filters_frame, height=120, highlightthickness=0)
        types_scroll = tk.Scrollbar(
            filters_frame, orient="vertical", command=types_canvas.yview,
        )
        self._types_inner = tk.Frame(types_canvas)

        self._types_inner.bind(
            "<Configure>",
            lambda e: types_canvas.configure(scrollregion=types_canvas.bbox("all"))
        )
        types_canvas.create_window((0, 0), window=self._types_inner, anchor="nw")
        types_canvas.configure(yscrollcommand=types_scroll.set)

        types_canvas.pack(side="left", fill="both", expand=True, pady=(2, 0))
        types_scroll.pack(side="right", fill="y", pady=(2, 0))

        # Колесо мыши в области типов
        def _on_mousewheel(event):
            types_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        types_canvas.bind("<MouseWheel>", _on_mousewheel)
        self._types_inner.bind("<MouseWheel>", _on_mousewheel)

        # Кнопка поиска
        search_row = tk.Frame(filters_frame)
        search_row.pack(fill="x", pady=(8, 0))

        self._search_btn = tk.Button(
            search_row, text="🔍 Найти",
            command=self._do_search,
            bg=COLOR_INTERACT_BTN, fg="white",
            width=20, font=("Arial", 10, "bold"),
        )
        self._search_btn.pack(side="left")

        self._loading_label = tk.Label(
            search_row, text="", font=("Arial", 9), fg="gray",
        )
        self._loading_label.pack(side="left", padx=15)

    def _build_results_table(self):
        """Таблица результатов."""
        results_frame = tk.LabelFrame(
            self._win, text="Результаты", font=("Arial", 9, "bold"),
            padx=5, pady=5,
        )
        results_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        v_scroll = tk.Scrollbar(results_frame, orient=tk.VERTICAL)
        h_scroll = tk.Scrollbar(results_frame, orient=tk.HORIZONTAL)

        cols = ("id", "fio", "tab", "count", "types")
        self._tree = ttk.Treeview(
            results_frame, columns=cols, show="headings",
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set,
            selectmode="extended",  # множественный выбор через Ctrl/Shift
        )

        for col, title, w, anchor in [
            ("id",    "ID",                  60,  "center"),
            ("fio",   "ФИО",                 280, "w"),
            ("tab",   "Таб. номер",          110, "center"),
            ("count", "Кол-во ID",           80,  "center"),
            ("types", "Типы идентификаторов", 350, "w"),
        ]:
            self._tree.heading(col, text=title)
            self._tree.column(col, width=w, anchor=anchor)

        v_scroll.config(command=self._tree.yview)
        h_scroll.config(command=self._tree.xview)

        self._tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        results_frame.grid_rowconfigure(0, weight=1)
        results_frame.grid_columnconfigure(0, weight=1)

        # Двойной клик — копировать ФИО в буфер
        self._tree.bind("<Double-1>", self._on_row_double_click)

        # Статус под таблицей
        self._status_label = tk.Label(
            self._win, text="", font=("Arial", 9), fg="gray", anchor="w",
        )
        self._status_label.pack(fill="x", padx=15, pady=(2, 0))

    def _build_buttons(self):
        # Подсказка над кнопками
        hint_frame = tk.Frame(self._win)
        hint_frame.pack(fill="x", padx=10, pady=(2, 0))
        tk.Label(
            hint_frame,
            text="💡 Множественный выбор строк — Ctrl+клик / Shift+клик. "
                 "Двойной клик — копировать в буфер.",
            font=("Arial", 8), fg="gray", anchor="w",
        ).pack(side="left")

        btn_frame = tk.Frame(self._win)
        btn_frame.pack(pady=8)

        tk.Button(
            btn_frame, text="🗑 Удалить ID у выбранных",
            command=self._delete_marks_for_selected,
            bg="#f44336", fg="white", width=22,
            font=("Arial", 9),
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="🗑 Удалить ID у ВСЕХ найденных",
            command=self._delete_marks_for_all,
            bg="#d32f2f", fg="white", width=24,
            font=("Arial", 9),
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="Экспорт CSV", command=self._export_csv,
            bg=COLOR_INTERACT_BTN, fg="white", width=12,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame, text="Закрыть", command=self._win.destroy,
            bg=COLOR_CLOSE_BTN, fg="white", width=12,
        ).pack(side="left", padx=5)

    # =========================================================================
    # ЗАГРУЗКА СПРАВОЧНИКА ТИПОВ
    # =========================================================================

    def _load_password_types_async(self):
        """Загружаем типы в фоне, чтобы не блокировать UI."""
        self._loading_label.config(text="Загрузка типов идентификаторов...")
        self._search_btn.config(state="disabled")

        def worker():
            try:
                types = self._connector.get_password_types(
                    self._db_type, self._params
                )
            except Exception as e:
                types = []
                self._win.after(0, lambda: messagebox.showerror(
                    "Ошибка",
                    f"Не удалось загрузить типы идентификаторов:\n{e}"
                ))
            self._win.after(0, self._on_types_loaded, types)

        threading.Thread(target=worker, daemon=True).start()

    def _on_types_loaded(self, types: list):
        """Заполняем чекбоксы типов и запускаем первичный поиск."""
        self._password_types = types
        self._type_vars = []

        # Очищаем старые чекбоксы (если перезагружаем)
        for w in self._types_inner.winfo_children():
            w.destroy()

        if not types:
            tk.Label(
                self._types_inner,
                text="(в БД нет ни одного типа идентификатора)",
                font=("Arial", 9), fg="red",
            ).pack(anchor="w", padx=5, pady=5)
            self._loading_label.config(text="")
            self._search_btn.config(state="normal")
            return

        # 3 колонки чекбоксов
        for i, (tid, name, comment) in enumerate(types):
            var = tk.BooleanVar(value=True)
            self._type_vars.append(var)
            row = i // 3
            col = i % 3
            cb = tk.Checkbutton(
                self._types_inner,
                text=f"{name}",
                variable=var,
                font=("Arial", 9),
                anchor="w",
            )
            cb.grid(row=row, column=col, sticky="w", padx=10, pady=1)

        self._loading_label.config(text="")
        self._search_btn.config(state="normal")

        # Запускаем первичный поиск (все типы включены)
        self._do_search()

    def _select_all_types(self):
        for var in self._type_vars:
            var.set(True)

    def _deselect_all_types(self):
        for var in self._type_vars:
            var.set(False)

    # =========================================================================
    # ПОИСК
    # =========================================================================

    def _do_search(self):
        """Выполнить поиск с текущими фильтрами в фоновом потоке."""
        # Собираем выбранные типы
        selected_type_ids = []
        all_selected = True
        for i, var in enumerate(self._type_vars):
            if var.get():
                selected_type_ids.append(self._password_types[i][0])
            else:
                all_selected = False

        if not self._type_vars or all_selected:
            # Все включены или нет фильтра — передаём None
            type_filter = None
        elif not selected_type_ids:
            messagebox.showwarning(
                "Нет фильтра",
                "Не выбрано ни одного типа идентификаторов.\n"
                "Выберите хотя бы один тип или нажмите «Все»."
            )
            return
        else:
            type_filter = selected_type_ids

        name_filter = self._name_var.get().strip()

        # Очищаем таблицу и блокируем кнопку
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._results = []
        self._search_btn.config(state="disabled")
        self._loading_label.config(text="Поиск...")
        self._status_label.config(text="")

        def worker():
            try:
                results = self._connector.find_persons_by_mark_type(
                    self._db_type, self._params,
                    type_ids=type_filter,
                    name_filter=name_filter,
                )
            except Exception as e:
                results = [{'error': str(e)}]
            self._win.after(0, self._on_search_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_search_done(self, results: list):
        """Заполняем таблицу результатами."""
        self._search_btn.config(state="normal")
        self._loading_label.config(text="")

        # Проверяем на ошибку
        if results and isinstance(results[0], dict) and 'error' in results[0]:
            messagebox.showerror(
                "Ошибка поиска",
                f"Не удалось выполнить поиск:\n{results[0]['error']}"
            )
            self._status_label.config(text="Ошибка поиска", fg="red")
            return

        self._results = results

        for r in results:
            self._tree.insert(
                "", "end",
                values=(
                    r['person_id'],
                    r['full_name'],
                    r['tab_number'],
                    r['mark_count'],
                    r['type_names'],
                ),
            )

        # Статус
        count = len(results)
        if count >= 5000:
            self._status_label.config(
                text=f"Найдено: 5000+ (показаны первые 5000 — уточните фильтр)",
                fg="orange",
            )
        else:
            self._status_label.config(
                text=f"Найдено: {count:,}".replace(",", " "),
                fg="gray",
            )

    # =========================================================================
    # ДЕЙСТВИЯ С РЕЗУЛЬТАТАМИ
    # =========================================================================

    def _on_row_double_click(self, event):
        """Копировать ФИО + ID выбранной строки в буфер обмена."""
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if not values:
            return
        text = f"{values[0]}\t{values[1]}\t{values[2]}"
        self._win.clipboard_clear()
        self._win.clipboard_append(text)
        self._status_label.config(
            text=f"Скопировано в буфер: {values[1]}", fg="#4CAF50"
        )

    # =========================================================================
    # УДАЛЕНИЕ ИДЕНТИФИКАТОРОВ
    # =========================================================================

    def _delete_marks_for_selected(self):
        """Удалить идентификаторы у выделенных строк (Ctrl+клик / Shift+клик)."""
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo(
                "Нет выбора",
                "Выделите одну или несколько строк (Ctrl+клик / Shift+клик)."
            )
            return

        # Собираем ID + краткое описание для подтверждения
        person_ids = []
        for item_id in sel:
            values = self._tree.item(item_id, "values")
            if values:
                try:
                    person_ids.append(int(values[0]))
                except (ValueError, TypeError):
                    continue

        if not person_ids:
            return

        self._do_delete_marks(person_ids, scope_label="выбранных")

    def _delete_marks_for_all(self):
        """Удалить идентификаторы у ВСЕХ найденных в текущем результате."""
        if not self._results:
            messagebox.showinfo("Нет данных", "Сначала выполните поиск.")
            return

        person_ids = [r['person_id'] for r in self._results
                       if isinstance(r, dict) and 'person_id' in r]
        if not person_ids:
            return

        self._do_delete_marks(person_ids, scope_label="ВСЕХ найденных")

    def _do_delete_marks(self, person_ids: list, scope_label: str):
        """
        Общая логика удаления — спрашивает подтверждение, выполняет
        удаление в фоновом потоке, обновляет таблицу.

        Если в фильтре «Типы идентификаторов» выбрана НЕ ВСЯ галочка —
        удаляются только выбранные типы. Иначе — все идентификаторы.
        """
        # Определяем фильтр по типам
        type_filter = None
        if self._type_vars:
            selected_type_ids = [
                self._password_types[i][0]
                for i, var in enumerate(self._type_vars) if var.get()
            ]
            all_selected = all(var.get() for var in self._type_vars)
            if not all_selected and selected_type_ids:
                type_filter = selected_type_ids

        # Формируем подтверждение
        count = len(person_ids)
        if type_filter:
            type_names_selected = [
                self._password_types[i][1]
                for i, var in enumerate(self._type_vars) if var.get()
            ]
            type_info = (f"Удалятся только идентификаторы типов:\n"
                         f"  • " + "\n  • ".join(type_names_selected))
        else:
            type_info = "Удалятся ВСЕ идентификаторы (любых типов)."

        confirm = messagebox.askyesno(
            "Подтверждение удаления",
            f"Удалить идентификаторы у {scope_label} сотрудников?\n\n"
            f"Количество сотрудников: {count:,}\n\n".replace(",", " ")
            + type_info + "\n\n"
            f"⚠ Это действие НЕОБРАТИМО.\n"
            f"Записи в таблице pMark будут удалены безвозвратно.",
            icon='warning',
        )
        if not confirm:
            return

        # Дополнительное подтверждение для большого количества
        if count > 100:
            confirm2 = messagebox.askyesno(
                "Большое количество!",
                f"Будет удалено идентификаторов у {count:,} сотрудников.\n"
                f"Точно продолжить?".replace(",", " "),
                icon='warning',
            )
            if not confirm2:
                return

        # Запускаем в фоновом потоке (может быть долго при большом count)
        self._search_btn.config(state="disabled")
        self._loading_label.config(text="Удаление...")

        def worker():
            result = self._connector.delete_marks_for_persons(
                self._db_type, self._params,
                person_ids,
                type_ids=type_filter,
            )
            self._win.after(0, self._on_delete_done, result)

        threading.Thread(target=worker, daemon=True).start()

    def _on_delete_done(self, result: dict):
        """Завершение удаления — показываем результат и обновляем таблицу."""
        self._search_btn.config(state="normal")
        self._loading_label.config(text="")

        if result.get('error'):
            messagebox.showerror(
                "Ошибка удаления",
                f"Не удалось выполнить удаление:\n{result['error']}"
            )
            return

        deleted = result.get('deleted', 0)
        messagebox.showinfo(
            "Готово",
            f"Удалено записей в pMark: {deleted:,}".replace(",", " ")
        )
        # Перезапускаем поиск, чтобы таблица обновилась
        self._do_search()

    def _export_csv(self):
        """Экспорт результатов в CSV."""
        if not self._results:
            messagebox.showinfo("Нет данных", "Сначала выполните поиск.")
            return

        filepath = filedialog.asksaveasfilename(
            title="Сохранить результаты",
            defaultextension=".csv",
            initialfile=f"persons_{self._db_name}.csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not filepath:
            return

        try:
            with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
                # utf-8-sig — чтобы Excel правильно открыл кириллицу
                writer = csv.writer(f, delimiter=';')
                writer.writerow([
                    'ID', 'ФИО', 'Табельный номер',
                    'Количество идентификаторов', 'Типы идентификаторов'
                ])
                for r in self._results:
                    writer.writerow([
                        r['person_id'],
                        r['full_name'],
                        r['tab_number'],
                        r['mark_count'],
                        r['type_names'],
                    ])
            messagebox.showinfo("Успех", f"Сохранено: {filepath}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{str(e)}")
