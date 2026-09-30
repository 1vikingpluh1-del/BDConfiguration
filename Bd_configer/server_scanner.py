# ==============================================================================
# server_scanner.py — Сканер серверов в локальной сети
# ==============================================================================
# Отвечает за:
#   - Обнаружение локальных экземпляров PostgreSQL
#   - Обнаружение локальных экземпляров SQL Server
#   - Обнаружение сетевых серверов SQL Server
#   - Сканирование порта для проверки доступности сервера
#   - Определение локальной подсети
#
# КРОССПЛАТФОРМЕННОСТЬ:
#   - Windows: tasklist, PowerShell/WMI, sqlcmd
#   - Linux (Astra Linux и др.): pgrep/systemctl, сканирование портов
#
# Все методы «тихие» — при ошибке возвращают пустой список,
# а не выбрасывают исключение. Это важно для UX: если сканирование
# не удалось — просто показываем пустой список, а не crash.
#
# !!!! TODO: Добавить сканирование по портам по подсети (порт 5432 для PG, 1433 для MSSQL)
# !!!! TODO: Добавить сканирование через UDP-broadcast для MSSQL
# ==============================================================================

import json
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from constants import DB_TYPE_POSTGRES, DB_TYPE_MSSQL, IS_WINDOWS, IS_LINUX


class ServerScanner:
    """
    Сканер серверов баз данных в локальной сети.

    Кроссплатформенный: автоматически выбирает способ поиска
    в зависимости от ОС (Windows / Linux / Astra Linux).

    Умеет находить:
      - Локальные экземпляры PostgreSQL
        • Windows: по процессу postgres.exe (tasklist)
        • Linux:   по процессу postgres (pgrep) или сервису (systemctl)
      - Локальные экземпляры SQL Server
        • Windows: по WMI-сервисам MSSQL$* (PowerShell)
        • Linux:   по процессу sqlservr (pgrep) — для MSSQL on Linux
      - Сетевые экземпляры SQL Server (через sqlcmd -L, если доступен)
      - Доступность сервера по порту (кроссплатформенный socket-check)
      - Локальную подсеть (для будущего сканирования портов)

    Пример:
        scanner = ServerScanner()
        servers = scanner.discover_servers("PostgreSQL")
        # ['localhost']  — или пустой список, если PG не запущен
    """

    # --- Стандартные порты СУБД (для проверки через сокет) ---
    DEFAULT_PORTS = {
        DB_TYPE_POSTGRES: 5432,
        DB_TYPE_MSSQL: 1433,
    }

    # -------------------------------------------------------------------------
    # Главный метод — точка входа для поиска серверов
    # -------------------------------------------------------------------------
    def discover_servers(self, db_type: str) -> list:
        """
        Получить список доступных серверов для указанного типа СУБД.

        Объединяет результаты локального и сетевого поиска.
        Автоматически выбирает метод поиска в зависимости от ОС.

        Параметры:
            db_type (str): "PostgreSQL" или "Microsoft SQL Server"

        Возвращает:
            list[str] — список адресов серверов (без дубликатов)
        """
        if db_type == DB_TYPE_POSTGRES:
            # Для PostgreSQL — ищем локально
            # !!!! TODO: Добавить сканирование порта 5432 по подсети !!!!
            return self._get_local_postgres_instances()
        else:
            # Для MSSQL — локальные + сетевые серверы
            local = self._get_local_sql_instances()
            network = self._get_sql_servers_via_sqlcmd()
            # set() убирает дубликаты (например, localhost может быть и там, и там)
            return list(set(local + network))

    # =========================================================================
    # ЛОКАЛЬНЫЙ ПОИСК — PostgreSQL
    # =========================================================================

    def _get_local_postgres_instances(self) -> list:
        """
        Проверить, запущен ли PostgreSQL на локальной машине.

        Стратегия (в порядке приоритета):
          1. Windows → tasklist (ищем postgres.exe)
          2. Linux   → pgrep (ищем процесс postgres) + systemctl
          3. Fallback → проверяем порт 5432 через сокет (работает везде)

        Возвращает:
            ["localhost"] если PostgreSQL запущен, иначе [].
        """
        if IS_WINDOWS:
            return self._find_postgres_windows()
        elif IS_LINUX:
            return self._find_postgres_linux()
        else:
            # Fallback для любой ОС — просто проверяем порт
            return self._check_localhost_port(self.DEFAULT_PORTS[DB_TYPE_POSTGRES])

    def _find_postgres_windows(self) -> list:
        """
        [Windows] Поиск PostgreSQL через tasklist.

        Ищем процесс postgres.exe в списке задач.
        """
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq postgres.exe"],
                capture_output=True,
                text=True,
            )
            if "postgres.exe" in result.stdout:
                return ["localhost"]
            return []
        except Exception:
            return []

    def _find_postgres_linux(self) -> list:
        """
        [Linux / Astra Linux] Поиск PostgreSQL через pgrep или systemctl.

        Способ 1: pgrep -x postgres — ищет процесс с точным именем "postgres"
        Способ 2: systemctl is-active postgresql — проверяет systemd-сервис
        Способ 3: fallback — проверяем порт 5432 через сокет

        На Astra Linux PostgreSQL обычно установлен из репозитория
        и работает как systemd-сервис 'postgresql'.
        """
        # Способ 1: pgrep (быстрый и надёжный)
        try:
            result = subprocess.run(
                ["pgrep", "-x", "postgres"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return ["localhost"]
        except FileNotFoundError:
            # pgrep не установлен — пробуем systemctl
            pass
        except Exception:
            pass

        # Способ 2: systemctl (работает на Astra Linux и других systemd-дистрибутивах)
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "postgresql"],
                capture_output=True,
                text=True,
            )
            # systemctl is-active возвращает "active\n" если сервис запущен
            if result.stdout.strip() == "active":
                return ["localhost"]
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Способ 3: fallback — просто проверяем порт 5432
        return self._check_localhost_port(self.DEFAULT_PORTS[DB_TYPE_POSTGRES])

    # =========================================================================
    # ЛОКАЛЬНЫЙ ПОИСК — SQL Server
    # =========================================================================

    def _get_local_sql_instances(self) -> list:
        """
        Получить список локальных экземпляров SQL Server.

        Стратегия:
          - Windows → PowerShell/WMI (Win32_Service)
          - Linux   → pgrep sqlservr (MSSQL on Linux) + systemctl

        Возвращает:
            list[str] — список серверов
        """
        if IS_WINDOWS:
            return self._find_mssql_windows()
        elif IS_LINUX:
            return self._find_mssql_linux()
        else:
            return self._check_localhost_port(self.DEFAULT_PORTS[DB_TYPE_MSSQL])

    def _find_mssql_windows(self) -> list:
        """
        [Windows] Поиск MSSQL через PowerShell/WMI.

        Запрашиваем Win32_Service, фильтруем по имени "*SQL*",
        ищем сервисы вида MSSQL$INSTANCENAME.

        Пример:
            MSSQL$MSSQLSERVER → "localhost" (дефолтный экземпляр)
            MSSQL$SQLEXPRESS  → "localhost\\SQLEXPRESS" (именованный экземпляр)

        Примечание:
            - subprocess.CREATE_NO_WINDOW — чтобы не мелькало окно PowerShell
            - Параметр "-WindowStyle Hidden" — дополнительная подстраховка
        """
        try:
            # PowerShell-команда: получить список SQL-сервисов в формате JSON
            ps_command = (
                'Get-WmiObject -Class Win32_Service '
                '| Where-Object {$_.Name -like "*SQL*"} '
                '| Select-Object Name, State, StartMode '
                '| ConvertTo-Json'
            )

            # Запускаем PowerShell с подавлением окна
            result = subprocess.run(
                ["powershell", "-WindowStyle", "Hidden", "-Command", ps_command],
                capture_output=True,
                text=True,
                # CREATE_NO_WINDOW — чтобы не мелькала консоль
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            if result.returncode != 0:
                return []

            services = json.loads(result.stdout)

            # Если найден только один сервис, PowerShell вернёт dict вместо list
            if isinstance(services, dict):
                services = [services]

            instances = []
            # Получаем реальное имя компьютера вместо "localhost"
            hostname = socket.gethostname()
            for service in services:
                name = service.get("Name", "")
                # Сервисы SQL Server называются MSSQL$INSTANCENAME
                if "MSSQL$" in name:
                    instance_name = name.replace("MSSQL$", "")
                    if instance_name == "MSSQLSERVER":
                        # Дефолтный экземпляр — просто имя ПК
                        instances.append(hostname)
                    else:
                        # Именованный экземпляр — ИМЯ_ПК\ЭКЗЕМПЛЯР
                        instances.append(f"{hostname}\\{instance_name}")

            return instances

        except Exception:
            return []

    def _find_mssql_linux(self) -> list:
        """
        [Linux / Astra Linux] Поиск MSSQL через pgrep или systemctl.

        Microsoft SQL Server on Linux работает как процесс sqlservr
        и systemd-сервис mssql-server.

        Примечание:
            MSSQL on Linux не поддерживает именованные экземпляры —
            всегда возвращаем просто "localhost".
        """
        # Способ 1: pgrep sqlservr
        try:
            result = subprocess.run(
                ["pgrep", "-x", "sqlservr"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                return ["localhost"]
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Способ 2: systemctl (сервис mssql-server)
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "mssql-server"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip() == "active":
                return ["localhost"]
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Способ 3: fallback — проверяем порт 1433
        return self._check_localhost_port(self.DEFAULT_PORTS[DB_TYPE_MSSQL])

    # =========================================================================
    # СЕТЕВОЙ ПОИСК
    # =========================================================================

    def _get_sql_servers_via_sqlcmd(self) -> list:
        """
        Получить список серверов SQL Server через sqlcmd -L.

        sqlcmd -L использует SQL Server Browser Service для обнаружения
        серверов в локальной сети по UDP-broadcast на порт 1434.

        Работает и на Windows, и на Linux (если установлен mssql-tools).
        На Astra Linux: sudo apt install mssql-tools (из репозитория Microsoft).

        Возвращает:
            list[str] — список найденных серверов.

        Ограничения:
            - Требуется установленный sqlcmd
            - SQL Server Browser должен быть запущен на целевых серверах
            - Может не работать через VPN/сегменты сети
        """
        try:
            # На Windows shell=True нужен для поиска sqlcmd в PATH
            # На Linux shell=False безопаснее
            result = subprocess.run(
                ["sqlcmd", "-L"],
                capture_output=True,
                text=True,
                shell=IS_WINDOWS,
            )

            if result.returncode != 0:
                return []

            servers = []
            for line in result.stdout.splitlines():
                # sqlcmd -L выводит строки вида "Server: SERVER_NAME"
                if "Server:" in line:
                    server = line.split(":")[1].strip()
                    if server:  # Пропускаем пустые строки
                        servers.append(server)
            return servers

        except FileNotFoundError:
            # sqlcmd не установлен — это нормально на Linux без mssql-tools
            return []
        except Exception:
            return []

    # =========================================================================
    # УТИЛИТЫ
    # =========================================================================

    def _check_localhost_port(self, port: int, timeout: float = 1.0) -> list:
        """
        Проверить, слушает ли localhost указанный порт.

        Кроссплатформенный fallback-метод: работает на ЛЮБОЙ ОС.
        Используется, когда pgrep/tasklist/systemctl недоступны.

        Параметры:
            port (int): номер порта (5432 для PG, 1433 для MSSQL)
            timeout (float): таймаут подключения в секундах

        Возвращает:
            ["localhost"] если порт открыт, иначе [].
        """
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            # connect_ex возвращает 0 если подключение успешно
            result = sock.connect_ex(("localhost", port))
            sock.close()
            if result == 0:
                return ["localhost"]
            return []
        except Exception:
            return []

    def get_local_subnet(self) -> str:
        """
        Получить локальную подсеть (например, "192.168.1") из текущего IP.

        Используется для будущего сканирования портов по подсети.

        Механизм:
            1. Создаём UDP-сокет
            2. «Подключаемся» к Google DNS (8.8.8.8:80) — это НЕ отправляет данные,
               а просто определяет, через какой интерфейс пойдёт трафик
            3. Берём наш IP-адрес из сокета
            4. Отрезаем последний октет — получаем подсеть

        Возвращает:
            str — подсеть (например, "192.168.1"), или None при ошибке.
        """
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # connect() для UDP не создаёт реальное соединение,
            # а только определяет маршрут
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            # Отрезаем последний октет: "192.168.1.100" → "192.168.1"
            return ".".join(ip.split(".")[:-1])
        except Exception:
            return None