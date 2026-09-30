# ==============================================================================
# views/mass_add_view.py — Окно массового добавления элементов
# ==============================================================================
# КЛЮЧЕВЫЕ ОСОБЕННОСТИ:
#
#   1. Поле ВВОДА количества (вместо выпадающего списка), макс. 10 000 000.
#   2. Быстрая batch-вставка через executemany/execute_values.
#   3. Работа в ФОНОВОМ потоке, GUI отзывчив.
#   4. Прогресс UI обновляется с троттлингом (раз в N записей).
#   5. Кнопка «Стоп» — мягкая остановка.
#   6. Для фото — предупреждение о замедлении.
#
#   7. Секция «Идентификаторы»:
#      - Чекбокс «Выдать идентификатор всем созданным сотрудникам»
#      - Combobox с типами идентификаторов из pTypePasswords
#      - Combobox с уровнями доступа из dbo.Groups
#        (по умолчанию ищется группа с именем «Максимум»)
#      - Дата начала действия (по умолчанию сегодня)
#      - Дата окончания (по умолчанию +1 год)
# ==============================================================================

import os
import sys
import threading
import time
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk, messagebox

from constants import COLOR_CONNECT_BTN, COLOR_CLOSE_BTN, DB_TYPE_POSTGRES
from ui_widgets import center_window
from person_generator import generate_person_data, generate_mark_code


_MAX_COUNT = 10_000_000
_UI_UPDATE_EVERY_N = 1000

# Названия групп доступа в Орионе, которые соответствуют «полному доступу».
# Когда пользователь открывает окно — мы ищем группу с одним из этих имён
# и подставляем её в Combobox по умолчанию.
_DEFAULT_GROUP_NAMES = ('максимум', 'максимальный', 'полный', 'full', 'admin')


class MassAddView:
    """Окно «Массовое добавление элементов»."""

    def __init__(self, parent, connector, db_type, params):
        self._parent = parent
        self._connector = connector
        self._db_type = db_type
        self._params = params

        self._stop_flag = False
        self._worker_thread = None
        self._start_time = None

        # Кэш справочников.
        # _all_types — единый список ВСЕХ типов идентификаторов:
        #   list[(id, name, kind)]
        #   kind: 'normal' для типов из pTypePasswords;
        #         'finger'/'face'/'palm'/'photo'/'qr_bio'/'bio' — биометрия из
        #         pBioTypePasswords.
        # Combobox показывает их вперемешку с маркером в имени.
        self._all_types = []
        self._access_groups = []    # list[(id, name, comment)]

        # --- Окно ---
        self._win = tk.Toplevel(parent)
        self._win.title("Массовое добавление элементов")
        self._win.geometry("600x720")
        self._win.resizable(False, False)
        center_window(self._win, parent)
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        tk.Label(
            self._win, text="Массовое добавление элементов",
            font=("Arial", 12, "bold"),
        ).pack(pady=(10, 5))

        form = tk.Frame(self._win)
        form.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)

        self._build_basic_section(form)
        self._build_photo_section(form)
        self._build_marks_section(form)
        self._build_progress_section(form)
        self._build_buttons()

        # Загружаем справочники
        self._load_password_types()
        self._load_access_groups()

    # =========================================================================
    # ПОСТРОЕНИЕ ИНТЕРФЕЙСА
    # =========================================================================

    def _build_basic_section(self, parent):
        """Тип элемента + количество."""
        tk.Label(parent, text="Тип элемента:",
                 font=("Arial", 9)).grid(row=0, column=0, sticky="e", pady=5)
        self._element_type = ttk.Combobox(
            parent, values=["Персонал"], state="readonly", width=30,
        )
        self._element_type.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self._element_type.current(0)

        tk.Label(parent, text="Количество:",
                 font=("Arial", 9)).grid(row=1, column=0, sticky="e", pady=5)
        vcmd = (self._win.register(self._validate_count), '%P')
        self._count_var = tk.StringVar(value="1000")
        self._count_entry = tk.Entry(
            parent, textvariable=self._count_var, width=32,
            validate="key", validatecommand=vcmd,
            font=("Arial", 10),
        )
        self._count_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")

        tk.Label(
            parent,
            text=f"(от 1 до {_MAX_COUNT:,})".replace(",", " "),
            font=("Arial", 8), fg="gray",
        ).grid(row=2, column=1, sticky="w", padx=5)

    def _build_photo_section(self, parent):
        """Чекбокс «Добавить фотографии»."""
        self._add_photos_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent, text="Добавить фотографии",
            variable=self._add_photos_var, font=("Arial", 9),
            command=self._on_photos_toggle,
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=5, pady=(15, 2))

        self._photo_warning = tk.Label(
            parent,
            text="⚠ С фотографиями добавление в ~10 раз медленнее",
            font=("Arial", 8), fg="#FF9800",
        )
        self._photo_warning.grid(row=4, column=0, columnspan=2,
                                  sticky="w", padx=25)
        self._photo_warning.grid_remove()

    def _build_marks_section(self, parent):
        """Секция «Идентификаторы»."""
        ttk.Separator(parent, orient="horizontal").grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(15, 5)
        )

        tk.Label(
            parent, text="Идентификаторы (записи в pMark):",
            font=("Arial", 9, "bold"),
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=5)

        self._add_marks_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            parent, text="Выдать идентификатор всем созданным сотрудникам",
            variable=self._add_marks_var, font=("Arial", 9),
            command=self._on_marks_toggle,
        ).grid(row=7, column=0, columnspan=2, sticky="w", padx=5, pady=(2, 2))

        # Тип идентификатора
        tk.Label(parent, text="Тип идентификатора:",
                 font=("Arial", 9)).grid(row=8, column=0, sticky="e", pady=5)
        self._mark_type_combo = ttk.Combobox(
            parent, state="disabled", width=30,
        )
        self._mark_type_combo.grid(row=8, column=1, padx=5, pady=5, sticky="w")

        self._mark_type_hint = tk.Label(
            parent, text="",
            font=("Arial", 8), fg="gray", wraplength=380, justify="left",
        )
        self._mark_type_hint.grid(row=9, column=0, columnspan=2,
                                   sticky="w", padx=25)
        self._mark_type_combo.bind("<<ComboboxSelected>>", self._on_mark_type_change)

        # Уровень доступа
        tk.Label(parent, text="Уровень доступа:",
                 font=("Arial", 9)).grid(row=10, column=0, sticky="e", pady=5)
        self._access_combo = ttk.Combobox(
            parent, state="disabled", width=30,
        )
        self._access_combo.grid(row=10, column=1, padx=5, pady=5, sticky="w")

        # Срок действия — Start
        tk.Label(parent, text="Действует с:",
                 font=("Arial", 9)).grid(row=11, column=0, sticky="e", pady=5)
        date_start_frame = tk.Frame(parent)
        date_start_frame.grid(row=11, column=1, padx=5, pady=5, sticky="w")
        self._start_date_var = tk.StringVar(
            value=datetime.now().strftime("%d.%m.%Y")
        )
        self._start_date_entry = tk.Entry(
            date_start_frame, textvariable=self._start_date_var,
            width=12, state="disabled", font=("Arial", 10),
        )
        self._start_date_entry.pack(side="left")
        tk.Label(date_start_frame, text=" (дд.мм.гггг)",
                 font=("Arial", 8), fg="gray").pack(side="left")

        # Срок действия — Finish
        tk.Label(parent, text="Действует по:",
                 font=("Arial", 9)).grid(row=12, column=0, sticky="e", pady=5)
        date_finish_frame = tk.Frame(parent)
        date_finish_frame.grid(row=12, column=1, padx=5, pady=5, sticky="w")
        self._finish_date_var = tk.StringVar(
            value=(datetime.now() + timedelta(days=365)).strftime("%d.%m.%Y")
        )
        self._finish_date_entry = tk.Entry(
            date_finish_frame, textvariable=self._finish_date_var,
            width=12, state="disabled", font=("Arial", 10),
        )
        self._finish_date_entry.pack(side="left")
        tk.Label(date_finish_frame, text=" (по умолчанию +1 год)",
                 font=("Arial", 8), fg="gray").pack(side="left")

    def _build_progress_section(self, parent):
        """Прогресс и статус."""
        ttk.Separator(parent, orient="horizontal").grid(
            row=13, column=0, columnspan=2, sticky="ew", pady=(15, 5)
        )

        tk.Label(parent, text="Прогресс:",
                 font=("Arial", 9)).grid(row=14, column=0, sticky="e", pady=5)
        self._progress = ttk.Progressbar(parent, length=380, mode='determinate')
        self._progress.grid(row=14, column=1, padx=5, pady=5, sticky="w")

        self._status_label = tk.Label(
            parent, text="Готов к работе",
            font=("Arial", 9), fg="gray",
        )
        self._status_label.grid(row=15, column=0, columnspan=2, pady=(5, 2))

        self._speed_label = tk.Label(
            parent, text="",
            font=("Arial", 8), fg="gray",
        )
        self._speed_label.grid(row=16, column=0, columnspan=2)

    def _build_buttons(self):
        btn_frame = tk.Frame(self._win)
        btn_frame.pack(pady=(5, 15))

        self._create_btn = tk.Button(
            btn_frame, text="Создать", command=self._on_create,
            bg=COLOR_CONNECT_BTN, fg="white", width=15,
        )
        self._create_btn.pack(side="left", padx=10)

        self._stop_btn = tk.Button(
            btn_frame, text="Стоп", command=self._on_stop,
            bg="#FF9800", fg="white", width=15, state="disabled",
        )
        self._stop_btn.pack(side="left", padx=10)

        self._cancel_btn = tk.Button(
            btn_frame, text="Закрыть", command=self._on_close,
            bg=COLOR_CLOSE_BTN, fg="white", width=15,
        )
        self._cancel_btn.pack(side="left", padx=10)

    # =========================================================================
    # ОБРАБОТЧИКИ UI
    # =========================================================================

    def _validate_count(self, new_value: str) -> bool:
        if new_value == "":
            return True
        if not new_value.isdigit():
            return False
        try:
            n = int(new_value)
        except ValueError:
            return False
        return 1 <= n <= _MAX_COUNT

    def _on_photos_toggle(self):
        if self._add_photos_var.get():
            self._photo_warning.grid()
        else:
            self._photo_warning.grid_remove()

    def _on_marks_toggle(self):
        """Активировать/деактивировать всю секцию идентификаторов."""
        enabled = self._add_marks_var.get()

        if enabled:
            if not self._all_types:
                self._load_password_types()
                if not self._all_types:
                    messagebox.showwarning(
                        "Нет типов идентификаторов",
                        "В таблицах pTypePasswords и pBioTypePasswords нет записей."
                    )
                    self._add_marks_var.set(False)
                    return

            if not self._access_groups:
                self._load_access_groups()
                if not self._access_groups:
                    messagebox.showwarning(
                        "Нет уровней доступа",
                        "В таблице dbo.Groups нет записей.\n"
                        "Невозможно назначить уровень доступа."
                    )
                    self._add_marks_var.set(False)
                    return

            self._mark_type_combo.config(state="readonly")
            if self._mark_type_combo.current() < 0:
                self._mark_type_combo.current(0)
                self._on_mark_type_change()

            self._access_combo.config(state="readonly")
            if self._access_combo.current() < 0:
                default_idx = self._find_default_access_group_index()
                self._access_combo.current(default_idx)

            self._start_date_entry.config(state="normal")
            self._finish_date_entry.config(state="normal")
        else:
            self._mark_type_combo.config(state="disabled")
            self._access_combo.config(state="disabled")
            self._start_date_entry.config(state="disabled")
            self._finish_date_entry.config(state="disabled")
            self._mark_type_hint.config(text="")

    def _on_mark_type_change(self, event=None):
        """Подсказка по выбранному типу."""
        idx = self._mark_type_combo.current()
        if idx < 0 or idx >= len(self._all_types):
            return
        type_id, name, kind = self._all_types[idx]

        # Подсказка зависит от вида:
        if kind == 'normal':
            hint = f"ID типа: {type_id} (обычный идентификатор)"
            fg = "gray"
        elif kind == 'finger':
            hint = ("🖐 Биометрия: отпечаток пальца. "
                    "Шаблон записывается в pMark.fingertemplate.")
            fg = "#9C27B0"
        elif kind == 'face':
            hint = ("😊 Биометрия: лицо. Шаблон записывается в pBioAccess "
                    "(связан с pMark через ID_pMark).")
            fg = "#9C27B0"
        elif kind == 'palm':
            hint = ("🖐 Биометрия: ладонь. Шаблон записывается в pBioAccess.")
            fg = "#9C27B0"
        elif kind == 'photo':
            hint = ("📷 Биометрия: фото. Шаблон записывается в pBioAccess.")
            fg = "#9C27B0"
        elif kind == 'qr_bio':
            hint = "Биометрический QR. Шаблон записывается в pBioAccess."
            fg = "#9C27B0"
        else:
            hint = f"Тип ID: {type_id} ({kind})"
            fg = "gray"

        self._mark_type_hint.config(text=hint, fg=fg)

    def _find_default_access_group_index(self) -> int:
        """
        Найти индекс группы по умолчанию: ищем «Максимум» (case-insensitive).
        Если не находим — берём последнюю.
        """
        for i, (_gid, name, _comment) in enumerate(self._access_groups):
            name_lower = (name or '').strip().lower()
            for default_name in _DEFAULT_GROUP_NAMES:
                if default_name in name_lower:
                    return i
        return len(self._access_groups) - 1

    def _load_password_types(self):
        """
        Загрузить ОБА справочника: pTypePasswords + pBioTypePasswords.
        Объединяет их в self._all_types: list[(id, name, kind)].
        Combobox показывает оба списка с маркером.
        """
        # Обычные типы
        normal_types = []
        try:
            rows = self._connector.get_password_types(
                self._db_type, self._params
            )
            for tid, name, _comment in rows:
                normal_types.append((tid, name, 'normal'))
        except Exception as e:
            print(f"Ошибка загрузки pTypePasswords: {e}")

        # Биометрические типы
        bio_types = []
        try:
            bio_rows = self._connector.get_bio_password_types(
                self._db_type, self._params
            )
            # bio_rows: list[(id, name, kind)]
            for bid, name, kind in bio_rows:
                bio_types.append((bid, name, kind))
        except Exception as e:
            print(f"Ошибка загрузки pBioTypePasswords: {e}")

        # Объединяем: сначала обычные, потом биометрия
        self._all_types = normal_types + bio_types

        if self._all_types:
            display_values = []
            for tid, name, kind in self._all_types:
                if kind == 'normal':
                    label = f"{name}  (ID={tid})"
                else:
                    # Биометрию помечаем эмоджи + словом, чтобы пользователь
                    # сразу видел отличие
                    icons = {
                        'finger': '🖐',
                        'face':   '😊',
                        'palm':   '🖐',
                        'photo':  '📷',
                        'qr_bio': '⌗',
                        'bio':    '◆',
                    }
                    icon = icons.get(kind, '◆')
                    label = f"{icon} {name}  (БИО, ID={tid})"
                display_values.append(label)
            self._mark_type_combo['values'] = display_values

    def _load_access_groups(self):
        try:
            self._access_groups = self._connector.get_groups(
                self._db_type, self._params
            )
        except Exception as e:
            self._access_groups = []
            print(f"Ошибка загрузки групп: {e}")
            return

        if self._access_groups:
            display_values = [
                f"{name}  (ID={gid})"
                for gid, name, _c in self._access_groups
            ]
            self._access_combo['values'] = display_values

    # =========================================================================
    # ВАЛИДАЦИЯ ДАТ
    # =========================================================================

    def _parse_date(self, text: str, label: str):
        """
        Распарсить дату формата дд.мм.гггг. Возвращает datetime или None
        (если строка пустая). Бросает ValueError при ошибке формата.
        """
        text = (text or '').strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%d.%m.%Y")
        except ValueError:
            raise ValueError(
                f"Неверный формат даты «{label}»: '{text}'\n"
                f"Используйте формат дд.мм.гггг (например, 31.12.2025)"
            )

    # =========================================================================
    # ЗАПУСК ОПЕРАЦИИ
    # =========================================================================

    def _on_create(self):
        # Парсим количество
        raw = self._count_var.get().strip()
        if not raw:
            messagebox.showerror("Ошибка", "Введите количество.")
            return
        try:
            count = int(raw)
        except ValueError:
            messagebox.showerror("Ошибка", "Количество должно быть числом.")
            return

        if count < 1 or count > _MAX_COUNT:
            messagebox.showerror(
                "Ошибка",
                f"Количество должно быть от 1 до {_MAX_COUNT:,}".replace(",", " ")
            )
            return

        # Загрузка фото
        photo_files = []
        if self._add_photos_var.get():
            photo_files = self._load_photos()
            if not photo_files:
                messagebox.showwarning(
                    "Предупреждение",
                    "Фотографии не найдены в папке personnel_photos!\n"
                    "Добавление продолжится без фотографий."
                )

        # Параметры идентификаторов
        marks_config = None
        if self._add_marks_var.get():
            t_idx = self._mark_type_combo.current()
            if t_idx < 0 or t_idx >= len(self._all_types):
                messagebox.showerror("Ошибка", "Выберите тип идентификатора.")
                return

            a_idx = self._access_combo.current()
            if a_idx < 0 or a_idx >= len(self._access_groups):
                messagebox.showerror("Ошибка", "Выберите уровень доступа.")
                return

            # Парсим даты
            try:
                start_date = self._parse_date(
                    self._start_date_var.get(), "Действует с"
                )
                finish_date = self._parse_date(
                    self._finish_date_var.get(), "Действует по"
                )
            except ValueError as e:
                messagebox.showerror("Ошибка даты", str(e))
                return

            if start_date and finish_date and finish_date < start_date:
                messagebox.showerror(
                    "Ошибка дат",
                    "Дата окончания не может быть раньше даты начала."
                )
                return

            type_id, type_name, type_kind = self._all_types[t_idx]
            group_id, group_name, _c = self._access_groups[a_idx]

            marks_config = {
                'type_id': type_id,
                'type_name': type_name,
                'type_kind': type_kind,  # 'normal' | 'finger' | 'face' | ...
                'group_id': group_id,
                'group_name': group_name,
                'start_date': start_date,
                'finish_date': finish_date,
            }

        # UI в режим работы
        self._stop_flag = False
        self._start_time = time.time()
        self._create_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._cancel_btn.config(state="disabled")
        self._element_type.config(state="disabled")
        self._count_entry.config(state="disabled")
        self._mark_type_combo.config(state="disabled")
        self._access_combo.config(state="disabled")
        self._start_date_entry.config(state="disabled")
        self._finish_date_entry.config(state="disabled")
        self._progress['value'] = 0
        self._status_label.config(text="Запуск...", fg="blue")
        self._speed_label.config(text="")

        self._worker_thread = threading.Thread(
            target=self._worker,
            args=(count, photo_files, marks_config),
            daemon=True,
        )
        self._worker_thread.start()

    def _on_stop(self):
        self._stop_flag = True
        self._status_label.config(text="Останавливаюсь...", fg="orange")
        self._stop_btn.config(state="disabled")

    def _on_close(self):
        if self._worker_thread and self._worker_thread.is_alive():
            if not messagebox.askyesno(
                "Закрыть окно?",
                "Идёт массовое добавление. Прервать и закрыть окно?"
            ):
                return
            self._stop_flag = True
        self._win.destroy()

    # =========================================================================
    # ФОНОВЫЙ WORKER
    # =========================================================================

    def _worker(self, count: int, photo_files: list, marks_config: dict):
        try:
            start_person_id = self._get_next_person_id()
        except Exception as e:
            self._win.after(0, self._on_worker_error,
                            f"Не удалось получить стартовый ID: {e}")
            return

        def data_iter():
            for i in range(1, count + 1):
                if self._stop_flag:
                    break
                person = generate_person_data(i)
                if photo_files:
                    photo_index = (i - 1) % len(photo_files)
                    if photo_files[photo_index]:
                        person['picture'] = photo_files[photo_index]
                yield person

        last_ui_update_done = [0]
        phase = ['Сотрудники']

        def progress_callback(success, errors, last_error):
            done = success + errors
            if done - last_ui_update_done[0] >= _UI_UPDATE_EVERY_N or done == count:
                last_ui_update_done[0] = done
                pct = (done / count) * 100 if count > 0 else 0
                elapsed = time.time() - self._start_time
                speed = done / elapsed if elapsed > 0 else 0
                self._win.after(
                    0, self._update_progress,
                    pct, success, errors, speed, phase[0]
                )

        # Этап 1: персонал
        try:
            persons_result = self._connector.batch_add_persons(
                self._db_type, self._params,
                data_iter(),
                progress_callback=progress_callback,
                should_stop=lambda: self._stop_flag,
            )
        except Exception as e:
            self._win.after(0, self._on_worker_error, str(e))
            return

        if not marks_config or persons_result['stopped'] or persons_result['success'] == 0:
            self._win.after(0, self._on_worker_done,
                            persons_result, count, None)
            return

        # Этап 2: идентификаторы
        added_person_ids = list(range(
            start_person_id,
            start_person_id + persons_result['success'] + persons_result['errors']
        ))

        phase[0] = 'Идентификаторы'
        last_ui_update_done[0] = 0
        marks_total = len(added_person_ids)

        type_id = marks_config['type_id']
        type_name = marks_config['type_name']
        type_kind = marks_config.get('type_kind', 'normal')

        def code_gen(person_id, t_id):
            return generate_mark_code(person_id, t_id, type_name)

        def marks_progress(success, errors, last_error):
            done = success + errors
            if done - last_ui_update_done[0] >= _UI_UPDATE_EVERY_N or done == marks_total:
                last_ui_update_done[0] = done
                pct = (done / marks_total) * 100 if marks_total > 0 else 0
                elapsed = time.time() - self._start_time
                speed = done / elapsed if elapsed > 0 else 0
                self._win.after(
                    0, self._update_progress,
                    pct, success, errors, speed, 'Идентификаторы'
                )

        try:
            marks_result = self._connector.batch_add_marks(
                self._db_type, self._params,
                added_person_ids, type_id, code_gen,
                group_id=marks_config['group_id'],
                start_date=marks_config['start_date'],
                finish_date=marks_config['finish_date'],
                type_kind=type_kind,  # для биометрии — пишем в pBioAccess
                progress_callback=marks_progress,
                should_stop=lambda: self._stop_flag,
            )
        except Exception as e:
            self._win.after(0, self._on_worker_done,
                            persons_result, count,
                            {'success': 0, 'errors': marks_total,
                             'last_error': str(e), 'stopped': False})
            return

        self._win.after(0, self._on_worker_done,
                        persons_result, count, marks_result)

    def _get_next_person_id(self) -> int:
        """Получить следующий доступный ID для pList."""
        conn = self._connector._create_connection(self._db_type, self._params)
        try:
            cur = conn.cursor()
            if self._db_type == DB_TYPE_POSTGRES:
                cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM dbo.plist')
            else:
                cur.execute("SELECT ISNULL(MAX(ID), 0) + 1 FROM pList")
            next_id = cur.fetchone()[0]
            cur.close()
            return next_id
        finally:
            conn.close()

    # =========================================================================
    # ОБНОВЛЕНИЯ UI
    # =========================================================================

    def _update_progress(self, pct, success, errors, speed, phase=''):
        self._progress['value'] = pct
        phase_prefix = f"[{phase}] " if phase else ""
        self._status_label.config(
            text=(f"{phase_prefix}Добавлено: {success:,}, "
                  f"Ошибок: {errors:,}").replace(",", " "),
            fg="blue",
        )
        if speed > 0:
            self._speed_label.config(
                text=f"Скорость: {int(speed):,} зап/сек".replace(",", " ")
            )

    def _on_worker_error(self, error_msg):
        self._restore_ui()
        self._status_label.config(text="Ошибка!", fg="red")
        messagebox.showerror("Ошибка", f"Произошла ошибка:\n{error_msg}")

    def _on_worker_done(self, persons_result, count, marks_result):
        self._restore_ui()
        elapsed = time.time() - self._start_time

        p_success = persons_result['success']
        p_errors = persons_result['errors']
        p_last_err = persons_result['last_error']
        stopped = persons_result['stopped']

        msg_parts = [
            f"Сотрудники: добавлено {p_success:,}, ошибок {p_errors:,}".replace(",", " ")
        ]

        if marks_result is not None:
            m_success = marks_result['success']
            m_errors = marks_result['errors']
            m_last_err = marks_result['last_error']
            msg_parts.append(
                f"Идентификаторы: добавлено {m_success:,}, "
                f"ошибок {m_errors:,}".replace(",", " ")
            )
            if m_last_err and not p_last_err:
                p_last_err = m_last_err

        msg_parts.append(f"Время: {elapsed:.1f}с")

        if p_success > 0 and elapsed > 0:
            speed = p_success / elapsed
            msg_parts.append(
                f"Скорость персонала: {int(speed):,} зап/сек".replace(",", " ")
            )

        msg = "\n".join(msg_parts)
        if p_last_err:
            msg += f"\n\nПоследняя ошибка:\n{p_last_err}"

        if stopped:
            title = "Прервано"
            self._status_label.config(
                text=f"Прервано (добавлено: {p_success:,})".replace(",", " "),
                fg="orange",
            )
        else:
            title = "Завершено"
            self._status_label.config(
                text=f"Готово: {p_success:,}".replace(",", " "),
                fg="green",
            )

        self._speed_label.config(text=f"Завершено за {elapsed:.1f}с")
        messagebox.showinfo(title, msg)

    def _restore_ui(self):
        self._create_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._element_type.config(state="readonly")
        self._count_entry.config(state="normal")
        if self._add_marks_var.get():
            self._mark_type_combo.config(state="readonly")
            self._access_combo.config(state="readonly")
            self._start_date_entry.config(state="normal")
            self._finish_date_entry.config(state="normal")

    # =========================================================================
    # ЗАГРУЗКА ФОТО
    # =========================================================================

    def _load_photos(self) -> list:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )

        photos_dir = os.path.join(base_dir, 'personnel_photos')

        photo_files = []
        for i in range(1, 6):
            for ext in ('.jpg', '.jpeg', '.png', '.bmp'):
                photo_path = os.path.join(photos_dir, f"person_{i}{ext}")
                if os.path.exists(photo_path):
                    try:
                        with open(photo_path, 'rb') as f:
                            photo_files.append(f.read())
                        break
                    except Exception:
                        photo_files.append(None)
                        break

        return photo_files if any(photo_files) else []
