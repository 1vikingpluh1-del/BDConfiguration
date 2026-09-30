# ==============================================================================
# constants.py — Константы и настройки приложения
# ==============================================================================
# Централизованное хранилище всех "магических чисел", строк и параметров.
# Если нужно поменять размер окна, цвет кнопки или имя конфига —
# меняем ЗДЕСЬ, а не ищем по всему проекту.
# ==============================================================================

import os
import sys

# -----------------------------------------------------------------------------
# Определение платформы
# -----------------------------------------------------------------------------
# IS_WINDOWS / IS_LINUX — чтобы не писать os.name == 'nt' по всему проекту.
# На Astra Linux (Debian-based) IS_LINUX = True.
IS_WINDOWS = (os.name == 'nt')
IS_LINUX = (sys.platform.startswith('linux'))

# -----------------------------------------------------------------------------
# Пути к файлам
# -----------------------------------------------------------------------------
# CONFIG_FILE — имя ini-файла, в котором сохраняются настройки подключения
# (хост, порт, тип СУБД и т.д.). Пароль НЕ сохраняется (безопасность).
CONFIG_FILE = "config.ini"

# -----------------------------------------------------------------------------
# Информация о приложении
# -----------------------------------------------------------------------------
APP_VERSION = "1.5.2"
APP_DESCRIPTION = (
    "Вспомогательные инструменты для тестирования.\n\n"
    "Возможности программы:\n"
    "• Подключение к PostgreSQL и Microsoft SQL Server\n"
    "• Автоматический поиск серверов в локальной сети\n"
    "• Получение списка баз данных на сервере\n"
    "• Просмотр структуры БД (таблицы, хранимые процедуры)\n"
    "• Сохранение настроек подключения между сеансами\n"
    "• Реализовано добавление персонала и идентификаторов\n"
    "• Реализован Profiler(доработки)\n"
    "• Функционал просмотра marapRC(добавлены иконки)\n\n"
    "Поддерживаемые ОС: Windows, Astra Linux (Wine)"
)

# -----------------------------------------------------------------------------
# Параметры главного окна
# -----------------------------------------------------------------------------
WINDOW_TITLE = "Вспомогательные инструменты для тестирования 1.5.2 Fix"
WINDOW_WIDTH = 530
WINDOW_HEIGHT = 400

# На сколько увеличиваем окно при отображении структуры БД
# ОСТАВИЛ увеличенным для комфортного просмотра
STRUCTURE_EXTRA_WIDTH = 330
STRUCTURE_EXTRA_HEIGHT = 480

# -----------------------------------------------------------------------------
# Типы поддерживаемых СУБД
# -----------------------------------------------------------------------------
# В будущем можно добавить SQLite для АРМС3000
DB_TYPE_POSTGRES = "PostgreSQL"
DB_TYPE_MSSQL = "Microsoft SQL Server"
SUPPORTED_DB_TYPES = [DB_TYPE_POSTGRES, DB_TYPE_MSSQL]

# -----------------------------------------------------------------------------
# Типы аутентификации (для MSSQL)
# -----------------------------------------------------------------------------
AUTH_TYPE_SQL = "sql"           # SQL Server аутентификация (логин + пароль)
AUTH_TYPE_WINDOWS = "windows"   # Windows-аутентификация (Trusted_Connection)

# -----------------------------------------------------------------------------
# Цвета и стили
# -----------------------------------------------------------------------------
# Цвета индикатора подключения (красный/оранжевый/зелёный «светофор»)
COLOR_DISCONNECTED = "red"      # Не подключено
COLOR_CONNECTING = "orange"     # Идёт подключение...
COLOR_CONNECTED = "green"       # Подключено успешно

# Цвета кнопок
COLOR_CONNECT_BTN = "#4CAF50"       # Зелёная кнопка «Подключиться»
COLOR_INTERACT_BTN = "#2196F3"      # Синяя кнопка «Взаимодействие с БД»
COLOR_CLOSE_BTN = "#f44336"         # Красная кнопка «Закрыть»
COLOR_DISABLED_BTN = "lightgray"    # Неактивная кнопка

# -----------------------------------------------------------------------------
# Значения по умолчанию для полей ввода
# -----------------------------------------------------------------------------
DEFAULT_PG_HOST = "localhost"
DEFAULT_PG_PORT = "5432"

# -----------------------------------------------------------------------------
# ODBC-драйвер для MSSQL
# -----------------------------------------------------------------------------
# Если на машине другая версия драйвера — поменять здесь
MSSQL_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"

# -----------------------------------------------------------------------------
# SQL-запросы (вынесены из логики для удобства)
# -----------------------------------------------------------------------------
# Запрос списка баз данных PostgreSQL (без шаблонных БД)
SQL_PG_LIST_DATABASES = (
    "SELECT datname FROM pg_database "
    "WHERE datistemplate = false ORDER BY datname;"
)

# Запрос списка баз данных MSSQL (без системных: master, model, msdb, tempdb)
SQL_MS_LIST_DATABASES = (
    "SELECT name FROM sys.databases "
    "WHERE database_id > 4 ORDER BY name;"
)

# Запрос структуры БД PostgreSQL - таблицы и количество колонок
# Ищем во ВСЕХ пользовательских схемах (не только public),
# т.к. БД Орион Про может хранить таблицы в других схемах (например, dbo, orion и т.д.)
SQL_PG_TABLES = """
    SELECT t.table_schema || '.' || t.table_name AS full_name,
           COUNT(c.column_name) AS column_count
    FROM information_schema.tables t
    JOIN information_schema.columns c
        ON t.table_name = c.table_name AND t.table_schema = c.table_schema
    WHERE t.table_type = 'BASE TABLE'
      AND t.table_schema NOT IN ('pg_catalog', 'information_schema')
    GROUP BY t.table_schema, t.table_name
    ORDER BY t.table_schema, t.table_name;
"""

# Запрос хранимых процедур/функций PostgreSQL
# Ищем во всех пользовательских схемах (не только public)
SQL_PG_PROCEDURES = """
    SELECT ns.nspname || '.' || p.proname AS full_name
    FROM pg_proc p
    JOIN pg_namespace ns ON p.pronamespace = ns.oid
    WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY ns.nspname, p.proname;
"""

# Запрос структуры БД MSSQL — таблицы и количество колонок
SQL_MS_TABLES = """
    SELECT t.name AS table_name, COUNT(c.column_id) AS column_count
    FROM sys.tables t
    LEFT JOIN sys.columns c ON t.object_id = c.object_id
    GROUP BY t.name
    ORDER BY t.name;
"""

# Запрос хранимых процедур MSSQL
SQL_MS_PROCEDURES = """
    SELECT name
    FROM sys.procedures
    ORDER BY name;
"""

# ==============================================================================
# ЗАПРОСЫ ДЛЯ ДЕТАЛЬНОГО ПРОСМОТРА СТРУКТУРЫ (дерево с колонками)
# ==============================================================================

# PostgreSQL: список всех таблиц с их схемами
SQL_PG_TABLES_LIST = """
    SELECT t.table_schema, t.table_name, COUNT(c.column_name) as column_count
    FROM information_schema.tables t
    LEFT JOIN information_schema.columns c 
        ON t.table_name = c.table_name AND t.table_schema = c.table_schema
    WHERE t.table_type = 'BASE TABLE'
      AND t.table_schema NOT IN ('pg_catalog', 'information_schema')
    GROUP BY t.table_schema, t.table_name
    ORDER BY t.table_schema, t.table_name;
"""

# PostgreSQL: столбцы конкретной таблицы (schema + table передаются как параметры)
SQL_PG_COLUMNS = """
    SELECT c.column_name, c.data_type, c.is_nullable, c.column_default
    FROM information_schema.columns c
    WHERE c.table_schema = %s AND c.table_name = %s
    ORDER BY c.ordinal_position;
"""

# MSSQL: список всех таблиц со схемами
SQL_MS_TABLES_LIST = """
    SELECT s.name AS table_schema, t.name AS table_name, COUNT(c.column_id) as column_count
    FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    LEFT JOIN sys.columns c ON t.object_id = c.object_id
    GROUP BY s.name, t.name, t.object_id
    ORDER BY s.name, t.name;
"""

# MSSQL: столбцы конкретной таблицы
SQL_MS_COLUMNS = """
    SELECT c.name AS column_name,
           tp.name AS data_type,
           CASE WHEN c.is_nullable = 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
           dc.definition AS column_default
    FROM sys.columns c
    JOIN sys.types tp ON c.user_type_id = tp.user_type_id
    LEFT JOIN sys.default_constraints dc ON c.object_id = dc.object_id
    WHERE c.object_id = OBJECT_ID(? + '.' + ?)
    ORDER BY c.column_id;
"""