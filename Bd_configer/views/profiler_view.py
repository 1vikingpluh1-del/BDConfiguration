# ==============================================================================
# views/profiler_view.py — Окно мониторинга запросов к БД
# ==============================================================================
# MSSQL: Extended Events (XEvents) — настоящий Profiler, видит все завершённые
#        запросы с точностью до миллисекунды. При нехватке прав на XEvents
#        автоматически переключается в DMV-fallback режим (видны только
#        активные сейчас запросы).
# PostgreSQL: pg_stat_activity (видны активные запросы).
#
# Особенности UI:
#   - Таблица (Treeview) с колонками: Время, Операция, Таблица, Логин,
#     Хост, Длит. (мс), Reads, Writes, Строк, SPID
#   - При клике на строку — внизу показывается ПОЛНЫЙ SQL-текст запроса
#   - Дедупликация: каждое событие показывается только один раз
#     (по уникальному ключу, который формирует DBConnector)
#
# ВАЖНО: при использовании XEvents в БД создаётся event session. При закрытии
# окна сессия обязательно удаляется (drop). Если программа упадёт без грации —
# при следующем старте «осиротевшие» сессии будут найдены и удалены автоматически.
# ==============================================================================

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import threading
import queue
import re
import time
from collections import OrderedDict

from constants import COLOR_INTERACT_BTN, COLOR_CLOSE_BTN
from ui_widgets import center_window

# Сколько событий держим в таблице. При превышении — удаляем самые старые.
_MAX_EVENTS_IN_TABLE = 5000

# Максимальный размер кэша seen_keys (защита от утечки памяти)
_MAX_SEEN_KEYS_CACHE = 20000

# Цветовая схема операций (foreground для строки в таблице)
_OPERATION_COLORS = {
    'SELECT': '#9cdcfe',
    'INSERT': '#4ec9b0',
    'UPDATE': '#dcdcaa',
    'DELETE': '#f44747',
    'MERGE': '#c586c0',
    'EXEC': '#ffa07a',
    'ROLLBACK': '#f44747',
    'COMMIT': '#4fc1ff',
    'BEGIN': '#c586c0',
}


class ProfilerView:
    """Окно профайлера активных запросов (DMV-режим)."""

    def __init__(self, parent, connector, db_type, params, db_name):
        self._connector = connector
        self._db_type = db_type
        self._params = params
        self._db_name = db_name

        self._is_running = False
        # Используем OrderedDict для LRU-кэша с ограничением по размеру
        self._seen_keys = OrderedDict()
        self._total_events = 0
        self._last_marker = None  # Исправлено: None вместо '0'

        # Очередь для безопасной передачи данных из потока
        self._poll_queue = queue.Queue()
        self._background_thread = None

        # XEvents (MSSQL) / pgsm / pgss (PostgreSQL): имя event session и режим.
        # mode: 'xevents' / 'dmv' / 'pgsm' / 'pgss' / 'pg_activity' / 'none'
        self._session_name = ''
        self._mode = 'none'

        # Кэш всех событий для фильтрации и экспорта
        self._events_cache = []

        # --- Окно ---
        self._win = tk.Toplevel(parent)
        self._win.title(f"Profiler — {db_name} ({db_type})")
        self._win.geometry("1200x700")
        self._win.resizable(True, True)
        center_window(self._win, parent)
        self._win.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_control_panel()
        self._build_events_table()
        self._build_sql_panel()
        self._build_buttons()

    # -------------------------------------------------------------------------

    def _build_control_panel(self):
        # --- ВЕРХНЯЯ СТРОКА: основные элементы управления ---
        ctrl = tk.Frame(self._win)
        ctrl.pack(fill="x", padx=10, pady=(5, 0))

        # Чекбокс автопрокрутки
        self._auto_scroll = tk.BooleanVar(value=True)
        tk.Checkbutton(
            ctrl, text="Автопрокрутка к новым",
            variable=self._auto_scroll, font=("Arial", 9),
        ).pack(side="left", padx=(0, 15))

        # Фильтр по операциям
        tk.Label(ctrl, text="Фильтр:", font=("Arial", 9)).pack(side="left", padx=(0, 5))
        self._filter_var = tk.StringVar(value="Все")
        filter_combo = ttk.Combobox(
            ctrl, textvariable=self._filter_var,
            values=["Все", "SELECT", "INSERT", "UPDATE", "DELETE", "EXEC"],
            state="readonly", width=10,
        )
        filter_combo.pack(side="left", padx=(0, 15))
        self._filter_var.trace_add('write', self._apply_filter)

        # Чекбокс «Скрыть мусорные запросы»
        # Прячет служебные команды (BEGIN/COMMIT/SET/SHOW/sp_reset_connection и т.п.),
        # которые засоряют логи и не несут полезной информации о работе приложения.
        self._hide_noise = tk.BooleanVar(value=True)
        tk.Checkbutton(
            ctrl, text="Скрыть служебные (BEGIN/COMMIT/SET…)",
            variable=self._hide_noise, font=("Arial", 9),
        ).pack(side="left", padx=(0, 15))
        self._hide_noise.trace_add('write', self._apply_filter)

        # Счётчик и статус
        self._event_count_label = tk.Label(
            ctrl, text="Событий: 0", font=("Arial", 9), fg="gray",
        )
        self._event_count_label.pack(side="right", padx=10)

        self._status_label = tk.Label(
            ctrl, text="⏸ Остановлен",
            font=("Arial", 9, "bold"), fg="red",
        )
        self._status_label.pack(side="right", padx=10)

        # --- НИЖНЯЯ СТРОКА: строка поиска ---
        search = tk.Frame(self._win)
        search.pack(fill="x", padx=10, pady=(2, 5))

        tk.Label(search, text="🔍 Поиск:", font=("Arial", 9)).pack(side="left", padx=(0, 5))
        self._search_var = tk.StringVar(value="")
        self._search_entry = tk.Entry(
            search, textvariable=self._search_var, width=60, font=("Arial", 9),
        )
        self._search_entry.pack(side="left", padx=(0, 5))
        # Поиск применяется в реальном времени при изменении текста
        self._search_var.trace_add('write', self._apply_filter)
        # Escape — быстрый сброс поиска
        self._search_entry.bind('<Escape>', lambda e: self._search_var.set(""))

        tk.Button(
            search, text="✕", command=lambda: self._search_var.set(""),
            font=("Arial", 8), width=2,
        ).pack(side="left", padx=(0, 10))

        # Чекбокс «Искать только в SQL-тексте»
        # Если выключен — поиск идёт по всем колонкам (логин, хост, таблица...).
        # Если включён — только по тексту запроса. По умолчанию выключен.
        self._search_in_sql_only = tk.BooleanVar(value=False)
        tk.Checkbutton(
            search, text="только в SQL-тексте",
            variable=self._search_in_sql_only, font=("Arial", 8),
        ).pack(side="left", padx=(0, 15))
        self._search_in_sql_only.trace_add('write', self._apply_filter)

        # Подсказка по количеству совпадений
        self._search_match_label = tk.Label(
            search, text="", font=("Arial", 8), fg="gray",
        )
        self._search_match_label.pack(side="left", padx=(5, 0))

    def _build_events_table(self):
        """Таблица событий — Treeview с колонками."""
        # PanedWindow: сверху таблица, снизу SQL-текст. Делитель регулируется.
        self._paned = tk.PanedWindow(
            self._win, orient=tk.VERTICAL, sashrelief="raised",
        )
        self._paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Верхняя часть — таблица
        table_frame = tk.Frame(self._paned)
        self._paned.add(table_frame, minsize=200, height=400)

        v_scroll = tk.Scrollbar(table_frame, orient=tk.VERTICAL)
        h_scroll = tk.Scrollbar(table_frame, orient=tk.HORIZONTAL)

        cols = ("time", "op", "object", "login", "host",
                "duration", "reads", "writes", "rows", "spid")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings",
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set,
        )

        # Заголовки и ширина
        for col, title, w, anchor in [
            ("time", "Время", 145, "w"),
            ("op", "Операция", 80, "center"),
            ("object", "Таблица", 180, "w"),
            ("login", "Логин", 110, "w"),
            ("host", "Хост", 110, "w"),
            ("duration", "Длит.,мс", 80, "e"),
            ("reads", "Reads", 80, "e"),
            ("writes", "Writes", 80, "e"),
            ("rows", "Строк", 70, "e"),
            ("spid", "SPID", 60, "center"),
        ]:
            self._tree.heading(col, text=title)
            self._tree.column(col, width=w, anchor=anchor)

        # Цветовые теги для операций
        for op_name, fg in _OPERATION_COLORS.items():
            self._tree.tag_configure(op_name, foreground=fg)
        # Тег для неизвестных операций
        self._tree.tag_configure('UNKNOWN', foreground='#d4d4d4')

        v_scroll.config(command=self._tree.yview)
        h_scroll.config(command=self._tree.xview)

        self._tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        # Хранилище SQL-текстов: item_id → sql_text
        self._sql_by_item = {}

        # Обработчик клика — показываем SQL внизу
        self._tree.bind("<<TreeviewSelect>>", self._on_row_select)

    def _build_sql_panel(self):
        """Нижняя панель — текстовое поле с SQL-текстом выбранной строки."""
        sql_frame = tk.Frame(self._paned)
        self._paned.add(sql_frame, minsize=80, height=200)

        tk.Label(
            sql_frame, text="SQL-текст выбранного запроса:",
            font=("Arial", 9, "bold"), anchor="w",
        ).pack(fill="x", padx=5, pady=(5, 2))

        text_container = tk.Frame(sql_frame)
        text_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=2)

        sql_scroll = tk.Scrollbar(text_container, orient=tk.VERTICAL)
        self._sql_text = tk.Text(
            text_container, wrap="word",
            font=("Consolas", 10),
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white",
            yscrollcommand=sql_scroll.set,
            state="disabled", height=8,
        )
        sql_scroll.config(command=self._sql_text.yview)
        self._sql_text.pack(side="left", fill=tk.BOTH, expand=True)
        sql_scroll.pack(side="right", fill="y")

        # Метаданные выбранного запроса (под SQL-текстом)
        self._meta_label = tk.Label(
            sql_frame, text="", font=("Consolas", 9),
            fg="gray", anchor="w", justify="left",
        )
        self._meta_label.pack(fill="x", padx=5, pady=(2, 5))

    def _build_buttons(self):
        btn = tk.Frame(self._win)
        btn.pack(pady=8)

        tk.Button(btn, text="▶ Старт", command=self._start,
                  bg="#4CAF50", fg="white", width=12,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="⏸ Стоп", command=self._stop,
                  bg="#FF9800", fg="white", width=12,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="🔍 Диагностика", command=self._show_diagnostics,
                  bg="#673AB7", fg="white", width=14,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="Очистить", command=self._clear,
                  bg="#607D8B", fg="white", width=12,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="Копировать SQL", command=self._copy_sql,
                  bg="#9C27B0", fg="white", width=14,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="Сохранить лог", command=self._export_log,
                  bg=COLOR_INTERACT_BTN, fg="white", width=14,
                  font=("Arial", 10)).pack(side="left", padx=5)
        tk.Button(btn, text="Закрыть", command=self._on_close,
                  bg=COLOR_CLOSE_BTN, fg="white", width=12,
                  font=("Arial", 10)).pack(side="left", padx=5)

    # -------------------------------------------------------------------------

    def _show_diagnostics(self):
        """
        Показать окно диагностики XEvents-сессии.
        Помогает понять, почему профайлер не показывает события:
          - Создана ли сессия?
          - Запущена ли?
          - Сколько событий в ring_buffer?
          - Какие БД фигурируют в событиях?
        """
        # Если профайлер не запущен — предупреждаем
        if not self._session_name:
            messagebox.showinfo(
                "Диагностика",
                "Profiler не запущен. Сначала нажмите ▶ Старт.\n\n"
                "После того как сессия проработает несколько секунд,\n"
                "нажмите «Диагностика» — программа покажет, что лежит\n"
                "в ring_buffer и почему события могут не показываться."
            )
            return

        # Делаем диагностику в фоновом потоке, чтобы не блокировать UI
        diag_win = tk.Toplevel(self._win)
        diag_win.title("Диагностика Profiler")
        diag_win.geometry("800x600")

        tk.Label(diag_win, text="Сбор диагностических данных...",
                 font=("Arial", 10)).pack(pady=10)

        text_frame = tk.Frame(diag_win)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        scroll = tk.Scrollbar(text_frame, orient=tk.VERTICAL)
        text_widget = tk.Text(
            text_frame, wrap="word", font=("Consolas", 9),
            yscrollcommand=scroll.set,
        )
        scroll.config(command=text_widget.yview)
        text_widget.pack(side="left", fill=tk.BOTH, expand=True)
        scroll.pack(side="right", fill="y")
        text_widget.insert("end", "Запрос диагностики, подождите...\n")

        tk.Button(
            diag_win, text="Закрыть",
            command=diag_win.destroy,
            bg=COLOR_CLOSE_BTN, fg="white", width=15,
        ).pack(pady=5)

        def diag_worker():
            try:
                result = self._connector.profiler_diagnose(
                    self._db_type, self._params, self._session_name
                )
            except Exception as e:
                result = {'errors': [f'Не удалось выполнить диагностику: {e}']}
            diag_win.after(0, render_result, result)

        def render_result(result):
            text_widget.delete("1.0", "end")
            text_widget.insert("end", "=" * 60 + "\n")
            text_widget.insert("end", "ДИАГНОСТИКА PROFILER\n")
            text_widget.insert("end", "=" * 60 + "\n\n")

            text_widget.insert("end", f"Имя сессии: {self._session_name}\n")
            text_widget.insert("end", f"Целевая БД:  {self._db_name}\n")
            text_widget.insert("end", f"Тип БД:      {self._db_type}\n\n")

            text_widget.insert(
                "end",
                f"Сессия существует в server_event_sessions: "
                f"{'✅ ДА' if result.get('session_exists') else '❌ НЕТ'}\n"
            )
            text_widget.insert(
                "end",
                f"Сессия запущена (dm_xe_sessions):          "
                f"{'✅ ДА' if result.get('session_active') else '❌ НЕТ'}\n"
            )
            text_widget.insert(
                "end",
                f"Размер XML в ring_buffer:                   "
                f"{result.get('xml_length', 0):,} байт\n".replace(",", " ")
            )
            text_widget.insert(
                "end",
                f"Найдено <event> элементов в XML:            "
                f"{result.get('event_count', 0):,}\n\n".replace(",", " ")
            )

            errors = result.get('errors', [])
            if errors:
                text_widget.insert("end", "ОШИБКИ И ПОДСКАЗКИ:\n")
                text_widget.insert("end", "-" * 60 + "\n")
                for err in errors:
                    text_widget.insert("end", f"⚠ {err}\n\n")

            samples = result.get('sample_events', [])
            if samples:
                text_widget.insert(
                    "end",
                    "ПРИМЕРЫ СОБЫТИЙ ИЗ ring_buffer (первые 3):\n"
                )
                text_widget.insert("end", "-" * 60 + "\n")
                for i, s in enumerate(samples, 1):
                    text_widget.insert("end", f"[{i}]\n")
                    text_widget.insert("end", f"  Время:     {s.get('time', '')}\n")
                    text_widget.insert("end", f"  Операция:  {s.get('op', '')}\n")
                    text_widget.insert("end", f"  Логин:     {s.get('login', '')}\n")
                    text_widget.insert("end", f"  База:      {s.get('database', '')}\n")
                    text_widget.insert("end", f"  SQL:       {s.get('sql', '')}\n\n")

                # Подсказка если БД событий не совпадает с целевой
                target_lower = (self._db_name or '').lower()
                bases = set(
                    s.get('database', '').lower()
                    for s in samples if s.get('database')
                )
                if target_lower and bases and target_lower not in bases:
                    text_widget.insert(
                        "end",
                        f"⚠ ВАЖНО: события идут с БД {bases}, а целевая БД "
                        f"для фильтра — '{self._db_name}'.\n"
                        f"Поэтому в основной таблице они не показываются.\n"
                        f"Возможно, Орион Про подключается к БД с другим именем,\n"
                        f"или соединение делает USE на другую БД.\n\n"
                    )

            preview = result.get('xml_preview', '')
            if preview:
                text_widget.insert("end", "\nПЕРВЫЕ 500 СИМВОЛОВ XML (для отладки):\n")
                text_widget.insert("end", "-" * 60 + "\n")
                text_widget.insert("end", preview + "\n")

        threading.Thread(target=diag_worker, daemon=True).start()

    # -------------------------------------------------------------------------

    def _get_selected_sql(self):
        """Получить SQL-текст текущей выбранной строки."""
        sel = self._tree.selection()
        if not sel:
            return ''
        return self._sql_by_item.get(sel[0], '')

    def _on_row_select(self, event):
        """Показать SQL-текст выбранной строки в нижней панели."""
        sql = self._get_selected_sql()

        self._sql_text.configure(state="normal")
        self._sql_text.delete("1.0", "end")
        self._sql_text.insert("1.0", sql)
        self._sql_text.configure(state="disabled")

        # Метаданные строки
        values = self._tree.item(self._tree.selection()[0], "values") if self._tree.selection() else None
        if values:
            self._meta_label.config(text=(
                f"Время: {values[0]}  |  Операция: {values[1]}  |  "
                f"Таблица: {values[2]}  |  Логин: {values[3]}  |  "
                f"SPID: {values[9]}  |  Длит.: {values[5]}мс  |  "
                f"Reads: {values[6]}  Writes: {values[7]}  Строк: {values[8]}"
            ))

    def _background_poll(self):
        """Фоновый поток для опроса БД (не блокирует UI)."""
        while self._is_running:
            try:
                result = self._connector.profiler_poll(
                    self._db_type, self._params,
                    self._last_marker,
                    session_name=self._session_name,
                )
                self._poll_queue.put(('events', result))
                self._last_marker = result.get('new_marker', self._last_marker)
            except Exception as e:
                self._poll_queue.put(('error', str(e)))
                time.sleep(2)  # Паза при ошибке перед повторной попыткой
            else:
                time.sleep(1)  # Интервал опроса

    def _process_queue(self):
        """Обработка очереди событий в главном потоке (безопасно для Tkinter)."""
        if not self._is_running:
            return

        processed_count = 0
        max_batch = 100  # Обрабатываем не более 100 событий за раз

        try:
            while not self._poll_queue.empty() and processed_count < max_batch:
                msg_type, data = self._poll_queue.get_nowait()

                if msg_type == 'error':
                    self._status_label.config(text="⚠️ Ошибка подключения", fg="orange")
                    self._is_running = False
                    messagebox.showerror("Ошибка Profiler", f"Ошибка при опросе БД:\n{data}")
                    return

                elif msg_type == 'events':
                    events = data.get('events', [])

                    for ev in events:
                        if 'error' in ev:
                            self._add_error_row(ev['error'])
                            continue

                        key = ev.get('unique_key', '')
                        if key and key in self._seen_keys:
                            continue

                        # Добавляем в кэш с ограничением размера
                        if key:
                            if len(self._seen_keys) >= _MAX_SEEN_KEYS_CACHE:
                                # Удаляем самые старые записи (первую)
                                self._seen_keys.popitem(last=False)
                            self._seen_keys[key] = True

                        # Фильтрация (операция / служебные / поиск) применяется
                        # внутри _add_event_row через _event_matches_filters.
                        self._add_event_row(ev)
                        processed_count += 1

                    self._trim_table()
                    self._event_count_label.config(text=f"Событий: {self._total_events}")

        except queue.Empty:
            pass
        except Exception as e:
            print(f"Queue processing error: {e}")

        if self._is_running:
            self._win.after(100, self._process_queue)

    def _add_error_row(self, error_msg):
        """Добавить строку с ошибкой в таблицу."""
        item_id = self._tree.insert(
            "", "end",
            values=("--", "ERROR", "", "", "",
                    "", "", "", "", ""),
            tags=("DELETE",),
        )
        self._sql_by_item[item_id] = f"ОШИБКА: {error_msg}"
        self._events_cache.append({'error': error_msg})
        self._total_events += 1

        if self._auto_scroll.get():
            self._tree.see(item_id)

    def _add_event_row(self, ev):
        """Добавить новое событие в таблицу."""
        op = ev.get('operation', '?').upper()

        # Проверяем, есть ли тег для этой операции
        if op not in _OPERATION_COLORS:
            op_tag = 'UNKNOWN'
        else:
            op_tag = op

        item_id = self._tree.insert(
            "", "end",
            values=(
                ev.get('time', ''),
                op,
                ev.get('object', ''),
                ev.get('login', ''),
                ev.get('host', ''),
                ev.get('duration_ms', 0),
                ev.get('reads', 0),
                ev.get('writes', 0),
                ev.get('rows', 0),
                ev.get('spid', ''),
            ),
            tags=(op_tag,),
        )
        sql_text = ev.get('sql_text', '')
        self._sql_by_item[item_id] = sql_text

        # Добавляем в кэш для фильтрации/поиска/экспорта.
        # Храним все поля события, нужные _event_matches_filters().
        cache_entry = {
            'item_id': item_id,
            'values': self._tree.item(item_id, "values"),
            'sql': sql_text,           # для экспорта
            'sql_text': sql_text,      # для поиска (одно и то же)
            'operation': op,
            'object': ev.get('object', ''),
            'login': ev.get('login', ''),
            'host': ev.get('host', ''),
            'program': ev.get('program', ''),
            'database': ev.get('database', ''),
            'spid': ev.get('spid', ''),
        }
        self._events_cache.append(cache_entry)

        self._total_events += 1

        # Сразу проверяем — проходит ли событие через текущие фильтры.
        # Если нет — detach (но запись остаётся в кэше, чтобы при отключении
        # фильтра/поиска она снова появилась).
        try:
            if not self._event_matches_filters(cache_entry):
                self._tree.detach(item_id)
                return
        except Exception:
            pass  # на старте фильтры могут быть ещё не созданы

        if self._auto_scroll.get():
            self._tree.see(item_id)

    # =========================================================================
    # ФИЛЬТРАЦИЯ И ПОИСК
    # =========================================================================

    # Регулярка для распознавания «мусорных» (служебных) запросов.
    # Перечислены команды, которые не несут полезной информации о работе
    # приложения и засоряют лог:
    #   - Управление транзакциями: BEGIN/COMMIT/ROLLBACK/SAVEPOINT
    #   - Настройки соединения: SET/SHOW/RESET/USE
    #   - Курсорные команды: FETCH/CLOSE/DEALLOCATE/DISCARD
    #   - ODBC-служебные RPC: sp_reset_connection / sp_unprepare / sp_prepare
    #   - Проверки соединения: SELECT 1, SELECT @@VERSION, SELECT @@TRANCOUNT
    # Регистр не учитывается, отступы в начале — допустимы.
    _NOISE_RE = re.compile(
        r'^\s*('
        r'BEGIN\b|COMMIT\b|ROLLBACK\b|SAVEPOINT\b|RELEASE\s+SAVEPOINT\b'
        r'|SET\s+|SHOW\s+|RESET\s+|USE\s+'
        r'|FETCH\b|CLOSE\b|DEALLOCATE\b|DISCARD\b'
        r'|EXEC\s+sp_reset_connection\b'
        r'|EXEC\s+sp_unprepare\b'
        r'|EXEC\s+sp_prepare\b'
        r'|SELECT\s+1\s*;?\s*$'
        r'|SELECT\s+@@VERSION\s*;?\s*$'
        r'|SELECT\s+@@TRANCOUNT\b'
        r'|SELECT\s+@@SPID\s*;?\s*$'
        r')',
        re.IGNORECASE,
    )

    def _is_noise(self, sql_text: str) -> bool:
        """
        Определить, является ли запрос «мусорным» (служебным).
        Возвращает True если запрос стоит скрыть при включённом
        чекбоксе «Скрыть служебные».
        """
        if not sql_text:
            return True  # пустые тексты — тоже мусор
        return bool(self._NOISE_RE.match(sql_text))

    def _event_matches_filters(self, event: dict) -> bool:
        """
        Проверить, проходит ли событие через ВСЕ текущие фильтры:
          1. Фильтр операции (Combobox: Все/SELECT/INSERT/...)
          2. Чекбокс «Скрыть служебные»
          3. Строка поиска
        Возвращает True если событие нужно ПОКАЗАТЬ.
        """
        # 1. Фильтр операции
        filter_value = self._filter_var.get()
        if filter_value != "Все":
            if event.get('operation', '') != filter_value:
                return False

        # 2. Скрытие служебных запросов
        if self._hide_noise.get():
            if self._is_noise(event.get('sql_text', '')):
                return False

        # 3. Поиск
        query = self._search_var.get().strip().lower()
        if query:
            if self._search_in_sql_only.get():
                # Ищем только в тексте SQL
                haystack = event.get('sql_text', '').lower()
            else:
                # Ищем во всех полях: SQL, объект, логин, хост, программа, БД
                haystack = ' '.join(str(event.get(k, '')).lower() for k in (
                    'sql_text', 'object', 'login', 'host', 'program',
                    'database', 'operation', 'spid',
                ))
            if query not in haystack:
                return False

        return True

    def _apply_filter(self, *args):
        """
        Применить все фильтры (операция / служебные / поиск) к таблице.

        Принцип: проходим по _events_cache (полный список накопленных
        событий), для каждого решаем — показывать или нет. Соответственно
        attach'им к дереву или detach'им.

        Treeview хранит все строки внутри, но мы скрываем неподходящие
        через detach() — это позволяет не пересоздавать строки и сохранять
        SQL-тексты в self._sql_by_item.
        """
        if not hasattr(self, '_events_cache'):
            return

        matched_count = 0
        total_in_cache = len(self._events_cache)

        for ev in self._events_cache:
            item_id = ev.get('item_id')
            if not item_id:
                continue

            try:
                show = self._event_matches_filters(ev)
            except Exception:
                show = True

            try:
                if show:
                    # Проверяем — уже привязана ли строка к дереву.
                    # parent="" значит на верхнем уровне (видна).
                    if self._tree.parent(item_id) is None and not self._tree.exists(item_id):
                        # Нет в дереве вообще — пропускаем (была удалена при trim)
                        continue
                    # reattach как последнего ребёнка root
                    self._tree.reattach(item_id, '', 'end')
                    matched_count += 1
                else:
                    self._tree.detach(item_id)
            except tk.TclError:
                # Строка могла быть удалена параллельно — игнорируем
                pass

        # Обновляем счётчик совпадений
        query = self._search_var.get().strip()
        if query:
            self._search_match_label.config(
                text=f"найдено: {matched_count} из {total_in_cache}",
                fg="#2196F3" if matched_count > 0 else "red",
            )
        else:
            self._search_match_label.config(text="")

    def _trim_table(self):
        """Если в таблице больше _MAX_EVENTS_IN_TABLE — удалить самые старые."""
        children = self._tree.get_children()
        if len(children) > _MAX_EVENTS_IN_TABLE:
            to_delete = children[:len(children) - _MAX_EVENTS_IN_TABLE]
            for item_id in to_delete:
                self._sql_by_item.pop(item_id, None)
                self._tree.delete(item_id)

            # Очищаем кэш удалённых событий
            self._events_cache = [
                ev for ev in self._events_cache
                if ev.get('item_id') not in to_delete
            ]

    def _start(self):
        if self._is_running:
            return

        # Создаём event session (только для MSSQL).
        # Для PG это no-op.
        self._status_label.config(text="Запуск сессии...", fg="orange")
        self._win.update_idletasks()

        try:
            session_info = self._connector.profiler_start_session(
                self._db_type, self._params,
                target_db_name=self._db_name,
            )
        except Exception as e:
            messagebox.showerror(
                "Ошибка",
                f"Не удалось запустить сессию профайлера:\n{e}"
            )
            self._status_label.config(text="⏸ Остановлен", fg="red")
            return

        self._session_name = session_info.get('session_name', '')
        self._mode = session_info.get('mode', 'none')
        err = session_info.get('error', '')

        # Если режим — none (полный фейл), не запускаемся
        if self._mode == 'none':
            messagebox.showerror(
                "Ошибка",
                f"Не удалось запустить Profiler:\n{err}"
            )
            self._status_label.config(text="⏸ Остановлен", fg="red")
            return

        # Предупреждаем только для DMV-fallback (т.е. когда XEvents не пошли).
        # Для PG режимов pgss/pg_activity сообщения о расширениях — справочные,
        # пользователь уже сделал свой выбор (видел сообщение раньше), не нужно
        # их повторять при каждом старте. Эта информация теперь доступна
        # по кнопке «🔍 Диагностика».
        if self._mode == 'dmv' and err:
            messagebox.showwarning("Внимание", err)

        # Показываем режим в статусе
        mode_label = {
            'xevents':     '▶ Запущен (XEvents — все запросы)',
            'dmv':         '▶ Запущен (DMV-fallback — только активные)',
            'pgsm':        '▶ Запущен (pg_stat_monitor — реальные значения параметров)',
            'pgss':        '▶ Запущен (pg_stat_statements — параметры как $1, $2)',
            'pg_activity': '▶ Запущен (pg_stat_activity — только активные)',
            'none':        '⏸ Остановлен',
        }.get(self._mode, '▶ Запущен')

        self._is_running = True
        self._last_marker = None  # Сбрасываем маркер
        self._status_label.config(text=mode_label, fg="#4CAF50")

        # Запускаем фоновый поток
        self._background_thread = threading.Thread(
            target=self._background_poll,
            daemon=True
        )
        self._background_thread.start()

        # Запускаем обработку очереди в главном потоке
        self._win.after(100, self._process_queue)

    def _stop(self):
        self._is_running = False

        # Ждём завершения фонового потока
        if self._background_thread and self._background_thread.is_alive():
            self._background_thread.join(timeout=2.0)

        # Удаляем event session
        if self._session_name:
            try:
                self._connector.profiler_stop_session(
                    self._db_type, self._params, self._session_name
                )
            except Exception as e:
                print(f"Stop session error: {e}")
            self._session_name = ''
            self._mode = 'none'

        self._status_label.config(text="⏸ Остановлен", fg="red")

    def _clear(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._sql_by_item.clear()
        self._seen_keys.clear()
        self._events_cache.clear()
        self._total_events = 0
        self._event_count_label.config(text="Событий: 0")
        self._sql_text.configure(state="normal")
        self._sql_text.delete("1.0", "end")
        self._sql_text.configure(state="disabled")
        self._meta_label.config(text="")

    def _copy_sql(self):
        """Скопировать SQL-текст текущей строки в буфер обмена."""
        sql = self._get_selected_sql()
        if not sql:
            messagebox.showinfo("Подсказка", "Сначала выберите строку в таблице.")
            return
        self._win.clipboard_clear()
        self._win.clipboard_append(sql)
        messagebox.showinfo("Успех", "SQL скопирован в буфер обмена")

    def _export_log(self):
        """Экспортировать всё содержимое таблицы в текстовый файл."""
        filepath = filedialog.asksaveasfilename(
            title="Сохранить лог Profiler",
            defaultextension=".txt",
            initialfile=f"profiler_{self._db_name}.txt",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )
        if not filepath:
            return

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(f"Profiler log — {self._db_name} ({self._db_type})\n")
                f.write(f"Экспортировано: {datetime.now()}\n")
                f.write(f"Всего событий: {self._total_events}\n")
                f.write("=" * 100 + "\n\n")

                # Экспортируем из кэша (надёжнее, чем из Treeview)
                for ev in self._events_cache:
                    if 'error' in ev:
                        f.write(f"[ERROR] {ev['error']}\n\n")
                        continue

                    values = ev.get('values')
                    sql = ev.get('sql', '')
                    if values:
                        f.write(
                            f"[{values[0]}] {values[1]:8} {values[2]} "
                            f"| {values[3]}@{values[4]} | SPID:{values[9]} "
                            f"| {values[5]}ms | reads:{values[6]} writes:{values[7]} "
                            f"rows:{values[8]}\n"
                        )
                        if sql:
                            f.write(f"    SQL: {sql}\n")
                        f.write("\n")

            messagebox.showinfo("Успех", f"Лог сохранён:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{str(e)}")

    def _on_close(self):
        self._is_running = False

        # Ждём завершения фонового потока
        if self._background_thread and self._background_thread.is_alive():
            self._background_thread.join(timeout=2.0)

        # КРИТИЧНО: гарантированно удалить event session, иначе она
        # останется висеть в БД после закрытия окна.
        if self._session_name:
            try:
                self._connector.profiler_stop_session(
                    self._db_type, self._params, self._session_name
                )
            except Exception:
                pass
            self._session_name = ''

        self._win.destroy()