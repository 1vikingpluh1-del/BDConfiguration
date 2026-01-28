import tkinter as tk  # стандартная библиотека для создания графических интерфейсов
from tkinter import ttk, messagebox
import getpass
import psycopg2
import pyodbc
import configparser  # работает с ini файлами
import os  # проверяет, существует ли файл os.path.exists
# импорт для сканирования портов
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
import subprocess
CONFIG_FILE = "config.ini"
# Класс DBConnectionAPP - главный класс конекта
# __init__ - конструктор(создание окна)
# root - главное окно
# self.root - сохраняем ссылку на окно, чтобы управлять им
# Заголовок окна
# Размер: 500x400 пикселей
# Запрет изменения размера (resizable(false, false))
class DBConnectionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Вспомогательные инструменты для тестирования")
        self.root.geometry("500x400")
        self.root.resizable(False, False)
        # Сохраняем Исходный размер
        self.original_width = 500
        self.original_height = 400
        # Индикатор подключения (Красный/зеленый кружок)
        # Canvas - холст для рисования (круг)
        # grid() - размещение в строке 0, колонка 0
        # Рисуем красный круг (начальное состояние - "не подключено")
        # Координаты: (2,2) - верхний левый угол, (18, 18) - нижний правый?? пока предварительно
        self.status_canvas = tk.Canvas(root, width=20, height=20)
        self.status_canvas.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.status_indicator = self.status_canvas.create_oval(2, 2, 18, 18, fill="red")
        # Выбор СУБД((список СУБД)выпадающий)
        # Надпись "Тип СУБД" в строке 0, колонке 1
        # Специальная переменная StringVar - связывает значение с виджетом(автообновление)
        # Выпадающий список с двумя вариантами(в будущем надо добавить SQLLite для АРМС3000)
        # state="readonly" - блокиратор ввода текста, только выбор
        # on_db_change() - это метод который вызвается при выборе элемента(обновление видимых полей)
        tk.Label(root, text="Тип СУБД:").grid(row=0, column=1, sticky="w", padx=5, pady=5)
        self.db_type = tk.StringVar(value="PostgreSQL")
        db_combo = ttk.Combobox(
            root,
            textvariable=self.db_type,
            values=["PostgreSQL", "Microsoft SQL Server"],
            state="readonly",
            width=25
        )
        db_combo.grid(row=0, column=2, columnspan=2, sticky="w", padx=5)
        db_combo.bind("<<ComboboxSelected>>", self.on_db_change)
        # Создание полей ввода
        # self.widgets = {} - словарь для хранения всех полей(Entry, StringVar), для дальнейшего обращения
        # Создаем все поля сразу, но потом скрываем ненужные (В зависимости от выбора СУБД)
        # on_db_change()  - покажем нужные поля(вызываем в ручную, чтобы при запуске отображались поля для PostgreSQL(значения по умолчанию))
        self.widgets = {}
        self.create_postgres_fields()
        self.create_mssql_fields()
        # Кнопка "Получить список БД" - значально скрыта
        self.fetch_db_button = tk.Button(
            root, text="...", command=self.fetch_databases,
            width=3, height=1, font=("Arial", 6)
        )
        self.fetch_db_button.grid_remove()
        # Добавление Combobox для выбора базы данных
        self.db_list_combo = ttk.Combobox(root, state="readonly", width=28)
        self.db_list_combo.grid(row=3, column=2, columnspan=2, sticky="w", padx=5)
        self.db_list_combo.grid_remove()  # crhsnm
        self.db_selection_mode = False  # Флаг: используем ли список вместо Entry
        # Listbox и Toplevel
        self.db_list_window = None
        self.db_listbox = None
        # Кнопка "..." для выбора сервера
        self.fetch_server_button = tk.Button(
            root, text="...", command=self.fetch_servers,
            width=3, height=1, font=("Arial", 6)
        )
        self.fetch_server_button.grid_remove()
        # Кнопка подключения
        # При нажатии вызывается метод self.connect()
        self.connect_button = tk.Button(
            root, text="Подключиться", command=self.connect, width=20, bg="#4CAF50", fg="white"
        )
        self.connect_button.grid(row=12, column=1, columnspan=3, pady=20)
        # Загрузка сохранненых настроек(пока без пароля)(главное условие, это если config.ini создан)
        self.load_config()
        # --- ИНИЦИАЛИЗИРУЕМ АТРИБУТЫ ПЕРЕД ВЫЗОВОМ on_db_change ---
        self.connected = False
        self.interaction_button = None
        self.structure_frame = None
        self.structure_text = None
        # -----------------------------------------------------------
        # Вызов метода on_db_change - вызывается после всех элементов!
        self.on_db_change()

    # Далее добавляем методы создания полей _create_field() - универсальное текстовое поле
    # для postgres и MSSQL
    def create_postgres_fields(self):
        row = 1
        self.widgets['pg_host'] = self._create_field("Сервер (хост):", "localhost", row)
        self.widgets['pg_port'] = self._create_field("Порт:", "5432", row + 1)
        self.widgets['pg_db'] = self._create_field("Имя базы данных:", "", row + 2)
        self.widgets['pg_user'] = self._create_field("Имя пользователя:", "", row + 3)
        self.widgets['pg_pass'] = self._create_password_field("Пароль:", row + 4)

    def create_mssql_fields(self):
        row = 6
        self.widgets['ms_server'] = self._create_field("Сервер (MS SQL):", "", row)
        self.widgets['ms_db'] = self._create_field("База данных (MS SQL):", "", row + 1)
        self.widgets['ms_user'] = self._create_field("Имя пользователя (SQL):", "", row + 2)
        self.widgets['ms_pass'] = self._create_password_field("Пароль:", row + 3)
        # Тип аутентификации — ПОД паролем
        auth_row = row + 4  # ← следующая строка после пароля
        self.auth_label = tk.Label(self.root, text="Тип аутентификации:")
        self.auth_label.grid(row=auth_row, column=1, sticky="w", padx=5, pady=5)
        self.auth_type = tk.StringVar(value="sql")
        self.sql_radio = ttk.Radiobutton(
            self.root, text="SQL Server", variable=self.auth_type, value="sql", command=self.on_auth_change
        )
        self.windows_radio = ttk.Radiobutton(
            self.root, text="Windows", variable=self.auth_type, value="windows", command=self.on_auth_change
        )
        self.sql_radio.grid(row=auth_row, column=2, sticky="w")
        self.windows_radio.grid(row=auth_row, column=3, sticky="w")

    def _create_field(self, label_text, default, row):
        label = tk.Label(self.root, text=label_text)
        label.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        var = tk.StringVar(value=default)
        # Отслеживаем изменения
        var.trace_add("write", lambda *args: self.enable_connect_button())
        entry = tk.Entry(self.root, textvariable=var, width=30, highlightthickness=0, relief="solid")
        entry.grid(row=row, column=2, columnspan=2, sticky="w", padx=5)
        return {'entry': entry, 'var': var, 'row': row, 'label': label}

    def _create_password_field(self, label_text, row):
        label = tk.Label(self.root, text=label_text)
        label.grid(row=row, column=1, sticky="w", padx=5, pady=5)
        var = tk.StringVar()
        var.trace_add("write", lambda *args: self.enable_connect_button())
        entry = tk.Entry(self.root, textvariable=var, show="*", width=30, highlightthickness=0, relief="solid")
        entry.grid(row=row, column=2, columnspan=2, sticky="w", padx=5)
        return {'entry': entry, 'var': var, 'row': row, 'label': label}

    def on_db_change(self, event=None):
        # Сбрасываем подключение при смене СУБД
        self.connected = False
        self.status_canvas.itemconfig(self.status_indicator, fill="red")
        self.connect_button.config(state="normal", bg="#4CAF50")
        # Скрываем кнопку взаимодействия при смене СУБД
        # Проверяем, существует ли кнопка перед тем, как скрывать её
        if self.interaction_button:
            self.interaction_button.grid_remove()
        # Скрываем фрейм структуры БД при смене СУБД
        self.hide_structure_frame()
        # Остальной код — скрываем кнопки и поля
        self.fetch_db_button.grid_remove()
        self.fetch_server_button.grid_remove()  # ← НОВОЕ: Скрываем кнопку сервера
        self.db_list_combo.grid_remove()
        self.db_selection_mode = False
        is_pg = self.db_type.get() == "PostgreSQL"
        # Скрыть все Entry и Label
        for key in self.widgets:
            self.widgets[key]['entry'].grid_forget()
            self.widgets[key]['label'].grid_forget()
        # Скрыть MS SQL-specific widgets
        if hasattr(self, 'auth_label'):
            self.auth_label.grid_forget()
        if hasattr(self, 'sql_radio'):
            self.sql_radio.grid_forget()
        if hasattr(self, 'windows_radio'):
            self.windows_radio.grid_forget()
        if is_pg:
            for key in ['pg_host', 'pg_port', 'pg_db', 'pg_user', 'pg_pass']:
                row = self.widgets[key]['row']
                self.widgets[key]['entry'].grid(row=row, column=2, columnspan=2, sticky="w", padx=5)
                self.widgets[key]['label'].grid(row=row, column=1, sticky="w", padx=5, pady=5)
            # Показываем кнопку сервера для Postgres
            self.fetch_server_button.grid(row=1, column=4, padx=5)
        else:
            for key in ['ms_server', 'ms_db', 'ms_user', 'ms_pass']:
                row = self.widgets[key]['row']
                self.widgets[key]['entry'].grid(row=row, column=2, columnspan=2, sticky="w", padx=5)
                self.widgets[key]['label'].grid(row=row, column=1, sticky="w", padx=5, pady=5)
            # Показываем кнопку сервера для MSSQL
            self.fetch_server_button.grid(row=6, column=4, padx=5)
            # Аутентификация
            auth_row = self.widgets['ms_pass']['row'] + 1
            self.auth_label.grid(row=auth_row, column=1, sticky="w", padx=5, pady=5)
            self.sql_radio.grid(row=auth_row, column=2, sticky="w")
            self.windows_radio.grid(row=auth_row, column=3, sticky="w")
            self.on_auth_change()
        # Активировать кнопку подключения (она уже активна из-за сброса выше)
        # Можно оставить или убрать — не критично
        self.enable_connect_button()

    def on_auth_change(self):
        is_sql = self.auth_type.get() == "sql"
        if is_sql:
            for key in ['ms_user', 'ms_pass']:
                row = self.widgets[key]['row']
                self.widgets[key]['entry'].grid(row=row, column=2, columnspan=2, sticky="w", padx=5)
                self.widgets[key]['label'].grid(row=row, column=1, sticky="w", padx=5, pady=5)
        else:
            for key in ['ms_user', 'ms_pass']:
                self.widgets[key]['entry'].grid_forget()
                self.widgets[key]['label'].grid_forget()
        self.enable_connect_button()

    def get_local_sql_instances(self):
        """
        Получить список локальных экземпляров SQL Server через PowerShell.
        """
        try:
            import json
            # Запускаем PowerShell-команду для получения списка экземпляров
            ps_command = '''
Get-WmiObject -Class Win32_Service | Where-Object {$_.Name -like "*SQL*"} | Select-Object Name, State, StartMode | ConvertTo-Json
'''
            # Добавляем параметры для скрытия окна PowerShell
            result = subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps_command],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            )
            if result.returncode == 0:
                services = json.loads(result.stdout)
                instances = []
                for service in services:
                    name = service.get("Name", "")
                    if "MSSQL$" in name:
                        instance_name = name.replace("MSSQL$", "")
                        if instance_name == "MSSQLSERVER":
                            instances.append("localhost")
                        else:
                            instances.append(f"localhost\\{instance_name}")
                return instances
            else:
                return []
        except Exception:
            return []

    def get_local_postgres_instances(self):
        """
        Проверить, запущен ли PostgreSQL на локальной машине.
        """
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq postgres.exe"],
                capture_output=True, text=True
            )
            if "postgres.exe" in result.stdout:
                return ["localhost"]
            else:
                return []
        except Exception:
            return []

    def scan_network_for_servers(self):
        """
        Получить список локальных и сетевых серверов.
        """
        local_sql = self.get_local_sql_instances()
        local_pg = self.get_local_postgres_instances()
        network_sql = self.get_sql_servers_via_sqlcmd()
        # Объединяем списки
        all_servers = list(set(local_sql + network_sql))
        # Для PostgreSQL возвращаем только локальные
        if self.db_type.get() == "PostgreSQL":
            return local_pg
        else:
            return all_servers

    def get_sql_servers_via_sqlcmd(self):
        """
        Получить список серверов через sqlcmd -L.
        """
        try:
            result = subprocess.run(
                ["sqlcmd", "-L"],
                capture_output=True, text=True,
                shell=True
            )
            if result.returncode == 0:
                servers = []
                for line in result.stdout.splitlines():
                    if "Server:" in line:
                        server = line.split(":")[1].strip()
                        servers.append(server)
                return servers
            else:
                return []
        except Exception:
            return []

    def get_local_subnet(self):
        """
        Получить локальную подсеть (например, 192.168.1) из текущего IP.
        """
        try:
            # Получить IP-адрес текущего компьютера
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            # Вернуть подсеть (например, 192.168.1)
            return ".".join(ip.split(".")[:-1])
        except Exception:
            return None

    def get_real_servers(self):
        """
        Попытка получить список реальных серверов из сети.
        Возвращает список серверов или None, если не удалось.
        Если None - используется заглушка.
        """
        try:
            discovered_servers = self.scan_network_for_servers()
            if discovered_servers:
                return discovered_servers
            else:
                return None
        except Exception:
            return None

    # !!!! Метод получения списка серверов - ОПИСАТЬ !!!!
    def fetch_servers(self):
        # Создаем окно с прогресс баром
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Поиск серверов...")
        progress_window.geometry("300x100")
        progress_window.resizable(False, False)
        # Центрируем окно
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (progress_window.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (progress_window.winfo_height() // 2)
        progress_window.geometry(f"+{x}+{y}")
        # Надпись
        label = tk.Label(progress_window, text="Поиск серверов...", font=("Arial", 10))
        label.pack(pady=10)
        # Прогресс бар
        progress = ttk.Progressbar(progress_window, mode='indeterminate')
        progress.pack(padx=20, pady=10, fill='x')
        progress.start()
        # Переменная для хранения результатов
        result_var = []

        def search_servers():
            try:
                # Попытаться получить список реальных серверов
                real_servers = self.get_real_servers()
                if real_servers:
                    # Если удалось получить реальные серверы — использовать их
                    result_var.extend(real_servers)
                else:
                    messagebox.showinfo("Информация", "Серверы не найдены.")
            finally:
                # Останавливаем прогресс бар и закрываем окно
                progress.stop()
                progress_window.destroy()
                # Показываем список серверов в отдельном окне
                if result_var:
                    self.show_server_list_window(result_var)

        # Запускаем поиск в отдельном потоке
        thread = threading.Thread(target=search_servers)
        thread.daemon = True
        thread.start()

    def show_server_list_window(self, servers):
        # Закрываем предыдущее окно, если открыто
        if self.db_list_window is not None:
            self.db_list_window.destroy()
        # Создаем новое всплывающее окно
        self.db_list_window = tk.Toplevel(self.root)
        self.db_list_window.title("Выберите сервер")
        self.db_list_window.geometry("300x200")
        self.db_list_window.resizable(False, False)
        # Создаем Listbox
        self.db_listbox = tk.Listbox(
            self.db_list_window,
            selectmode=tk.SINGLE,
            background="white",
            selectbackground="blue",
            selectforeground="white",
            activestyle="dotbox"
        )
        self.db_listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        # Добавляем серверы в Listbox
        for server in servers:
            self.db_listbox.insert(tk.END, server)
        # Привязываем событие выбора элемента
        self.db_listbox.bind("<<ListboxSelect>>", self.on_server_listbox_selected)
        # Привязываем событие наведение мыши
        self.db_listbox.bind("<Motion>", self.on_listbox_hover)
        # Центрируем окно относительно основного окна
        self.center_window(self.db_list_window)

    def on_server_listbox_selected(self, event):
        if not self.db_listbox.curselection():
            return
        selected = self.db_listbox.get(self.db_listbox.curselection()[0])
        # Закрываем окно списка
        self.db_list_window.destroy()
        self.db_list_window = None
        # Устанавливаем выбранный сервер в поле ввода
        if self.db_type.get() == "PostgreSQL":
            self.widgets['pg_host']['var'].set(selected)
        else:
            self.widgets['ms_server']['var'].set(selected)
        # Показываем подсказку (Только если не показывали ранее)
        self.show_auth_hint_if_needed()

    def on_listbox_hover(self, event):
        index = self.db_listbox.nearest(event.y)
        self.db_listbox.selection_clear(0, tk.END)
        self.db_listbox.selection_set(index)
        self.db_listbox.activate(index)

    def show_auth_hint_if_needed(self):
        # Проверяем, была ли подсказка уже показана
        config = configparser.ConfigParser()
        if os.path.exists(CONFIG_FILE):
            config.read(CONFIG_FILE, encoding="utf-8")
            # Проверяем флаг в файле конфиг
            hint_shown = config.getboolean('HINTS', 'auth_hint_shown', fallback=False)
            if not hint_shown:
                # Показываем подсказку
                if self.db_type.get() == "PostgreSQL":
                    messagebox.showinfo("Подсказка", "Для подключения к PostgreSQL нужно ввести логин и пароль.")
                else:
                    if self.auth_type.get() == "windows":
                        messagebox.showinfo("Подсказка",
                                            "Вы выбрали Windows-аутентификацию — вводить логин и пароль не нужно.")
                    else:
                        messagebox.showinfo("Подсказка", "Для подключения к MSSQL нужно ввести логин и пароль.")
                # Сохраняем, что подсказка была показана
                if not config.has_section('HINTS'):
                    config.add_section('HINTS')
                config.set('HINTS', 'auth_hint_shown', 'True')
                with open(CONFIG_FILE, "w", encoding='utf-8') as f:
                    config.write(f)

    # !!!! Метод получения списка БД - ОПИСАТЬ !!!!
    def fetch_databases(self):
        # Попытка подключения к реальному серверу
        try:
            if self.db_type.get() == "PostgreSQL":
                conn = psycopg2.connect(
                    host=self.widgets['pg_host']['var'].get().strip(),
                    port=self.widgets['pg_port']['var'].get().strip(),
                    dbname='postgres',
                    user=self.widgets['pg_user']['var'].get().strip(),
                    password=self.widgets['pg_pass']['var'].get(),
                )
                query = "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname;"
            else:
                server = self.widgets['ms_server']['var'].get().strip()
                database_name = 'master'  # Используем master для получения списка баз
                if self.auth_type.get() == "windows":
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database_name};"
                        f"Trusted_Connection=yes;"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database_name};"
                        f"UID={self.widgets['ms_user']['var'].get().strip()};"
                        f"PWD={self.widgets['ms_pass']['var'].get()}"
                    )
                conn = pyodbc.connect(conn_str)
                query = "SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name;"
            cur = conn.cursor()
            cur.execute(query)
            databases = [row[0] for row in cur.fetchall()]
            cur.close()
            conn.close()
            # Показываем список БД в отдельном окне с Listbox
            self.show_db_list_window(databases)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось получить список баз данных:\n{str(e)}")

    def show_db_list_window(self, databases):
        # Закрываем предыдущее окно, если открыто
        if self.db_list_window is not None:
            self.db_list_window.destroy()
        # Создаем новое всплывающее окно
        self.db_list_window = tk.Toplevel(self.root)
        self.db_list_window.title("Выберите базу данных")
        self.db_list_window.geometry("300x200")
        self.db_list_window.resizable(False, False)
        # Создаем Listbox
        self.db_listbox = tk.Listbox(self.db_list_window, selectmode=tk.SINGLE, background="white",
                                     selectbackground="blue", selectforeground="white", activestyle="dotbox")
        self.db_listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        # Добавляем базы данных в Listbox
        for db in databases:
            self.db_listbox.insert(tk.END, db)
        # Привязываем событие выбора элемента
        self.db_listbox.bind("<<ListboxSelect>>", self.on_db_listbox_selected)
        # Привязываем событие наведение мыши
        self.db_listbox.bind("<Motion>", self.on_listbox_hover)
        # Центрируем окно относительно основного окна
        self.center_window(self.db_list_window)

    def on_db_listbox_selected(self, event):
        if not self.db_listbox.curselection():
            return
        selected = self.db_listbox.get(self.db_listbox.curselection()[0])
        # Закрываем окно списка
        self.db_list_window.destroy()
        self.db_list_window = None
        # Устанавливаем выбранную БД в поле ввода
        if self.db_type.get() == "PostgreSQL":
            self.widgets['pg_db']['var'].set(selected)
        else:
            self.widgets['ms_db']['var'].set(selected)
        # Показываем поле ввода ("База данных") обратно
        if self.db_type.get() == "PostgreSQL":
            self.widgets['pg_db']['entry'].grid(row=3, column=2, columnspan=2, sticky="w", padx=5)
        else:
            self.widgets['ms_db']['entry'].grid(row=self.widgets['ms_db']['row'], column=2, columnspan=2, sticky="w", padx=5)
        # После выбора БД — обновляем состояние кнопки "Подключиться"
        self.enable_connect_button()
        # Если мы были подключены - оставляем кнопку "Подключиться" disabled
        if self.connected:
            self.connect_button.config(state="disabled", bg="lightgray")
        # Показываем кнопку "..." рядом с полем базы данных
        if self.db_type.get() == "PostgreSQL":
            self.fetch_db_button.grid(row=3, column=4, padx=5)
        else:
            self.fetch_db_button.grid(row=self.widgets['ms_db']['row'], column=4, padx=5)

    def center_window(self, window):
        window.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (window.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (window.winfo_height() // 2)
        window.geometry(f"+{x}+{y}")

    def enable_connect_button(self):
        # Проверяем, заполнены ли все обязательные поля (кроме "База данных")
        is_pg = self.db_type.get() == "PostgreSQL"
        if is_pg:
            host = self.widgets['pg_host']['var'].get().strip()
            port = self.widgets['pg_port']['var'].get().strip()
            user = self.widgets['pg_user']['var'].get().strip()
            password = self.widgets['pg_pass']['var'].get().strip()
            # База данных не обязательна для активации кнопки
            if all([host, port, user, password]):
                self.connect_button.config(state="normal", bg="#4CAF50")
            else:
                self.connect_button.config(state="disabled", bg="lightgray")
        else:
            server = self.widgets['ms_server']['var'].get().strip()
            if self.auth_type.get() == "windows":
                # Для Windows-аутентификации: только сервер обязателен
                if server:
                    self.connect_button.config(state="normal", bg="#4CAF50")
                else:
                    self.connect_button.config(state="disabled", bg="lightgray")
            else:
                user = self.widgets['ms_user']['var'].get().strip()
                password = self.widgets['ms_pass']['var'].get().strip()
                # Для SQL-аутентификации: сервер, пользователь, пароль
                if all([server, user, password]):
                    self.connect_button.config(state="normal", bg="#4CAF50")
                else:
                    self.connect_button.config(state="disabled", bg="lightgray")

    def connect(self):
        try:
            self.status_canvas.itemconfig(self.status_indicator, fill="orange")
            self.root.update_idletasks()
            # Подключаемся к реальному серверу
            if self.db_type.get() == "PostgreSQL":
                conn = psycopg2.connect(
                    host=self.widgets['pg_host']['var'].get().strip(),
                    port=self.widgets['pg_port']['var'].get().strip(),
                    dbname=self.widgets['pg_db']['var'].get().strip(),
                    user=self.widgets['pg_user']['var'].get().strip(),
                    password=self.widgets['pg_pass']['var'].get()
                )
            else:
                server = self.widgets['ms_server']['var'].get().strip()
                database = self.widgets['ms_db']['var'].get().strip()
                if self.auth_type.get() == "windows":
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database};"
                        f"Trusted_Connection=yes;"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database};"
                        f"UID={self.widgets['ms_user']['var'].get().strip()};"
                        f"PWD={self.widgets['ms_pass']['var'].get()}"
                    )
                conn = pyodbc.connect(conn_str)
            conn.close()
            self.status_canvas.itemconfig(self.status_indicator, fill="green")
            messagebox.showinfo("Успех",
                                "Подключение успешно установлено! "
                                "Выберите базу данных с помощью нажатия на кнопку '...'")
            self.save_config()
            self.connect_button.config(state="disabled", bg="lightgray")
            self.connected = True
            # Показываем кнопку "..." при успешном подключении
            if self.db_type.get() == "PostgreSQL":
                self.fetch_db_button.grid(row=3, column=4, padx=5)
            else:
                self.fetch_db_button.grid(row=self.widgets['ms_db']['row'], column=4, padx=5)
            # Показываем кнопку "Взаимодействие с БД"
            self.show_interaction_button()
        except Exception as e:
            self.status_canvas.itemconfig(self.status_indicator, fill="red")
            messagebox.showerror("Ошибка подключения", f"Не удалось подключиться:\n{str(e)}")
            self.connected = False

    def show_interaction_button(self):
        """Показать кнопку 'Взаимодействие с БД'"""
        # Проверяем, существует ли кнопка, и создаем её, если нет
        if self.interaction_button is None:
            self.interaction_button = tk.Button(
                self.root, text="Взаимодействие с БД", command=self.show_interaction_menu,
                width=20, bg="#2196F3", fg="white"
            )
            self.interaction_button.grid(row=13, column=1, columnspan=3, pady=5)
        else:
            # Если кнопка уже существует, просто отображаем её
            self.interaction_button.grid()

    def show_interaction_menu(self):
        """Показать меню взаимодействия с БД"""
        # Создаем всплывающее окно с меню
        menu_window = tk.Toplevel(self.root)
        menu_window.title("Взаимодействие с БД")
        menu_window.geometry("300x150")
        menu_window.resizable(False, False)
        # Центрируем окно
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (menu_window.winfo_width() // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (menu_window.winfo_height() // 2)
        menu_window.geometry(f"+{x}+{y}")
        # Создаем список доступных действий
        actions = ["Просмотр структуры выбранной БД"]
        action_var = tk.StringVar()
        tk.Label(menu_window, text="Выберите действие:", font=("Arial", 10)).pack(pady=10)
        for action in actions:
            rb = tk.Radiobutton(menu_window, text=action, variable=action_var, value=action)
            rb.pack(anchor="w", padx=20, pady=2)
        # Кнопка OK
        def on_ok():
            selected_action = action_var.get()
            if selected_action:
                if selected_action == "Просмотр структуры выбранной БД":
                    self.show_db_structure()
                menu_window.destroy()
            else:
                messagebox.showwarning("Предупреждение", "Выберите действие!")
        tk.Button(menu_window, text="OK", command=on_ok).pack(pady=10)

    def show_db_structure(self):
        """Показать структуру выбранной БД"""
        # Сначала проверяем, выбрана ли база данных
        if self.db_type.get() == "PostgreSQL":
            db_name = self.widgets['pg_db']['var'].get().strip()
        else:
            db_name = self.widgets['ms_db']['var'].get().strip()
        if not db_name:
            messagebox.showwarning("Предупреждение", "Сначала выберите базу данных!")
            return
        # Если уже открыто окно со структурой, закрываем его
        if self.structure_frame:
            self.structure_frame.destroy()
        # Создаем фрейм для отображения структуры БД
        self.structure_frame = tk.Frame(self.root, bd=2, relief="groove")
        self.structure_frame.grid(row=14, column=0, columnspan=5, sticky="ew", padx=10, pady=10)
        # Добавляем заголовок
        tk.Label(self.structure_frame, text=f"Структура БД: {db_name}", font=("Arial", 10, "bold")).pack(pady=5)
        # Создаем Text widget с Scrollbar для отображения структуры
        text_frame = tk.Frame(self.structure_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        # Горизонтальный и вертикальный скролбары
        v_scrollbar = tk.Scrollbar(text_frame, orient=tk.VERTICAL)
        h_scrollbar = tk.Scrollbar(text_frame, orient=tk.HORIZONTAL)
        self.structure_text = tk.Text(
            text_frame,
            wrap=tk.NONE,
            yscrollcommand=v_scrollbar.set,
            xscrollcommand=h_scrollbar.set,
            height=10
        )
        v_scrollbar.config(command=self.structure_text.yview)
        h_scrollbar.config(command=self.structure_text.xview)
        self.structure_text.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        # Получаем и отображаем структуру БД
        structure_info = self.get_db_structure()
        self.structure_text.insert(tk.END, structure_info)
        self.structure_text.config(state=tk.DISABLED)  # Только для чтения
        # Кнопка закрытия
        close_button = tk.Button(self.structure_frame, text="Закрыть", command=self.hide_structure_frame, bg="#f44336", fg="white")
        close_button.pack(pady=5)
        # --- УВЕЛИЧИВАЕМ ОКНО ---
        # Устанавливаем новую ширину и высоту (например, +200 по ширине и +150 по высоте)
        new_width = self.original_width + 200
        new_height = self.original_height + 150
        self.root.geometry(f"{new_width}x{new_height}")

    def get_db_structure(self):
        """Получить информацию о структуре БД"""
        try:
            # Реальное подключение к БД
            if self.db_type.get() == "PostgreSQL":
                conn = psycopg2.connect(
                    host=self.widgets['pg_host']['var'].get().strip(),
                    port=self.widgets['pg_port']['var'].get().strip(),
                    dbname=self.widgets['pg_db']['var'].get().strip(),
                    user=self.widgets['pg_user']['var'].get().strip(),
                    password=self.widgets['pg_pass']['var'].get()
                )
                # Получаем таблицы
                cur = conn.cursor()
                cur.execute("""
SELECT table_name, count(c.column_name) as column_count
FROM information_schema.tables t
JOIN information_schema.columns c ON t.table_name = c.table_name
WHERE t.table_schema = 'public'
GROUP BY table_name
ORDER BY table_name;
""")
                tables = cur.fetchall()
                # Получаем функции/процедуры
                cur.execute("""
SELECT proname
FROM pg_proc
JOIN pg_namespace ns ON pg_proc.pronamespace = ns.oid
WHERE ns.nspname = 'public'
ORDER BY proname;
""")
                procedures = cur.fetchall()
                cur.close()
                conn.close()
                # Формируем результат
                result = f"=== Структура базы данных: {self.widgets['pg_db']['var'].get()} ===\n"
                result += "[ТАБЛИЦЫ]\n"
                for table in tables:
                    result += f"{table[0]} - {table[1]} столбцов\n"
                result += "\n[ХРАНИМЫЕ ПРОЦЕДУРЫ]\n"
                for proc in procedures:
                    result += f"{proc[0]}\n"
                result += f"\nВсего таблиц: {len(tables)}\n"
                result += f"Всего хранимых процедур: {len(procedures)}\n"
            else:  # MSSQL
                server = self.widgets['ms_server']['var'].get().strip()
                database = self.widgets['ms_db']['var'].get().strip()
                if self.auth_type.get() == "windows":
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database};"
                        f"Trusted_Connection=yes;"
                    )
                else:
                    conn_str = (
                        f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                        f"SERVER={server};"
                        f"DATABASE={database};"
                        f"UID={self.widgets['ms_user']['var'].get().strip()};"
                        f"PWD={self.widgets['ms_pass']['var'].get()}"
                    )
                conn = pyodbc.connect(conn_str)
                # Получаем таблицы
                cur = conn.cursor()
                cur.execute("""
SELECT t.name AS table_name, COUNT(c.column_id) AS column_count
FROM sys.tables t
LEFT JOIN sys.columns c ON t.object_id = c.object_id
GROUP BY t.name
ORDER BY t.name;
""")
                tables = cur.fetchall()
                # Получаем хранимые процедуры
                cur.execute("""
SELECT name
FROM sys.procedures
ORDER BY name;
""")
                procedures = cur.fetchall()
                cur.close()
                conn.close()
                # Формируем результат
                result = f"=== Структура базы данных: {self.widgets['ms_db']['var'].get()} ===\n"
                result += "[ТАБЛИЦЫ]\n"
                for table in tables:
                    result += f"{table[0]} - {table[1]} столбцов\n"
                result += "\n[ХРАНИМЫЕ ПРОЦЕДУРЫ]\n"
                for proc in procedures:
                    result += f"{proc[0]}\n"
                result += f"\nВсего таблиц: {len(tables)}\n"
                result += f"Всего хранимых процедур: {len(procedures)}\n"
            return result
        except Exception as e:
            return f"Ошибка при получении структуры БД: {str(e)}"

    def hide_structure_frame(self):
        """Скрыть фрейм со структурой БД"""
        if self.structure_frame:
            self.structure_frame.destroy()
            self.structure_frame = None
            self.structure_text = None
            # --- ВОЗВРАЩАЕМ ИСХОДНЫЙ РАЗМЕР ОКНА ---
            self.root.geometry(f"{self.original_width}x{self.original_height}")

    def save_config(self):
        config = configparser.ConfigParser()
        config['DB'] = {
            'type': self.db_type.get(),
            'pg_host': self.widgets['pg_host']['var'].get(),
            'pg_port': self.widgets['pg_port']['var'].get(),
            'pg_db': self.widgets['pg_db']['var'].get(),
            'pg_user': self.widgets['pg_user']['var'].get(),
            'ms_server': self.widgets['ms_server']['var'].get(),
            'ms_db': self.widgets['ms_db']['var'].get(),
            'auth_type': self.auth_type.get(),
            'ms_user': self.widgets['ms_user']['var'].get(),
        }
        # Добавляем секцию HINTS и сохраняем флаг
        if not config.has_section('HINTS'):
            config.add_section('HINTS')
        config.set('HINTS', 'auth_hint_shown', 'True')
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            config.write(f)

    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            config = configparser.ConfigParser()
            config.read(CONFIG_FILE, encoding='utf-8')
            db = config['DB']
            self.db_type.set(db.get('type', 'PostgreSQL'))
            self.widgets['pg_host']['var'].set(db.get('pg_host', 'localhost'))
            self.widgets['pg_port']['var'].set(db.get('pg_port', '5432'))
            self.widgets['pg_db']['var'].set(db.get('pg_db', ''))
            self.widgets['pg_user']['var'].set(db.get('pg_user', ''))
            self.widgets['ms_server']['var'].set(db.get('ms_server', ''))
            self.widgets['ms_db']['var'].set(db.get('ms_db', ''))
            self.auth_type.set(db.get('auth_type', 'sql'))
            self.widgets['ms_user']['var'].set(db.get('ms_user', ''))
            self.on_db_change()
        except Exception as e:
            messagebox.showwarning("Предупреждение", f"Не удалось загрузить config.ini:\n{str(e)}")

if __name__ == "__main__":
    root = tk.Tk()
    app = DBConnectionApp(root)
    root.mainloop()