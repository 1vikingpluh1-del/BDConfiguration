# ==============================================================================
# app.py — Главный класс приложения (GUI-контроллер)
# ==============================================================================
# DBConnectionApp — «дирижёр»:
#   - Создаёт главное окно с полями ввода и кнопками
#   - Делегирует логику модулям (db_connector, server_scanner, config_manager)
#   - Открывает дочерние окна из пакета views/ при необходимости
#
# Тяжёлые экраны вынесены в отдельные модули:
#   - views/db_structure_view.py   — структура БД
#   - views/profiler_view.py       — Profiler
#   - views/mass_add_view.py       — массовое добавление (фоновый поток)
#   - views/maraprc_viewer.py      — просмотр maraprC.xml
# ==============================================================================

import tkinter as tk
from tkinter import ttk, messagebox

import threading

from constants import (
    WINDOW_TITLE,
    WINDOW_WIDTH,
    WINDOW_HEIGHT,
    STRUCTURE_EXTRA_WIDTH,
    STRUCTURE_EXTRA_HEIGHT,
    SUPPORTED_DB_TYPES,
    DB_TYPE_POSTGRES,
    DB_TYPE_MSSQL,
    AUTH_TYPE_SQL,
    AUTH_TYPE_WINDOWS,
    COLOR_DISCONNECTED,
    COLOR_CONNECTING,
    COLOR_CONNECTED,
    COLOR_CONNECT_BTN,
    COLOR_INTERACT_BTN,
    COLOR_CLOSE_BTN,
    COLOR_DISABLED_BTN,
    DEFAULT_PG_HOST,
    DEFAULT_PG_PORT,
    APP_VERSION,
    APP_DESCRIPTION,
)
from db_connector import DBConnector
from server_scanner import ServerScanner
from config_manager import ConfigManager
from ui_widgets import ListPickerWindow, ProgressWindow, center_window

# Дочерние окна
from views.db_structure_view import DBStructureView
from views.profiler_view import ProfilerView
from views.mass_add_view import MassAddView
from views.maraprc_viewer import open_maraprc_viewer
from views.person_search_view import PersonSearchView


class DBConnectionApp:
    """Главный класс приложения — GUI-контроллер."""

    def __init__(self, root: tk.Tk):
        self.root = root

        # --- Настройка окна ---
        self.root.title(WINDOW_TITLE)
        self.root.resizable(False, False)
        self._center_on_screen(WINDOW_WIDTH, WINDOW_HEIGHT)
        self._original_width = WINDOW_WIDTH
        self._original_height = WINDOW_HEIGHT

        # --- Сетка колонок ---
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, minsize=30)
        self.root.grid_columnconfigure(2, minsize=140)
        self.root.grid_columnconfigure(3, minsize=280)
        self.root.grid_columnconfigure(4, minsize=35)
        self.root.grid_columnconfigure(5, weight=1)
        self.root.grid_columnconfigure(6, minsize=30)

        # --- Инициализация модулей бизнес-логики ---
        self._connector = DBConnector()
        self._scanner = ServerScanner()
        self._config = ConfigManager()

        # --- Состояние ---
        self._connected = False
        self._interaction_button = None
        self._structure_view = None  # экземпляр DBStructureView

        # --- GUI ---
        self._create_status_indicator()
        self._create_db_type_selector()
        self._create_input_fields()
        self._create_auxiliary_buttons()
        self._create_connect_button()
        self._create_maraprc_button()
        self._create_help_button()

        # --- Загрузка сохранённых настроек ---
        self._load_config()

        # --- Стартовое состояние (показ нужных полей) ---
        self._on_db_change()

    # =========================================================================
    # ПОЗИЦИОНИРОВАНИЕ ОКНА
    # =========================================================================

    def _center_on_screen(self, width: int, height: int):
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # =========================================================================
    # СОЗДАНИЕ GUI-ЭЛЕМЕНТОВ
    # =========================================================================

    def _create_status_indicator(self):
        self._status_canvas = tk.Canvas(self.root, width=20, height=20)
        self._status_canvas.grid(row=0, column=1, padx=10, pady=10, sticky="w")
        self._status_indicator = self._status_canvas.create_oval(
            2, 2, 18, 18, fill=COLOR_DISCONNECTED
        )

    def _create_db_type_selector(self):
        tk.Label(self.root, text="Тип СУБД: ").grid(
            row=0, column=2, sticky="e", padx=5, pady=5
        )
        self._db_type = tk.StringVar(value=DB_TYPE_POSTGRES)
        db_combo = ttk.Combobox(
            self.root, textvariable=self._db_type,
            values=SUPPORTED_DB_TYPES, state="readonly", width=28,
        )
        db_combo.grid(row=0, column=3, sticky="w", padx=5)
        db_combo.bind("<<ComboboxSelected>>", self._on_db_change)

    def _create_input_fields(self):
        self._widgets = {}

        # Поля для PostgreSQL
        self._widgets['pg_host'] = self._make_field("Сервер (хост):", DEFAULT_PG_HOST, row=1)
        self._widgets['pg_port'] = self._make_field("Порт:", DEFAULT_PG_PORT, row=2)
        self._widgets['pg_db'] = self._make_field("Имя базы данных:", "", row=3)
        self._widgets['pg_user'] = self._make_field("Имя пользователя:", "", row=4)
        self._widgets['pg_pass'] = self._make_password_field("Пароль:", row=5)

        # Поля для MSSQL
        self._widgets['ms_server'] = self._make_field("Сервер (MS SQL):", "", row=6)
        self._widgets['ms_db'] = self._make_field("База данных (MS SQL):", "", row=7)
        self._widgets['ms_user'] = self._make_field("Имя пользователя (SQL):", "", row=8)
        self._widgets['ms_pass'] = self._make_password_field("Пароль:", row=9)

        # Тип аутентификации MSSQL
        auth_row = 10
        self._auth_label = tk.Label(self.root, text="Тип аутентификации:")
        self._auth_label.grid(row=auth_row, column=2, sticky="e", padx=5, pady=5)

        self._auth_type = tk.StringVar(value=AUTH_TYPE_SQL)
        self._auth_frame = tk.Frame(self.root)
        self._auth_frame.grid(row=auth_row, column=3, sticky="w", padx=5)

        self._sql_radio = ttk.Radiobutton(
            self._auth_frame, text="SQL Server",
            variable=self._auth_type, value=AUTH_TYPE_SQL,
            command=self._on_auth_change,
        )
        self._windows_radio = ttk.Radiobutton(
            self._auth_frame, text="Windows",
            variable=self._auth_type, value=AUTH_TYPE_WINDOWS,
            command=self._on_auth_change,
        )
        self._sql_radio.pack(side="left", padx=(0, 15))
        self._windows_radio.pack(side="left")

    def _create_auxiliary_buttons(self):
        self._fetch_server_btn = tk.Button(
            self.root, text="...", command=self._fetch_servers,
            width=3, height=1, font=("Arial", 7, "bold"),
            bg="#2196F3", fg="white", relief="raised",
        )
        self._fetch_server_btn.grid_remove()

        self._fetch_db_btn = tk.Button(
            self.root, text="...", command=self._fetch_databases,
            width=3, height=1, font=("Arial", 7, "bold"),
            bg="#2196F3", fg="white", relief="raised",
        )
        self._fetch_db_btn.grid_remove()

    def _create_connect_button(self):
        self._connect_button = tk.Button(
            self.root, text="Подключиться",
            command=self._connect,
            width=20, bg=COLOR_CONNECT_BTN, fg="white",
        )
        self._connect_button.grid(row=12, column=2, columnspan=3, pady=10)

    def _create_maraprc_button(self):
        self._maraprc_button = tk.Button(
            self.root, text="Просмотр maraprC",
            command=lambda: open_maraprc_viewer(self.root),
            width=20, bg="#FF9800", fg="white",
        )
        self._maraprc_button.grid(row=11, column=2, columnspan=3, pady=(5, 0))

    def _create_help_button(self):
        self._help_button = tk.Button(
            self.root, text="?", command=self._show_about,
            width=2, height=1, font=("Arial", 9, "bold"),
        )
        self._help_button.grid(row=0, column=6, padx=5, pady=10, sticky="e")

    # =========================================================================
    # ФАБРИКИ ПОЛЕЙ
    # =========================================================================

    def _make_field(self, label_text: str, default: str, row: int) -> dict:
        label = tk.Label(self.root, text=label_text)
        label.grid(row=row, column=2, sticky="e", padx=5, pady=5)

        var = tk.StringVar(value=default)
        var.trace_add("write", lambda *args: self._update_connect_button_state())

        entry = tk.Entry(
            self.root, textvariable=var, width=28,
            highlightthickness=0, relief="solid",
        )
        entry.grid(row=row, column=3, sticky="w", padx=5)

        return {'entry': entry, 'var': var, 'row': row, 'label': label}

    def _make_password_field(self, label_text: str, row: int) -> dict:
        label = tk.Label(self.root, text=label_text)
        label.grid(row=row, column=2, sticky="e", padx=5, pady=5)

        var = tk.StringVar()
        var.trace_add("write", lambda *args: self._update_connect_button_state())

        entry = tk.Entry(
            self.root, textvariable=var, show="*", width=28,
            highlightthickness=0, relief="solid",
        )
        entry.grid(row=row, column=3, sticky="w", padx=5)

        return {'entry': entry, 'var': var, 'row': row, 'label': label}

    # =========================================================================
    # ОБРАБОТЧИКИ СОБЫТИЙ
    # =========================================================================

    def _on_db_change(self, event=None):
        """Обработчик смены типа СУБД."""
        self._connected = False
        self._status_canvas.itemconfig(self._status_indicator, fill=COLOR_DISCONNECTED)
        self._connect_button.config(state="normal", bg=COLOR_CONNECT_BTN)

        if self._interaction_button:
            self._interaction_button.grid_remove()

        self._hide_structure_frame()

        self._fetch_db_btn.grid_remove()
        self._fetch_server_btn.grid_remove()

        is_pg = self._db_type.get() == DB_TYPE_POSTGRES

        # Скрыть все поля
        for key in self._widgets:
            self._widgets[key]['entry'].grid_forget()
            self._widgets[key]['label'].grid_forget()
        self._auth_label.grid_forget()
        self._auth_frame.grid_forget()

        # Показать нужные
        if is_pg:
            for key in ['pg_host', 'pg_port', 'pg_db', 'pg_user', 'pg_pass']:
                row = self._widgets[key]['row']
                self._widgets[key]['entry'].grid(row=row, column=3, sticky="w", padx=5)
                self._widgets[key]['label'].grid(row=row, column=2, sticky="e", padx=5, pady=5)
            self._fetch_server_btn.grid(row=1, column=4, padx=5)
        else:
            for key in ['ms_server', 'ms_db', 'ms_user', 'ms_pass']:
                row = self._widgets[key]['row']
                self._widgets[key]['entry'].grid(row=row, column=3, sticky="w", padx=5)
                self._widgets[key]['label'].grid(row=row, column=2, sticky="e", padx=5, pady=5)
            self._fetch_server_btn.grid(row=6, column=4, padx=5)
            auth_row = self._widgets['ms_pass']['row'] + 1
            self._auth_label.grid(row=auth_row, column=2, sticky="e", padx=5, pady=5)
            self._auth_frame.grid(row=auth_row, column=3, sticky="w", padx=5)
            self._on_auth_change()

        self._update_connect_button_state()

    def _on_auth_change(self):
        is_sql = self._auth_type.get() == AUTH_TYPE_SQL
        for key in ['ms_user', 'ms_pass']:
            if is_sql:
                row = self._widgets[key]['row']
                self._widgets[key]['entry'].grid(row=row, column=3, sticky="w", padx=5)
                self._widgets[key]['label'].grid(row=row, column=2, sticky="e", padx=5, pady=5)
            else:
                self._widgets[key]['entry'].grid_forget()
                self._widgets[key]['label'].grid_forget()
        self._update_connect_button_state()

    # =========================================================================
    # ПОДКЛЮЧЕНИЕ
    # =========================================================================

    def _connect(self):
        try:
            self._status_canvas.itemconfig(self._status_indicator, fill=COLOR_CONNECTING)
            self.root.update_idletasks()

            params = self._collect_connection_params()
            self._connector.test_connection(self._db_type.get(), params)

            self._status_canvas.itemconfig(self._status_indicator, fill=COLOR_CONNECTED)
            messagebox.showinfo(
                "Успех",
                "Подключение успешно установлено!  "
                "Выберите базу данных с помощью нажатия на кнопку '...' "
            )
            self._save_config()
            self._connect_button.config(state="disabled", bg=COLOR_DISABLED_BTN)
            self._connected = True

            if self._db_type.get() == DB_TYPE_POSTGRES:
                self._fetch_db_btn.grid(row=3, column=4, padx=5)
            else:
                self._fetch_db_btn.grid(
                    row=self._widgets['ms_db']['row'], column=4, padx=5
                )

            self._show_interaction_button()

        except Exception as e:
            self._status_canvas.itemconfig(self._status_indicator, fill=COLOR_DISCONNECTED)
            messagebox.showerror("Ошибка подключения", f"Не удалось подключиться:\n{str(e)}")
            self._connected = False

    def _update_connect_button_state(self):
        is_pg = self._db_type.get() == DB_TYPE_POSTGRES

        if is_pg:
            host = self._widgets['pg_host']['var'].get().strip()
            port = self._widgets['pg_port']['var'].get().strip()
            user = self._widgets['pg_user']['var'].get().strip()
            password = self._widgets['pg_pass']['var'].get().strip()
            ready = all([host, port, user, password])
        else:
            server = self._widgets['ms_server']['var'].get().strip()
            if self._auth_type.get() == AUTH_TYPE_WINDOWS:
                ready = bool(server)
            else:
                user = self._widgets['ms_user']['var'].get().strip()
                password = self._widgets['ms_pass']['var'].get().strip()
                ready = all([server, user, password])

        if ready:
            self._connect_button.config(state="normal", bg=COLOR_CONNECT_BTN)
        else:
            self._connect_button.config(state="disabled", bg=COLOR_DISABLED_BTN)

    def _collect_connection_params(self) -> dict:
        if self._db_type.get() == DB_TYPE_POSTGRES:
            return {
                'host': self._widgets['pg_host']['var'].get().strip(),
                'port': self._widgets['pg_port']['var'].get().strip(),
                'dbname': self._widgets['pg_db']['var'].get().strip(),
                'user': self._widgets['pg_user']['var'].get().strip(),
                'password': self._widgets['pg_pass']['var'].get(),
            }
        else:
            return {
                'server': self._widgets['ms_server']['var'].get().strip(),
                'database': self._widgets['ms_db']['var'].get().strip(),
                'auth_type': self._auth_type.get(),
                'user': self._widgets['ms_user']['var'].get().strip(),
                'password': self._widgets['ms_pass']['var'].get(),
            }

    # =========================================================================
    # ПОИСК СЕРВЕРОВ
    # =========================================================================

    def _fetch_servers(self):
        progress = ProgressWindow(self.root, "Поиск серверов...", "Поиск серверов...")

        def _search():
            try:
                servers = self._scanner.discover_servers(self._db_type.get())
                if servers:
                    self.root.after(0, lambda: ListPickerWindow(
                        self.root,
                        "Выберите сервер",
                        servers,
                        self._on_server_selected,
                    ))
                else:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Информация", "Серверы не найдены."
                    ))
            finally:
                self.root.after(0, progress.close)

        thread = threading.Thread(target=_search, daemon=True)
        thread.start()

    def _on_server_selected(self, selected: str):
        if self._db_type.get() == DB_TYPE_POSTGRES:
            self._widgets['pg_host']['var'].set(selected)
        else:
            self._widgets['ms_server']['var'].set(selected)
        self._show_auth_hint_if_needed()

    # =========================================================================
    # СПИСОК БД
    # =========================================================================

    def _fetch_databases(self):
        try:
            params = self._collect_connection_params()
            databases = self._connector.fetch_databases(self._db_type.get(), params)
            ListPickerWindow(
                self.root, "Выберите базу данных",
                databases, self._on_database_selected,
            )
        except Exception as e:
            messagebox.showerror(
                "Ошибка", f"Не удалось получить список баз данных:\n{str(e)}"
            )

    def _on_database_selected(self, selected: str):
        if self._db_type.get() == DB_TYPE_POSTGRES:
            self._widgets['pg_db']['var'].set(selected)
            self._widgets['pg_db']['entry'].grid(row=3, column=3, sticky="w", padx=5)
            self._fetch_db_btn.grid(row=3, column=4, padx=5)
        else:
            self._widgets['ms_db']['var'].set(selected)
            row = self._widgets['ms_db']['row']
            self._widgets['ms_db']['entry'].grid(row=row, column=3, sticky="w", padx=5)
            self._fetch_db_btn.grid(row=row, column=4, padx=5)

        self._update_connect_button_state()
        if self._connected:
            self._connect_button.config(state="disabled", bg=COLOR_DISABLED_BTN)

    # =========================================================================
    # ВЗАИМОДЕЙСТВИЕ С БД
    # =========================================================================

    def _show_interaction_button(self):
        if self._interaction_button is None:
            self._interaction_button = tk.Button(
                self.root, text="Взаимодействие с БД",
                command=self._show_interaction_menu,
                width=20, bg=COLOR_INTERACT_BTN, fg="white",
            )
            self._interaction_button.grid(row=13, column=2, columnspan=3, pady=5)
        else:
            self._interaction_button.grid()

    def _show_interaction_menu(self):
        """Показать меню взаимодействия с БД (всплывающее окно с действиями)."""
        menu_window = tk.Toplevel(self.root)
        menu_window.title("Взаимодействие с БД")
        menu_window.geometry("400x290")
        menu_window.resizable(False, False)
        center_window(menu_window, self.root)

        actions = {
            "Просмотр структуры выбранной БД": tk.BooleanVar(value=False),
            "Массовое добавление элементов": tk.BooleanVar(value=False),
            "Поиск/выборка сотрудников": tk.BooleanVar(value=False),
        }

        tk.Label(menu_window, text="Выберите действие:",
                 font=("Arial", 10)).pack(pady=10)

        for action_text, var in actions.items():
            tk.Checkbutton(
                menu_window, text=action_text,
                variable=var, anchor="w", font=("Arial", 9),
            ).pack(anchor="w", padx=20, pady=2)

        def on_ok():
            selected = [text for text, var in actions.items() if var.get()]
            if not selected:
                messagebox.showwarning("Предупреждение", "Выберите хотя бы одно действие!")
                return
            menu_window.destroy()

            for action in selected:
                if action == "Просмотр структуры выбранной БД":
                    self._show_db_structure()
                elif action == "Массовое добавление элементов":
                    self._show_mass_add_window()
                elif action == "Поиск/выборка сотрудников":
                    self._show_person_search_window()

        tk.Button(menu_window, text="OK", command=on_ok,
                  bg=COLOR_INTERACT_BTN, fg="white", width=15).pack(pady=15)

    def _show_person_search_window(self):
        """Открыть окно поиска сотрудников по идентификаторам."""
        if not self._connected:
            messagebox.showwarning("Предупреждение", "Сначала подключитесь к базе данных!")
            return

        if self._db_type.get() == DB_TYPE_POSTGRES:
            db_name = self._widgets['pg_db']['var'].get().strip()
        else:
            db_name = self._widgets['ms_db']['var'].get().strip()

        if not db_name:
            messagebox.showwarning("Предупреждение", "Сначала выберите базу данных!")
            return

        params = self._collect_connection_params()
        PersonSearchView(self.root, self._connector, self._db_type.get(),
                         params, db_name)

    def _show_mass_add_window(self):
        """Открыть окно массового добавления."""
        if not self._connected:
            messagebox.showwarning("Предупреждение", "Сначала подключитесь к базе данных!")
            return

        if self._db_type.get() == DB_TYPE_POSTGRES:
            db_name = self._widgets['pg_db']['var'].get().strip()
        else:
            db_name = self._widgets['ms_db']['var'].get().strip()

        if not db_name:
            messagebox.showwarning("Предупреждение", "Сначала выберите базу данных!")
            return

        params = self._collect_connection_params()
        MassAddView(self.root, self._connector, self._db_type.get(), params)

    def _show_db_structure(self):
        """Показать структуру БД (через DBStructureView)."""
        if self._db_type.get() == DB_TYPE_POSTGRES:
            db_name = self._widgets['pg_db']['var'].get().strip()
        else:
            db_name = self._widgets['ms_db']['var'].get().strip()

        if not db_name:
            messagebox.showwarning("Предупреждение", "Сначала выберите базу данных!")
            return

        # Если уже открыто — закрываем
        if self._structure_view:
            self._structure_view.destroy()

        params = self._collect_connection_params()

        self._structure_view = DBStructureView(
            self.root, self._connector,
            self._db_type.get(), params, db_name,
            on_close=self._hide_structure_frame,
            on_open_profiler=self._show_profiler,
        )

        # Увеличиваем главное окно (учитываем высоту экрана!)
        new_width = self._original_width + STRUCTURE_EXTRA_WIDTH
        new_height = self._original_height + STRUCTURE_EXTRA_HEIGHT
        # Не вылезаем за пределы экрана
        max_height = self.root.winfo_screenheight() - 80
        new_height = min(new_height, max_height)
        self._center_on_screen(new_width, new_height)

    def _hide_structure_frame(self):
        """Скрыть структуру БД и вернуть окно к исходному размеру."""
        if self._structure_view:
            self._structure_view.destroy()
            self._structure_view = None
            self.root.resizable(False, False)
            self._center_on_screen(self._original_width, self._original_height)

    def _show_profiler(self):
        """Открыть окно Profiler."""
        if self._db_type.get() == DB_TYPE_POSTGRES:
            db_name = self._widgets['pg_db']['var'].get().strip()
        else:
            db_name = self._widgets['ms_db']['var'].get().strip()

        params = self._collect_connection_params()
        ProfilerView(self.root, self._connector, self._db_type.get(),
                     params, db_name)

    # =========================================================================
    # ПОДСКАЗКИ И СПРАВКА
    # =========================================================================

    def _show_about(self):
        about_window = tk.Toplevel(self.root)
        about_window.title("Справка")
        about_window.resizable(False, False)

        tk.Label(
            about_window, text=WINDOW_TITLE,
            font=("Arial", 11, "bold"),
        ).pack(padx=20, pady=(15, 5))

        tk.Label(
            about_window, text=f"Версия: {APP_VERSION}",
            font=("Arial", 9), fg="gray",
        ).pack()

        ttk.Separator(about_window, orient="horizontal").pack(fill="x", padx=15, pady=10)

        tk.Label(
            about_window, text=APP_DESCRIPTION,
            justify="left", anchor="w",
            font=("Arial", 9), wraplength=350,
        ).pack(padx=20, pady=(0, 10))

        tk.Button(
            about_window, text="Закрыть",
            command=about_window.destroy, width=10,
        ).pack(pady=(5, 15))

        center_window(about_window, self.root)

    def _show_auth_hint_if_needed(self):
        """Показать подсказку об аутентификации (только один раз)."""
        if self._config.is_hint_shown():
            return

        if self._db_type.get() == DB_TYPE_POSTGRES:
            msg = "Для подключения к PostgreSQL нужно ввести логин и пароль."
        elif self._auth_type.get() == AUTH_TYPE_WINDOWS:
            msg = "Вы выбрали Windows-аутентификацию — вводить логин и пароль не нужно."
        else:
            msg = "Для подключения к MSSQL нужно ввести логин и пароль."

        messagebox.showinfo("Подсказка", msg)
        self._config.mark_hint_shown()

    # =========================================================================
    # КОНФИГУРАЦИЯ
    # =========================================================================

    def _save_config(self):
        """
        Сохранить настройки в config.ini.
        ПАРОЛИ НЕ СОХРАНЯЮТСЯ — это политика безопасности.
        ConfigManager.save() автоматически фильтрует поля pg_pass и ms_pass.
        """
        settings = {
            'type': self._db_type.get(),
            'pg_host': self._widgets['pg_host']['var'].get(),
            'pg_port': self._widgets['pg_port']['var'].get(),
            'pg_db': self._widgets['pg_db']['var'].get(),
            'pg_user': self._widgets['pg_user']['var'].get(),
            # 'pg_pass' НЕ передаём — пароли в открытом виде хранить нельзя
            'ms_server': self._widgets['ms_server']['var'].get(),
            'ms_db': self._widgets['ms_db']['var'].get(),
            'auth_type': self._auth_type.get(),
            'ms_user': self._widgets['ms_user']['var'].get(),
        }
        self._config.save(settings)

    def _load_config(self):
        """Загрузить сохранённые настройки. Пароли НЕ загружаем."""
        data = self._config.load()
        if data is None:
            return

        try:
            self._db_type.set(data.get('type', DB_TYPE_POSTGRES))
            self._widgets['pg_host']['var'].set(data.get('pg_host', DEFAULT_PG_HOST))
            self._widgets['pg_port']['var'].set(data.get('pg_port', DEFAULT_PG_PORT))
            self._widgets['pg_db']['var'].set(data.get('pg_db', ''))
            self._widgets['pg_user']['var'].set(data.get('pg_user', ''))
            # 'pg_pass' НЕ загружаем — пользователь должен вводить пароль при каждом запуске
            self._widgets['ms_server']['var'].set(data.get('ms_server', ''))
            self._widgets['ms_db']['var'].set(data.get('ms_db', ''))
            self._auth_type.set(data.get('auth_type', AUTH_TYPE_SQL))
            self._widgets['ms_user']['var'].set(data.get('ms_user', ''))
            self._on_db_change()
        except Exception as e:
            messagebox.showwarning(
                "Предупреждение",
                f"Не удалось загрузить config.ini:\n{str(e)}"
            )
