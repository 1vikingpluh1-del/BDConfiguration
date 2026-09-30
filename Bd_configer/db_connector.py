# ==============================================================================
# db_connector.py — Модуль подключения к базам данных
# ==============================================================================
# Отвечает за:
#   - Подключение к PostgreSQL (psycopg2) и MSSQL (pyodbc)
#   - Получение списков БД, таблиц, процедур, столбцов
#   - БЫСТРОЕ массовое добавление персонала (batch-вставка)
#   - Profiler через DMV (активные запросы с реальным SQL-текстом)
#
# Производительность массовой вставки:
#   - MSSQL: cursor.fast_executemany = True + executemany() — ~50-100x быстрее
#     одиночных INSERT'ов
#   - PostgreSQL: psycopg2.extras.execute_values() — ~20-50x быстрее
#   - Оба варианта используют ОДНО соединение на всю операцию
# ==============================================================================

import os
os.environ['PGCLIENTENCODING'] = 'UTF8'
import psycopg2
from psycopg2.extras import execute_values
import pyodbc
from constants import (
    DB_TYPE_POSTGRES,
    DB_TYPE_MSSQL,
    AUTH_TYPE_WINDOWS,
    MSSQL_ODBC_DRIVER,
    SQL_PG_LIST_DATABASES,
    SQL_MS_LIST_DATABASES,
    SQL_PG_TABLES,
    SQL_PG_PROCEDURES,
    SQL_MS_TABLES,
    SQL_MS_PROCEDURES,
    SQL_PG_TABLES_LIST,
    SQL_PG_COLUMNS,
    SQL_MS_TABLES_LIST,
    SQL_MS_COLUMNS,
)


# Описание необязательных полей persona (общее для PG и MSSQL).
# (ключ_словаря, имя_колонки_pg, имя_колонки_ms, нужно_ли_strip)
_OPTIONAL_PERSON_FIELDS = [
    ('mid_name',    'midname',    'MidName',    True),
    ('company_id',  'company',    'Company',    False),
    ('division_id', 'section',    'Section',    False),
    ('post_id',     'post',       'Post',       False),
    ('tab_number',  'tabnumber',  'TabNumber',  True),
    ('work_phone',  'workphone',  'WorkPhone',  True),
    ('home_phone',  'homephone',  'HomePhone',  True),
    ('email',       'emaillist',  'EmailList',  True),
    ('birth_date',  'birthdate',  'BirthDate',  False),
    ('address',     'address',    'Address',    True),
    ('picture',     'picture',    'Picture',    False),
]

# Размер batch'а для пакетной вставки. Подбирается экспериментально:
# - Слишком маленький — много round-trip к БД
# - Слишком большой — большой расход памяти, риск таймаута
# - 1000 — хороший компромисс для записей без фото
# - С фото — снижаем до 100, т.к. строки тяжелее (фото = десятки/сотни КБ)
_BATCH_SIZE_NO_PHOTO = 1000
_BATCH_SIZE_WITH_PHOTO = 100


class DBConnector:
    """Универсальный коннектор к базам данных."""

    def __init__(self):
        # Словарь session_name → имя целевой БД.
        # Используется для фильтрации событий XEvents на стороне Python:
        # сессия ловит ВСЕ события, а в profiler_poll мы оставляем только
        # те, у которых database_name == target_db.
        self._xe_target_db = {}

    # =========================================================================
    # БАЗОВЫЕ ОПЕРАЦИИ
    # =========================================================================

    def test_connection(self, db_type: str, params: dict) -> None:
        conn = self._create_connection(db_type, params)
        conn.close()

    def fetch_databases(self, db_type: str, params: dict) -> list:
        connect_params = params.copy()
        if db_type == DB_TYPE_POSTGRES:
            connect_params['dbname'] = 'postgres'
            query = SQL_PG_LIST_DATABASES
        else:
            connect_params['database'] = 'master'
            query = SQL_MS_LIST_DATABASES

        conn = self._create_connection(db_type, connect_params)
        try:
            cur = conn.cursor()
            cur.execute(query)
            databases = [row[0] for row in cur.fetchall()]
            cur.close()
            return databases
        finally:
            conn.close()

    def get_tables_list(self, db_type: str, params: dict) -> list:
        conn = self._create_connection(db_type, params)
        try:
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute(SQL_PG_TABLES_LIST)
            else:
                cur.execute(SQL_MS_TABLES_LIST)
            result = cur.fetchall()
            cur.close()
            return result
        finally:
            conn.close()

    def get_procedures_list(self, db_type: str, params: dict) -> list:
        conn = self._create_connection(db_type, params)
        try:
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute(SQL_PG_PROCEDURES)
            else:
                cur.execute(SQL_MS_PROCEDURES)
            result = cur.fetchall()
            cur.close()
            return result
        finally:
            conn.close()

    def get_table_columns(self, db_type: str, params: dict, schema: str, table: str) -> list:
        conn = self._create_connection(db_type, params)
        try:
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute(SQL_PG_COLUMNS, (schema, table))
            else:
                cur.execute(SQL_MS_COLUMNS, (schema, table))
            result = cur.fetchall()
            cur.close()
            return result
        finally:
            conn.close()

    def get_full_structure_for_export(self, db_type: str, params: dict) -> str:
        """Сформировать текстовый отчёт по структуре БД (одно соединение)."""
        conn = self._create_connection(db_type, params)
        try:
            cur = conn.cursor()
            lines = []

            if db_type == DB_TYPE_POSTGRES:
                cur.execute(SQL_PG_TABLES_LIST)
            else:
                cur.execute(SQL_MS_TABLES_LIST)
            tables = cur.fetchall()

            lines.append("=" * 70)
            lines.append(f"СТРУКТУРА БД ({db_type})")
            lines.append("=" * 70)
            lines.append(f"Всего таблиц: {len(tables)}")
            lines.append("")

            schemas = {}
            for schema, table_name, column_count in tables:
                schemas.setdefault(schema, []).append((table_name, column_count))

            for schema, table_list in schemas.items():
                lines.append(f"СХЕМА: {schema} ({len(table_list)} таблиц)")
                lines.append("-" * 70)
                for table_name, column_count in table_list:
                    lines.append(f"  📋 {table_name} ({column_count} столбцов)")
                    if db_type == DB_TYPE_POSTGRES:
                        cur.execute(SQL_PG_COLUMNS, (schema, table_name))
                    else:
                        cur.execute(SQL_MS_COLUMNS, (schema, table_name))
                    for col in cur.fetchall():
                        col_name = col[0] or '???'
                        col_type = col[1] or '???'
                        nullable = 'NULL' if (col[2] or '').upper() == 'YES' else 'NOT NULL'
                        default = f" DEFAULT {col[3]}" if col[3] else ''
                        lines.append(f"      {col_name:30} {col_type:20} {nullable}{default}")
                    lines.append("")
                lines.append("")

            try:
                if db_type == DB_TYPE_POSTGRES:
                    cur.execute(SQL_PG_PROCEDURES)
                else:
                    cur.execute(SQL_MS_PROCEDURES)
                procs = cur.fetchall()
                lines.append(f"ХРАНИМЫЕ ПРОЦЕДУРЫ: {len(procs)}")
                lines.append("-" * 70)
                for p in procs:
                    lines.append(f"  ⚙ {p[0]}")
            except Exception:
                pass

            cur.close()
            return "\n".join(lines)
        finally:
            conn.close()

    # =========================================================================
    # СПРАВОЧНИКИ ПЕРСОНАЛА
    # =========================================================================

    def get_companies(self, db_type, params):
        return self._fetch_lookup(
            db_type, params,
            'SELECT id, name FROM dbo.pcompany ORDER BY name',
            'SELECT ID, Name FROM PCompany ORDER BY Name',
        )

    def get_divisions(self, db_type, params):
        return self._fetch_lookup(
            db_type, params,
            'SELECT id, name FROM dbo.pdivision ORDER BY name',
            'SELECT ID, Name FROM PDivision ORDER BY Name',
        )

    def get_posts(self, db_type, params):
        return self._fetch_lookup(
            db_type, params,
            'SELECT id, name FROM dbo.ppost ORDER BY number, name',
            'SELECT ID, Name FROM PPost ORDER BY Number, Name',
        )

    def get_person_statuses(self, db_type, params):
        return self._fetch_lookup(
            db_type, params,
            'SELECT id, name FROM dbo.pliststatus ORDER BY id',
            'SELECT ID, Name FROM pListStatus ORDER BY ID',
        )

    def _fetch_lookup(self, db_type, params, pg_query, ms_query):
        try:
            conn = self._create_connection(db_type, params)
        except Exception:
            return []
        try:
            cur = conn.cursor()
            cur.execute(pg_query if db_type == DB_TYPE_POSTGRES else ms_query)
            result = cur.fetchall()
            cur.close()
            return result
        except Exception:
            return []
        finally:
            conn.close()

    # =========================================================================
    # СПРАВОЧНИКИ ИДЕНТИФИКАТОРОВ (для работы с pMark)
    # =========================================================================

    def get_password_types(self, db_type: str, params: dict) -> list:
        """
        Получить список ОБЫЧНЫХ типов идентификаторов из pTypePasswords.
        Возвращает: list[(id, name, comment)]
        """
        return self._fetch_lookup(
            db_type, params,
            'SELECT id, name, comment FROM dbo.ptypepasswords ORDER BY id',
            'SELECT ID, Name, Comment FROM pTypePasswords ORDER BY ID',
        )

    def get_bio_password_types(self, db_type: str, params: dict) -> list:
        """
        Получить список биометрических типов идентификаторов из pBioTypePasswords.
        Возвращает: list[(id, name, kind)]
            kind: 'finger' | 'face' | 'palm' | 'photo' | 'qr_bio' | 'bio'
                  (вычисляется по флагам isFinger/isFace/isPalm/isPhoto/isQR)
        """
        try:
            conn = self._create_connection(db_type, params)
        except Exception:
            return []
        try:
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute(
                    'SELECT id, name, '
                    '       COALESCE(isfinger, 0), COALESCE(isface, 0), '
                    '       COALESCE(ispalm, 0), COALESCE(isphoto, 0), '
                    '       COALESCE(isqr, 0) '
                    'FROM dbo.pbiotypepasswords ORDER BY id'
                )
            else:
                cur.execute(
                    'SELECT ID, Name, '
                    '       ISNULL(isFinger, 0), ISNULL(isFace, 0), '
                    '       ISNULL(isPalm, 0), ISNULL(isPhoto, 0), '
                    '       ISNULL(isQR, 0) '
                    'FROM pBioTypePasswords ORDER BY ID'
                )

            results = []
            for row in cur.fetchall():
                bid, name, is_finger, is_face, is_palm, is_photo, is_qr = row
                # Определяем «вид» биометрии по флагам (для генератора шаблонов)
                if is_finger:
                    kind = 'finger'
                elif is_face:
                    kind = 'face'
                elif is_palm:
                    kind = 'palm'
                elif is_photo:
                    kind = 'photo'
                elif is_qr:
                    kind = 'qr_bio'
                else:
                    kind = 'bio'
                results.append((bid, name or '(без имени)', kind))
            cur.close()
            return results
        except Exception:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def get_min_group_id(self, db_type: str, params: dict) -> int:
        """
        Получить минимальный существующий ID из dbo.Groups —
        нужен для FK-поля pMark.GroupID при batch-вставке.
        Если таблица пустая — возвращает 1 (попытаемся вставить как есть,
        вылетит FK-ошибка с понятным сообщением).
        """
        return self._fetch_min_id(
            db_type, params,
            pg_query='SELECT MIN(id) FROM dbo.groups',
            ms_query='SELECT MIN(ID) FROM dbo.Groups',
            default=1,
        )

    def get_min_config_id(self, db_type: str, params: dict) -> int:
        """
        Получить минимальный ID конфигурации.
        Источник зависит от схемы БД:
          - PostgreSQL: dbo.config (если таблица существует)
          - MSSQL: используем 1 как дефолт, т.к. отдельной таблицы нет
                   (поле pMark.Config обычно ссылается на конфигурацию,
                   которая создаётся при инициализации БД с ID=1).
        """
        if db_type == DB_TYPE_POSTGRES:
            # Пытаемся прочитать из dbo.config
            val = self._fetch_min_id(
                db_type, params,
                pg_query='SELECT MIN(id) FROM dbo.config',
                ms_query='',
                default=1,
            )
            return val if val else 1
        else:
            # В MSSQL отдельной таблицы Config нет — используем 1.
            # Если в БД нет такой записи — пользователь увидит FK-ошибку.
            return 1

    def _fetch_min_id(self, db_type, params, pg_query, ms_query, default=1):
        """Получить MIN(ID) из таблицы. Возвращает default если таблица пустая/нет."""
        try:
            conn = self._create_connection(db_type, params)
        except Exception:
            return default
        try:
            cur = conn.cursor()
            query = pg_query if db_type == DB_TYPE_POSTGRES else ms_query
            if not query:
                return default
            cur.execute(query)
            row = cur.fetchone()
            cur.close()
            if row and row[0] is not None:
                return int(row[0])
            return default
        except Exception:
            return default
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # =========================================================================
    # ПОИСК СОТРУДНИКОВ ПО ИДЕНТИФИКАТОРАМ
    # =========================================================================

    def find_persons_by_mark_type(self, db_type: str, params: dict,
                                   type_ids: list = None,
                                   name_filter: str = '') -> list:
        """
        Найти сотрудников, у которых есть идентификаторы определённых типов.

        Параметры:
            type_ids:    список ID типов (из pTypePasswords). Если пусто/None —
                         вернёт всех сотрудников, у которых есть ХОТЯ БЫ ОДИН
                         идентификатор любого типа.
            name_filter: фильтр по фамилии/имени (LIKE %name_filter%). Регистр
                         не учитывается. Пусто — без фильтра.

        Возвращает:
            list[dict] с полями: person_id, full_name, tab_number,
                                 mark_count, type_names (строка через запятую)
        """
        try:
            conn = self._create_connection(db_type, params)
        except Exception:
            return []

        try:
            cur = conn.cursor()

            if db_type == DB_TYPE_POSTGRES:
                # PG: используем JOIN + GROUP BY + STRING_AGG
                where_clauses = []
                query_params = []

                if type_ids:
                    placeholders = ','.join(['%s'] * len(type_ids))
                    where_clauses.append(f'm.gtype IN ({placeholders})')
                    query_params.extend(type_ids)

                if name_filter:
                    where_clauses.append(
                        '(LOWER(p.name) LIKE %s OR LOWER(p.firstname) LIKE %s '
                        'OR LOWER(p.midname) LIKE %s)'
                    )
                    pattern = f'%{name_filter.lower()}%'
                    query_params.extend([pattern, pattern, pattern])

                where_sql = ('WHERE ' + ' AND '.join(where_clauses)
                             if where_clauses else '')

                query = f"""
                    SELECT p.id,
                           TRIM(COALESCE(p.name, '') || ' ' ||
                                COALESCE(p.firstname, '') || ' ' ||
                                COALESCE(p.midname, '')) AS full_name,
                           COALESCE(p.tabnumber, '') AS tab_number,
                           COUNT(m.id) AS mark_count,
                           STRING_AGG(DISTINCT t.name, ', ') AS type_names
                    FROM dbo.plist p
                    INNER JOIN dbo.pmark m ON m.owner = p.id
                    LEFT JOIN dbo.ptypepasswords t ON t.id = m.gtype
                    {where_sql}
                    GROUP BY p.id, p.name, p.firstname, p.midname, p.tabnumber
                    ORDER BY p.name, p.firstname
                    LIMIT 5000
                """
                cur.execute(query, query_params)

            else:
                # MSSQL: STRING_AGG доступен с SQL Server 2017+.
                # Для совместимости со старыми версиями используем STUFF + FOR XML.
                where_clauses = []
                query_params = []

                if type_ids:
                    placeholders = ','.join(['?'] * len(type_ids))
                    where_clauses.append(f'm.Gtype IN ({placeholders})')
                    query_params.extend(type_ids)

                if name_filter:
                    where_clauses.append(
                        '(LOWER(p.Name) LIKE ? OR LOWER(p.FirstName) LIKE ? '
                        'OR LOWER(ISNULL(p.MidName, \'\')) LIKE ?)'
                    )
                    pattern = f'%{name_filter.lower()}%'
                    query_params.extend([pattern, pattern, pattern])

                where_sql = ('WHERE ' + ' AND '.join(where_clauses)
                             if where_clauses else '')

                # TOP 5000 — защита от мегапростыни в UI
                query = f"""
                    SELECT TOP 5000
                           p.ID,
                           LTRIM(RTRIM(
                               ISNULL(p.Name, '') + ' ' +
                               ISNULL(p.FirstName, '') + ' ' +
                               ISNULL(p.MidName, '')
                           )) AS full_name,
                           ISNULL(p.TabNumber, '') AS tab_number,
                           COUNT(m.ID) AS mark_count,
                           STUFF((
                               SELECT DISTINCT ', ' + t2.Name
                               FROM pMark m2
                               LEFT JOIN pTypePasswords t2 ON t2.ID = m2.Gtype
                               WHERE m2.Owner = p.ID
                               FOR XML PATH('')
                           ), 1, 2, '') AS type_names
                    FROM pList p
                    INNER JOIN pMark m ON m.Owner = p.ID
                    LEFT JOIN pTypePasswords t ON t.ID = m.Gtype
                    {where_sql}
                    GROUP BY p.ID, p.Name, p.FirstName, p.MidName, p.TabNumber
                    ORDER BY p.Name, p.FirstName
                """
                cur.execute(query, query_params)

            results = []
            for row in cur.fetchall():
                results.append({
                    'person_id': row[0],
                    'full_name': row[1] or '',
                    'tab_number': row[2] or '',
                    'mark_count': row[3] or 0,
                    'type_names': row[4] or '',
                })
            cur.close()
            return results

        except Exception as e:
            # Возвращаем псевдо-запись с ошибкой, чтобы UI её показал
            return [{'error': str(e)}]
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # =========================================================================
    # СПРАВОЧНИК УРОВНЕЙ ДОСТУПА (Groups)
    # =========================================================================

    def get_groups(self, db_type: str, params: dict) -> list:
        """
        Получить список уровней доступа из dbo.Groups.
        Возвращает: list[(id, name, comment)]
        Используется для выбора GroupID при создании идентификатора (pMark).

        Названия уровней в Орионе обычно:
          - "Запрет" (минимальный)
          - "Максимум" (полный доступ)
          - и пользовательские группы между ними
        """
        return self._fetch_lookup(
            db_type, params,
            ('SELECT id, COALESCE(name, \'(без имени)\'), COALESCE(comment, \'\') '
             'FROM dbo.groups ORDER BY id'),
            ('SELECT ID, ISNULL(Name, \'(без имени)\'), ISNULL(Comment, \'\') '
             'FROM dbo.Groups ORDER BY ID'),
        )

    # =========================================================================
    # МАССОВОЕ ДОБАВЛЕНИЕ ИДЕНТИФИКАТОРОВ (pMark)
    # =========================================================================

    def batch_add_marks(self, db_type: str, params: dict,
                         person_ids: list, type_id: int,
                         code_generator,
                         group_id: int = None, config_id: int = None,
                         start_date=None, finish_date=None,
                         type_kind: str = 'normal',
                         progress_callback=None,
                         should_stop=None) -> dict:
        """
        Массово создать идентификаторы (pMark) для списка сотрудников.

        Параметры:
            person_ids:     список ID сотрудников (pList.ID)
            type_id:        ID типа идентификатора. Для биометрии (type_kind != 'normal')
                            — ID из pBioTypePasswords; для обычных — из pTypePasswords.
            code_generator: callable(person_id, type_id) → bytes/str — генератор
                            кода идентификатора (для НЕбиометрических типов).
                            Для биометрии шаблон генерируется внутри коннектора.
            group_id:       FK на Groups.ID
            config_id:      FK на конфигурацию
            start_date:     datetime или None — Start (срок действия с)
            finish_date:    datetime или None — Finish (срок действия по)
            type_kind:      вид типа:
                              'normal'  — обычный (Proximity, QR, ...)
                              'finger'  — отпечаток пальца (шаблон в pMark.fingertemplate)
                              'face'    — лицо (шаблон в pBioAccess)
                              'palm'    — ладонь (шаблон в pBioAccess)
                              'photo'   — фото (шаблон в pBioAccess)
                              'qr_bio'  — био-QR (шаблон в pBioAccess)
                              'bio'     — прочая биометрия (шаблон в pBioAccess)

        Возвращает: dict {success, errors, last_error, stopped}
        """
        # Совместимость: вычисляем is_bio из type_kind
        is_bio = type_kind != 'normal'
        bio_kind = type_kind if is_bio else ''

        success = 0
        errors = 0
        last_error = ""
        stopped = False

        # Получаем дефолтные значения для NOT NULL FK-полей
        if group_id is None:
            group_id = self.get_min_group_id(db_type, params)
        if config_id is None:
            config_id = self.get_min_config_id(db_type, params)

        conn = self._create_connection(db_type, params)
        try:
            # Стартовый ID для pMark — один раз, дальше в памяти
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM dbo.pmark')
            else:
                cur.execute("SELECT ISNULL(MAX(ID), 0) + 1 FROM pMark")
            next_id = cur.fetchone()[0]
            cur.close()

            BATCH_SIZE = 1000
            current_batch = []
            # Параллельно собираем записи pBioAccess для bio_kind != 'finger'.
            # Они вставятся отдельным батчем после flush() основного pMark-batch'а
            # (чтобы FK ID_pMark был валиден — pMark уже закоммичен).
            bio_pending = []  # list[(pmark_id, bio_template_str)]

            def flush():
                nonlocal success, errors, last_error
                if not current_batch:
                    return
                try:
                    if db_type == DB_TYPE_POSTGRES:
                        self._batch_insert_marks_postgres(conn, current_batch)
                    else:
                        self._batch_insert_marks_mssql(conn, current_batch)
                    conn.commit()
                    success += len(current_batch)
                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    last_error = str(e)
                    # Fallback: построчно
                    for mark in current_batch:
                        try:
                            if db_type == DB_TYPE_POSTGRES:
                                self._batch_insert_marks_postgres(conn, [mark])
                            else:
                                self._batch_insert_marks_mssql(conn, [mark])
                            conn.commit()
                            success += 1
                        except Exception as e2:
                            errors += 1
                            last_error = str(e2)
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                current_batch.clear()

                # После успешного flush'а pMark — записываем pBioAccess
                # для тех биометрических типов, что требуют отдельную таблицу.
                if bio_pending:
                    try:
                        self._batch_insert_bio_access(
                            conn, db_type, bio_pending, type_id
                        )
                        conn.commit()
                    except Exception as e:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        last_error = (
                            f"pMark добавлен, но pBioAccess не записан: {e}"
                        )
                    bio_pending.clear()

            for person_id in person_ids:
                if should_stop is not None and should_stop():
                    stopped = True
                    break

                # Генерируем код идентификатора
                code = code_generator(person_id, type_id)

                # Для PG codep имеет тип bytea — нужно bytes
                # Для MSSQL — varchar, нужна строка
                if db_type == DB_TYPE_POSTGRES and isinstance(code, str):
                    code = code.encode('utf-8')
                elif db_type == DB_TYPE_MSSQL and isinstance(code, bytes):
                    code = code.hex().upper()

                # Биометрический шаблон — генерируем если нужно
                fingertemplate = None
                if is_bio and bio_kind == 'finger':
                    # Отпечаток пальца — длинный hex-шаблон в самом pMark
                    fingertemplate = self._generate_bio_template_hex(person_id, 256)

                mark_record = {
                    'id': next_id,
                    'gtype': type_id,
                    'config': config_id,
                    'codep': code,
                    'status': 1,
                    'owner': person_id,
                    'groupid': group_id,
                    'start': start_date,
                    'finish': finish_date,
                    'fingertemplate': fingertemplate,
                }
                current_batch.append(mark_record)

                # Для не-finger биометрии — копим pBioAccess отдельно
                if is_bio and bio_kind in ('face', 'palm', 'photo', 'qr_bio', 'bio'):
                    template = self._generate_bio_template_hex(
                        person_id, 512 if bio_kind in ('face', 'palm') else 256
                    )
                    bio_pending.append((next_id, template))

                next_id += 1

                if len(current_batch) >= BATCH_SIZE:
                    flush()
                    if progress_callback:
                        progress_callback(success, errors, last_error)

            flush()
            if progress_callback:
                progress_callback(success, errors, last_error)

        finally:
            try:
                conn.close()
            except Exception:
                pass

        return {
            'success': success,
            'errors': errors,
            'last_error': last_error,
            'stopped': stopped,
        }

    def _batch_insert_marks_postgres(self, conn, batch):
        """Быстрая batch-вставка идентификаторов в PG."""
        if not batch:
            return
        # ВАЖНО: в PG поля codep и codepadd имеют тип bytea.
        # psycopg2 автоматически конвертирует Python bytes в bytea.
        rows = [
            (m['id'], m['gtype'], m['config'],
             psycopg2.Binary(m['codep']) if isinstance(m['codep'], bytes) else m['codep'],
             m['status'], m['owner'], m['groupid'],
             m.get('start'), m.get('finish'))
            for m in batch
        ]
        cur = conn.cursor()
        try:
            query = (
                'INSERT INTO dbo.pmark '
                '(id, gtype, config, codep, status, owner, groupid, start, finish) '
                'VALUES %s'
            )
            execute_values(cur, query, rows, page_size=len(rows))
        finally:
            cur.close()

    def _batch_insert_marks_mssql(self, conn, batch):
        """Быстрая batch-вставка идентификаторов в MSSQL."""
        if not batch:
            return
        rows = [
            (m['id'], m['gtype'], m['config'], m['codep'],
             m['status'], m['owner'], m['groupid'],
             m.get('start'), m.get('finish'))
            for m in batch
        ]
        cur = conn.cursor()
        try:
            cur.fast_executemany = True
            query = (
                "INSERT INTO pMark "
                "(ID, Gtype, Config, CodeP, Status, Owner, GroupID, Start, Finish) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            cur.executemany(query, rows)
        finally:
            cur.close()

    # =========================================================================
    # МАССОВОЕ УДАЛЕНИЕ ИДЕНТИФИКАТОРОВ
    # =========================================================================

    def delete_marks_for_persons(self, db_type: str, params: dict,
                                  person_ids: list,
                                  type_ids: list = None) -> dict:
        """
        Массово удалить идентификаторы (записи в pMark) для списка сотрудников.

        Параметры:
            person_ids: список ID сотрудников (pList.ID)
            type_ids:   опциональный фильтр по типам (pTypePasswords.ID).
                        Если None — удаляются ВСЕ идентификаторы этих сотрудников.
                        Если задан — удаляются только идентификаторы указанных типов.

        Возвращает: dict {deleted, error}
            deleted: сколько строк удалено
            error:   текст ошибки (пустая строка если успешно)
        """
        if not person_ids:
            return {'deleted': 0, 'error': 'Пустой список сотрудников'}

        conn = self._create_connection(db_type, params)
        try:
            cur = conn.cursor()

            # Удаление пакетами по 1000, чтобы не упереться в лимит
            # параметров в IN-clause (особенно у MSSQL — лимит 2100)
            BATCH = 1000
            total_deleted = 0

            for i in range(0, len(person_ids), BATCH):
                chunk = person_ids[i:i + BATCH]

                if db_type == DB_TYPE_POSTGRES:
                    placeholders = ','.join(['%s'] * len(chunk))
                    query_params = list(chunk)

                    if type_ids:
                        type_ph = ','.join(['%s'] * len(type_ids))
                        query = (f'DELETE FROM dbo.pmark '
                                 f'WHERE owner IN ({placeholders}) '
                                 f'AND gtype IN ({type_ph})')
                        query_params.extend(type_ids)
                    else:
                        query = (f'DELETE FROM dbo.pmark '
                                 f'WHERE owner IN ({placeholders})')

                    cur.execute(query, query_params)
                    total_deleted += cur.rowcount if cur.rowcount > 0 else 0
                else:
                    placeholders = ','.join(['?'] * len(chunk))
                    query_params = list(chunk)

                    if type_ids:
                        type_ph = ','.join(['?'] * len(type_ids))
                        query = (f'DELETE FROM pMark '
                                 f'WHERE Owner IN ({placeholders}) '
                                 f'AND Gtype IN ({type_ph})')
                        query_params.extend(type_ids)
                    else:
                        query = (f'DELETE FROM pMark '
                                 f'WHERE Owner IN ({placeholders})')

                    cur.execute(query, query_params)
                    total_deleted += cur.rowcount if cur.rowcount > 0 else 0

            conn.commit()
            cur.close()
            return {'deleted': total_deleted, 'error': ''}

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return {'deleted': 0, 'error': str(e)}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # =========================================================================
    # ОДИНОЧНОЕ ДОБАВЛЕНИЕ (для совместимости)
    # =========================================================================

    def add_person(self, db_type: str, params: dict, person_data: dict) -> int:
        """
        Добавить ОДНОГО сотрудника. Для массового добавления — batch_add_persons.
        """
        conn = self._create_connection(db_type, params)
        try:
            if db_type == DB_TYPE_POSTGRES:
                new_id = self._insert_person_postgres_single(conn, person_data)
            else:
                new_id = self._insert_person_mssql_single(conn, person_data)
            conn.commit()
            return new_id
        except Exception as e:
            conn.rollback()
            raise Exception(f"Не удалось добавить сотрудника: {str(e)}")
        finally:
            conn.close()

    def _insert_person_postgres_single(self, conn, person_data):
        cur = conn.cursor()
        try:
            cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM dbo.plist')
            next_id = cur.fetchone()[0]

            columns = ['id', 'name', 'firstname', 'status']
            values = [next_id, person_data['name'].strip(),
                      person_data['first_name'].strip(),
                      person_data.get('status', 1)]
            placeholders = ['%s', '%s', '%s', '%s']

            for field_key, pg_col, _ms, is_str in _OPTIONAL_PERSON_FIELDS:
                val = person_data.get(field_key)
                if val is not None:
                    columns.append(pg_col)
                    values.append(val.strip() if (is_str and isinstance(val, str)) else val)
                    placeholders.append('%s')

            query = (f'INSERT INTO dbo.plist ({", ".join(columns)}) '
                     f'VALUES ({", ".join(placeholders)}) RETURNING id')
            cur.execute(query, values)
            return cur.fetchone()[0]
        finally:
            cur.close()

    def _insert_person_mssql_single(self, conn, person_data):
        cur = conn.cursor()
        try:
            cur.execute("SELECT ISNULL(MAX(ID), 0) + 1 FROM pList")
            next_id = cur.fetchone()[0]

            columns = ['ID', 'Name', 'FirstName', 'Status']
            values = [next_id, person_data['name'].strip(),
                      person_data['first_name'].strip(),
                      person_data.get('status', 1)]
            placeholders = ['?', '?', '?', '?']

            for field_key, _pg, ms_col, is_str in _OPTIONAL_PERSON_FIELDS:
                val = person_data.get(field_key)
                if val is not None:
                    columns.append(ms_col)
                    values.append(val.strip() if (is_str and isinstance(val, str)) else val)
                    placeholders.append('?')

            query = (f"INSERT INTO pList ({', '.join(columns)}) "
                     f"VALUES ({', '.join(placeholders)})")
            cur.execute(query, values)
            return next_id
        finally:
            cur.close()

    # =========================================================================
    # МАССОВОЕ ДОБАВЛЕНИЕ — BATCH (быстрая вставка)
    # =========================================================================

    def batch_add_persons(self, db_type: str, params: dict,
                          person_data_iter, progress_callback=None,
                          should_stop=None) -> dict:
        """
        Быстрое массовое добавление сотрудников.

        Производительность:
            - MSSQL: cursor.fast_executemany = True + executemany()
            - PostgreSQL: execute_values()
            - Оба используют ОДНО соединение, накапливают batch и вставляют
              пакетами по _BATCH_SIZE_NO_PHOTO (или _BATCH_SIZE_WITH_PHOTO).

        Параметры:
            person_data_iter: итератор словарей person_data
            progress_callback(success, errors, last_error): вызывается из
                фонового потока ПОСЛЕ каждого успешного flush'а batch'а.
                UI обновлять через root.after()
            should_stop: callable() -> bool, для отмены операции

        Возвращает:
            dict: {'success', 'errors', 'last_error', 'stopped'}
        """
        success = 0
        errors = 0
        last_error = ""
        stopped = False

        conn = self._create_connection(db_type, params)
        try:
            # Стартовый ID — один раз, дальше инкрементируем в памяти.
            cur = conn.cursor()
            if db_type == DB_TYPE_POSTGRES:
                cur.execute('SELECT COALESCE(MAX(id), 0) + 1 FROM dbo.plist')
            else:
                cur.execute("SELECT ISNULL(MAX(ID), 0) + 1 FROM pList")
            next_id = cur.fetchone()[0]
            cur.close()

            current_batch = []
            current_batch_has_photo = None

            def flush():
                """Сбросить накопленный batch в БД."""
                nonlocal success, errors, last_error
                if not current_batch:
                    return

                try:
                    if db_type == DB_TYPE_POSTGRES:
                        self._batch_insert_postgres(conn, current_batch)
                    else:
                        self._batch_insert_mssql(conn, current_batch)
                    conn.commit()
                    success += len(current_batch)
                except Exception as e:
                    # Batch упал целиком — пробуем построчно, чтобы понять
                    # сколько вставилось.
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    last_error = str(e)
                    for person in current_batch:
                        try:
                            if db_type == DB_TYPE_POSTGRES:
                                self._batch_insert_postgres(conn, [person])
                            else:
                                self._batch_insert_mssql(conn, [person])
                            conn.commit()
                            success += 1
                        except Exception as e2:
                            errors += 1
                            last_error = str(e2)
                            try:
                                conn.rollback()
                            except Exception:
                                pass

                current_batch.clear()

            # Главный цикл
            for person in person_data_iter:
                if should_stop is not None and should_stop():
                    stopped = True
                    break

                person['_explicit_id'] = next_id
                next_id += 1

                has_photo = person.get('picture') is not None

                # Если набор колонок изменился (фото появилось/исчезло),
                # сначала flush'аем накопленное.
                if (current_batch_has_photo is not None
                        and has_photo != current_batch_has_photo):
                    flush()
                    if progress_callback is not None:
                        progress_callback(success, errors, last_error)

                current_batch_has_photo = has_photo
                current_batch.append(person)

                limit = (_BATCH_SIZE_WITH_PHOTO if has_photo
                         else _BATCH_SIZE_NO_PHOTO)
                if len(current_batch) >= limit:
                    flush()
                    if progress_callback is not None:
                        progress_callback(success, errors, last_error)

            # Финальный flush
            flush()
            if progress_callback is not None:
                progress_callback(success, errors, last_error)

        finally:
            try:
                conn.close()
            except Exception:
                pass

        return {
            'success': success,
            'errors': errors,
            'last_error': last_error,
            'stopped': stopped,
        }

    def _build_columns_and_values(self, batch: list, is_postgres: bool):
        """
        Собрать единый набор колонок и значений из batch'а.
        Все записи в batch ДОЛЖНЫ иметь одинаковый набор опциональных полей —
        вызывающая сторона гарантирует это разделением batch'ей по группам.
        """
        if is_postgres:
            columns = ['id', 'name', 'firstname', 'status']
        else:
            columns = ['ID', 'Name', 'FirstName', 'Status']

        first = batch[0]
        present_fields = []
        for field_key, pg_col, ms_col, is_str in _OPTIONAL_PERSON_FIELDS:
            if first.get(field_key) is not None:
                col = pg_col if is_postgres else ms_col
                present_fields.append((field_key, col, is_str))
                columns.append(col)

        rows = []
        for person in batch:
            row = [
                person['_explicit_id'],
                person['name'].strip(),
                person['first_name'].strip(),
                person.get('status', 1),
            ]
            for field_key, _col, is_str in present_fields:
                val = person.get(field_key)
                if val is None:
                    row.append(None)
                elif is_str and isinstance(val, str):
                    row.append(val.strip())
                else:
                    row.append(val)
            rows.append(tuple(row))

        return columns, rows

    def _batch_insert_postgres(self, conn, batch):
        """Быстрая вставка batch'а в PG через execute_values."""
        if not batch:
            return
        columns, rows = self._build_columns_and_values(batch, is_postgres=True)
        cur = conn.cursor()
        try:
            # Имена колонок берутся из захардкоженного списка _OPTIONAL_PERSON_FIELDS.
            # SQL-инъекция здесь невозможна.
            query = f'INSERT INTO dbo.plist ({", ".join(columns)}) VALUES %s'
            execute_values(cur, query, rows, page_size=len(rows))
        finally:
            cur.close()

    def _batch_insert_mssql(self, conn, batch):
        """Быстрая вставка batch'а в MSSQL через fast_executemany."""
        if not batch:
            return
        columns, rows = self._build_columns_and_values(batch, is_postgres=False)
        placeholders = ", ".join(["?"] * len(columns))

        cur = conn.cursor()
        try:
            # КЛЮЧЕВОЙ ФЛАГ: даёт прирост в 50-100 раз для executemany.
            # Без него pyodbc делает каждый INSERT отдельным round-trip.
            cur.fast_executemany = True
            query = (f"INSERT INTO pList ({', '.join(columns)}) "
                     f"VALUES ({placeholders})")
            cur.executemany(query, rows)
        finally:
            cur.close()

    # =========================================================================
    # ПОДКЛЮЧЕНИЕ
    # =========================================================================

    def _create_connection(self, db_type: str, params: dict):
        if db_type == DB_TYPE_POSTGRES:
            return self._connect_postgres(params)
        elif db_type == DB_TYPE_MSSQL:
            return self._connect_mssql(params)
        else:
            raise ValueError(f"Неподдерживаемый тип СУБД: {db_type}")

    def _connect_postgres(self, params: dict):
        try:
            dbname = params.get('dbname', '').strip()
            if not dbname:
                dbname = 'postgres'
            return psycopg2.connect(
                host=params['host'],
                port=params['port'],
                dbname=dbname,
                user=params['user'],
                password=params['password'],
                options='-c client_encoding=UTF8',
            )
        except psycopg2.OperationalError as e:
            err_msg = str(e).lower()
            if 'password authentication failed' in err_msg:
                raise ConnectionError(
                    f"Неверный пароль для пользователя '{params['user']}'.\n"
                    f"Проверьте логин и пароль."
                ) from e
            elif 'could not connect to server' in err_msg or 'connection refused' in err_msg:
                raise ConnectionError(
                    f"Сервер {params['host']}:{params['port']} недоступен.\n"
                    f"Проверьте адрес сервера и порт."
                ) from e
            elif 'does not exist' in err_msg and 'database' in err_msg:
                raise ConnectionError(
                    f"База данных '{params.get('dbname', '')}' не найдена.\n"
                    f"Проверьте имя базы данных."
                ) from e
            else:
                raise

    def _connect_mssql(self, params: dict):
        server = params['server']
        database = params.get('database', '')
        auth_type = params.get('auth_type', AUTH_TYPE_WINDOWS)

        if auth_type == AUTH_TYPE_WINDOWS:
            conn_str = (f"DRIVER={{{MSSQL_ODBC_DRIVER}}};"
                        f"SERVER={server};DATABASE={database};"
                        f"Trusted_Connection=yes;")
        else:
            conn_str = (f"DRIVER={{{MSSQL_ODBC_DRIVER}}};"
                        f"SERVER={server};DATABASE={database};"
                        f"UID={params['user']};PWD={params['password']}")
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.OperationalError as e:
            err_msg = str(e).lower()
            if 'login failed' in err_msg:
                raise ConnectionError(
                    "Неверный логин или пароль для SQL Server.\n"
                    "Проверьте учётные данные."
                ) from e
            elif 'cannot open database' in err_msg:
                raise ConnectionError(
                    f"База данных '{database}' не найдена на сервере.\n"
                    f"Проверьте имя базы данных."
                ) from e
            elif 'server does not exist' in err_msg or 'connection was refused' in err_msg:
                raise ConnectionError(
                    f"Сервер '{server}' недоступен.\nПроверьте адрес сервера."
                ) from e
            else:
                raise
        except pyodbc.InterfaceError as e:
            err_msg = str(e).lower()
            if 'driver' in err_msg:
                raise ConnectionError(
                    f"ODBC-драйвер '{MSSQL_ODBC_DRIVER}' не найден.\n"
                    f"Установите Microsoft ODBC Driver for SQL Server."
                ) from e
            else:
                raise

    # =========================================================================
    # PROFILER — Extended Events (XEvents) для MSSQL + pg_stat_activity для PG
    # =========================================================================
    #
    # MSSQL (XEvents): создаём event session, ловим sql_statement_completed
    #   и rpc_completed → видим КАЖДЫЙ завершённый запрос с точностью до ms.
    #   Это и есть «настоящий Profiler» (как в SSMS).
    #   Требует прав ALTER ANY EVENT SESSION.
    #
    # PostgreSQL: pg_stat_activity (как раньше) — query содержит реальный SQL
    #   активных запросов. Завершённые запросы PostgreSQL не показывает
    #   через активити-вьюху (для этого нужен pg_stat_statements / log_statement,
    #   что требует ребута сервера).
    #
    # Дедупликация в profiler_view.py через множество unique_key
    # (для XEvents используем event_sequence — гарантированно уникален).
    # =========================================================================

    # Имя event session — уникальное на процесс, чтобы несколько копий программы
    # не мешали друг другу. Имя начинается с фиксированного префикса, чтобы
    # при старте можно было найти и удалить «осиротевшие» сессии от прошлых
    # запусков (если программа упала без грации).
    _XE_SESSION_PREFIX = 'profiler_app_'

    def profiler_start_session(self, db_type: str, params: dict,
                                target_db_name: str = None) -> dict:
        """
        Создать и запустить event session для Profiler.
        Вызывается один раз при нажатии «Старт».

        Параметры:
            target_db_name: имя БД, которую мониторим (для фильтра событий).
                            Если None — мониторим все БД на сервере.

        Возвращает: dict
            'session_name': str  — имя сессии (передаётся в profiler_poll)
            'mode':         str  — 'xevents' или 'dmv' (fallback)
            'error':        str  — пустая строка если ОК
        """
        if db_type != DB_TYPE_MSSQL:
            # Для PostgreSQL — выбираем лучший доступный режим:
            #
            #   1. pg_stat_monitor (от Percona) — ЛУЧШИЙ режим. Видит ВСЕ
            #      запросы И умеет показывать РЕАЛЬНЫЕ значения параметров
            #      (через SET pg_stat_monitor.pgsm_normalized_query = false).
            #      Это то, ради чего стоит ставить расширение.
            #
            #   2. pg_stat_statements — стандартное расширение. Видит все
            #      запросы, но ТОЛЬКО с плейсхолдерами $1, $2 (значения
            #      физически не хранятся — ограничение PG).
            #
            #   3. pg_stat_activity — fallback. Видны только активные сейчас
            #      запросы. Быстрые INSERT/UPDATE проскакивают.
            has_pgsm = False
            has_pgss = False
            try:
                pg_conn = self._create_connection(db_type, params)
                pg_cur = pg_conn.cursor()
                pg_cur.execute(
                    "SELECT extname FROM pg_extension "
                    "WHERE extname IN ('pg_stat_monitor', 'pg_stat_statements')"
                )
                installed = {row[0] for row in pg_cur.fetchall()}
                has_pgsm = 'pg_stat_monitor' in installed
                has_pgss = 'pg_stat_statements' in installed
                pg_cur.close()
                pg_conn.close()
            except Exception:
                pass

            if has_pgsm:
                # Лучший режим: pg_stat_monitor с реальными значениями параметров
                if hasattr(self, '_pgsm_seen'):
                    self._pgsm_seen = {}
                return {'session_name': '', 'mode': 'pgsm', 'error': ''}
            elif has_pgss:
                # Средний режим: pg_stat_statements (только плейсхолдеры $1, $2)
                if hasattr(self, '_pgss_seen'):
                    self._pgss_seen = {}
                return {
                    'session_name': '', 'mode': 'pgss',
                    'error': (
                        'Используется pg_stat_statements — видны ВСЕ запросы, '
                        'но параметры показываются как $1, $2, $3 (без значений).\n\n'
                        'Для отображения РЕАЛЬНЫХ значений параметров можно '
                        'установить расширение pg_stat_monitor (от Percona):\n'
                        '  https://docs.percona.com/pg-stat-monitor/'
                    ),
                }
            else:
                return {
                    'session_name': '', 'mode': 'pg_activity',
                    'error': (
                        'Ни pg_stat_monitor, ни pg_stat_statements не установлены. '
                        'Профайлер будет показывать только активные запросы '
                        '(быстрые INSERT/UPDATE могут проскакивать).\n\n'
                        'Для полноценного профайлинга установите одно из расширений:\n'
                        '  • pg_stat_monitor (от Percona) — с реальными значениями параметров\n'
                        '  • pg_stat_statements — стандартное, но только с $1, $2'
                    ),
                }

        # Уникальное имя сессии: префикс + PID процесса + timestamp
        import os as _os
        import time as _time
        session_name = f"{self._XE_SESSION_PREFIX}{_os.getpid()}_{int(_time.time())}"

        try:
            conn = self._create_connection(db_type, params)
        except Exception as e:
            return {'session_name': '', 'mode': 'none',
                    'error': f'Нет соединения: {e}'}

        # КРИТИЧНО: SQL Server запрещает CREATE/ALTER/DROP EVENT SESSION
        # внутри транзакции (ошибка 574: "Инструкцию CREATE EVENT SESSION
        # нельзя использовать в пользовательской транзакции").
        # pyodbc по умолчанию работает с autocommit=False, и любой запрос
        # неявно открывает транзакцию. Включаем autocommit=True для этого
        # соединения, чтобы DDL-команды XEvents выполнялись вне транзакции.
        try:
            conn.autocommit = True
        except Exception:
            pass

        try:
            cur = conn.cursor()

            # 1. Чистим осиротевшие сессии от прошлых запусков (если есть)
            self._cleanup_orphan_sessions(cur)

            # 2. Создаём новую сессию
            #
            # Ловим два события:
            #   sql_statement_completed — обычные ad-hoc запросы (SELECT/INSERT/...)
            #   rpc_completed           — параметризованные вызовы (sp_executesql,
            #                             stored procedures)
            #
            # Action collect_*  — что прицепить к каждому событию (доп. поля)
            #
            # Target = ring_buffer — кольцевой буфер в памяти. Лимит 4 МБ
            # по умолчанию, при заполнении старые события вытесняются.
            # Это нормальный режим работы профайлера.

            # ВАЖНО: НЕ ставим фильтр database_name прямо в сессии —
            # имя БД, которое SQL Server видит для конкретного INSERT, может
            # отличаться от имени из строки подключения (например, при USE
            # database, при использовании fully-qualified имён, или когда
            # драйвер по умолчанию открывает соединение к master).
            # Фильтрацию по БД делаем на стороне Python после парсинга XML.
            #
            # Аналогично — НЕ фильтруем по session_id. Программа использует
            # несколько соединений, и блокировать «свой SPID» не имеет смысла:
            # SPID создателя сессии и SPID следующих запросов — разные.
            #
            # Единственный фильтр — is_system = 0, чтобы не ловить служебные
            # запросы SQL Agent, lazy writer и т.п. Но если и он мешает —
            # можно убрать.
            create_sql = f"""
            CREATE EVENT SESSION [{session_name}] ON SERVER
            ADD EVENT sqlserver.sql_statement_completed(
                ACTION (
                    sqlserver.session_id,
                    sqlserver.client_hostname,
                    sqlserver.client_app_name,
                    sqlserver.username,
                    sqlserver.database_name,
                    sqlserver.sql_text
                )
                WHERE (sqlserver.is_system = 0)
            ),
            ADD EVENT sqlserver.rpc_completed(
                ACTION (
                    sqlserver.session_id,
                    sqlserver.client_hostname,
                    sqlserver.client_app_name,
                    sqlserver.username,
                    sqlserver.database_name,
                    sqlserver.sql_text
                )
                WHERE (sqlserver.is_system = 0)
            )
            ADD TARGET package0.ring_buffer
                (SET max_memory = 4096, max_events_limit = 10000)
            WITH (
                MAX_DISPATCH_LATENCY = 1 SECONDS,
                STARTUP_STATE = OFF
            );
            """
            cur.execute(create_sql)

            # 3. Запускаем сессию
            cur.execute(
                f"ALTER EVENT SESSION [{session_name}] ON SERVER STATE = START;"
            )

            # Запоминаем целевую БД для фильтрации событий на стороне Python.
            # Это надёжнее, чем фильтр database_name в самой сессии: имя БД
            # из соединения и имя БД в событии могут отличаться.
            if target_db_name:
                self._xe_target_db[session_name] = target_db_name

            # commit/rollback не нужны — соединение в autocommit-режиме
            cur.close()
            return {'session_name': session_name, 'mode': 'xevents', 'error': ''}

        except pyodbc.Error as e:
            err = str(e).lower()
            # Нет прав — fallback на DMV-режим
            if ('permission' in err or 'denied' in err
                    or 'alter any event session' in err):
                return {
                    'session_name': '', 'mode': 'dmv',
                    'error': ('Нет прав ALTER ANY EVENT SESSION для XEvents — '
                              'переключаюсь на DMV-режим (видны только активные запросы).'),
                }
            return {'session_name': '', 'mode': 'none', 'error': str(e)}
        except Exception as e:
            return {'session_name': '', 'mode': 'none', 'error': str(e)}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def profiler_stop_session(self, db_type: str, params: dict,
                               session_name: str) -> None:
        """
        Остановить и удалить event session.
        ВАЖНО: вызвать обязательно при остановке профайлера и при закрытии окна,
        иначе сессия останется в БД навсегда.
        """
        # Сразу удаляем целевую БД из словаря, чтобы её не использовали
        # для фильтрации в случае повторного запуска с тем же именем
        self._xe_target_db.pop(session_name, None)

        if db_type != DB_TYPE_MSSQL or not session_name:
            return
        try:
            conn = self._create_connection(db_type, params)
        except Exception:
            return
        # DROP EVENT SESSION тоже нельзя в транзакции — нужен autocommit
        try:
            conn.autocommit = True
        except Exception:
            pass
        try:
            cur = conn.cursor()
            self._drop_session_safe(cur, session_name)
            cur.close()
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _cleanup_orphan_sessions(self, cur):
        """
        Удалить все event session с нашим префиксом, оставшиеся от прошлых
        запусков (если программа упала без грации).
        """
        try:
            cur.execute(
                "SELECT name FROM sys.server_event_sessions WHERE name LIKE ?",
                (f"{self._XE_SESSION_PREFIX}%",)
            )
            orphans = [row[0] for row in cur.fetchall()]
            for name in orphans:
                self._drop_session_safe(cur, name)
        except Exception:
            pass

    def _drop_session_safe(self, cur, session_name: str):
        """Безопасно удалить event session — игнорируем все ошибки."""
        try:
            cur.execute(
                f"IF EXISTS (SELECT 1 FROM sys.server_event_sessions "
                f"WHERE name = N'{session_name}') "
                f"DROP EVENT SESSION [{session_name}] ON SERVER;"
            )
        except Exception:
            pass

    def profiler_poll(self, db_type, params, last_marker, session_name=''):
        """
        Получить новые события из Profiler.

        Параметры:
            last_marker:  для XEvents — последний event_sequence (str/int);
                          для PG/DMV — не используется
            session_name: имя XEvents-сессии (только для MSSQL XEvents)

        Возвращает dict:
            'events':     list[dict]
            'new_marker': str
        """
        try:
            conn = self._create_connection(db_type, params)
        except Exception as e:
            return {'events': [{'error': f'Нет соединения: {e}'}],
                    'new_marker': last_marker}

        try:
            cur = conn.cursor()
            if db_type == DB_TYPE_MSSQL:
                if session_name:
                    return self._profiler_poll_mssql_xevents(
                        cur, last_marker, session_name
                    )
                else:
                    # Fallback: если сессия не создалась — DMV-режим
                    return self._profiler_poll_mssql_dmv(cur, last_marker)
            else:
                return self._profiler_poll_postgres(cur, last_marker)
        except Exception as e:
            return {'events': [{'error': str(e)}], 'new_marker': last_marker}
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _profiler_poll_mssql_xevents(self, cur, last_marker, session_name):
        """
        Прочитать буфер XEvents-сессии и распарсить XML.

        ВАЖНО про дедупликацию: ring_buffer возвращает накопленные события
        целиком при каждом запросе. Чтобы не показывать одни и те же события
        повторно, дедупликация делается на стороне ProfilerView через
        set уже виденных unique_key (а не через сравнение маркеров —
        у XEvents нет монотонно растущего event_sequence в ring_buffer-формате).

        last_marker и new_marker оставлены для совместимости интерфейса,
        но в XEvents-режиме фактически не используются.
        """
        import xml.etree.ElementTree as ET

        try:
            cur.execute("""
                SELECT CAST(t.target_data AS xml) AS xml_data
                FROM sys.dm_xe_sessions s
                JOIN sys.dm_xe_session_targets t
                    ON s.address = t.event_session_address
                WHERE s.name = ?
                  AND t.target_name = 'ring_buffer'
            """, (session_name,))
        except pyodbc.Error as e:
            err = str(e).lower()
            if 'permission' in err or 'view server state' in err:
                return {
                    'events': [{
                        'error': 'Нет прав VIEW SERVER STATE для чтения XEvents.'
                    }],
                    'new_marker': last_marker,
                }
            return {'events': [{'error': str(e)}], 'new_marker': last_marker}

        row = cur.fetchone()
        cur.close()

        # Диагностика: если сессия не нашлась — вернём ошибку
        if row is None:
            return {
                'events': [{
                    'error': (f'Сессия "{session_name}" не найдена в '
                              f'sys.dm_xe_sessions. Возможно, сессия была '
                              f'остановлена внешним инструментом.')
                }],
                'new_marker': last_marker,
            }

        if not row[0]:
            # Сессия есть, но в ring_buffer пусто — это нормально, события
            # просто ещё не пришли. Возвращаем пустой список.
            return {'events': [], 'new_marker': last_marker}

        # Преобразуем XML в строку
        xml_str = row[0]
        if isinstance(xml_str, (bytes, bytearray)):
            # Пробуем разные кодировки. UTF-16 — самая частая для MSSQL XML
            decoded = None
            for enc in ('utf-16-le', 'utf-16-be', 'utf-8', 'cp1251'):
                try:
                    decoded = xml_str.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            xml_str = decoded if decoded else xml_str.decode('utf-8', errors='replace')
        elif not isinstance(xml_str, str):
            xml_str = str(xml_str)

        # Диагностика для отладки (раскомментировать при проблемах):
        # print(f"[XEvents] xml_str length: {len(xml_str)}, "
        #       f"first 200 chars: {xml_str[:200]!r}")

        try:
            root = ET.fromstring(xml_str)
        except ET.ParseError as e:
            return {'events': [{'error': f'Ошибка парсинга XML: {e}'}],
                    'new_marker': last_marker}

        events = []
        # Целевая БД для фильтрации (если задана при start_session)
        target_db = self._xe_target_db.get(session_name, '')
        target_db_lower = target_db.lower() if target_db else ''

        for ev_elem in root.iter('event'):
            parsed = self._parse_xevent(ev_elem)
            if not parsed:
                continue

            # Фильтр 1: служебные запросы самого Profiler'а.
            # Profiler каждую секунду делает SELECT из sys.dm_xe_sessions
            # для чтения ring_buffer — этот запрос тоже попадает в события
            # и засоряет лог. Отсекаем по характерным сигнатурам.
            sql_upper = parsed.get('sql_text', '').upper()
            if (('SYS.DM_XE_SESSIONS' in sql_upper)
                    or ('SYS.DM_XE_SESSION_TARGETS' in sql_upper)
                    or ('SYS.SERVER_EVENT_SESSIONS' in sql_upper)
                    or ('SYS.FN_DBLOG' in sql_upper)):
                continue

            # Фильтр 2: имя БД — на Python-стороне, а не в самой сессии.
            # Это надёжнее: иногда event приходит с пустым database_name
            # (например, BATCH-вызовы до USE), и фильтр в сессии его бы отрезал.
            # Здесь же мы можем разрешать пустые database_name + точное совпадение.
            if target_db_lower:
                ev_db = (parsed.get('database', '') or '').lower()
                if ev_db and ev_db != target_db_lower:
                    continue  # событие из другой БД — пропускаем

            events.append(parsed)

        return {'events': events, 'new_marker': last_marker}

    def profiler_diagnose(self, db_type, params, session_name):
        """
        Диагностика состояния Profiler-сессии.
        Возвращает dict со структурой:
            'session_exists': bool — есть ли наша сессия в server_event_sessions
            'session_active': bool — запущена ли (есть в dm_xe_sessions)
            'event_count':    int  — сколько событий в ring_buffer
            'xml_length':     int  — размер XML
            'xml_preview':    str  — первые 500 символов XML (для отладки)
            'errors':         list — список найденных проблем
            'sample_events':  list — первые 3 распарсенных события (если есть)
        """
        if db_type != DB_TYPE_MSSQL:
            return {'errors': ['Диагностика поддерживается только для MSSQL']}

        result = {
            'session_exists': False,
            'session_active': False,
            'event_count': 0,
            'xml_length': 0,
            'xml_preview': '',
            'errors': [],
            'sample_events': [],
        }

        if not session_name:
            result['errors'].append('Имя сессии пустое — Profiler не запущен.')
            return result

        try:
            conn = self._create_connection(db_type, params)
        except Exception as e:
            result['errors'].append(f'Не удалось подключиться: {e}')
            return result

        try:
            conn.autocommit = True
        except Exception:
            pass

        try:
            cur = conn.cursor()

            # 1. Сессия существует?
            cur.execute(
                "SELECT COUNT(*) FROM sys.server_event_sessions WHERE name = ?",
                (session_name,)
            )
            result['session_exists'] = (cur.fetchone()[0] > 0)
            if not result['session_exists']:
                result['errors'].append(
                    f'Сессия {session_name} не существует в '
                    f'sys.server_event_sessions. CREATE EVENT SESSION '
                    f'не сработал или сессия была удалена.'
                )

            # 2. Сессия активна (запущена)?
            cur.execute(
                "SELECT COUNT(*) FROM sys.dm_xe_sessions WHERE name = ?",
                (session_name,)
            )
            result['session_active'] = (cur.fetchone()[0] > 0)
            if result['session_exists'] and not result['session_active']:
                result['errors'].append(
                    f'Сессия {session_name} существует, но НЕ запущена. '
                    f'ALTER EVENT SESSION ... STATE = START не сработал.'
                )

            if not result['session_active']:
                cur.close()
                return result

            # 3. Читаем target_data
            cur.execute("""
                SELECT CAST(t.target_data AS xml) AS xml_data
                FROM sys.dm_xe_sessions s
                JOIN sys.dm_xe_session_targets t
                    ON s.address = t.event_session_address
                WHERE s.name = ?
                  AND t.target_name = 'ring_buffer'
            """, (session_name,))
            row = cur.fetchone()

            if not row or not row[0]:
                result['errors'].append(
                    'ring_buffer пуст. Возможные причины:\n'
                    '  • События ещё не успели накопиться (норма первую секунду)\n'
                    '  • Фильтр в сессии режет все события (например, '
                    'фильтр database_name не совпадает с реальным именем БД)\n'
                    '  • Все запросы выполняются на той же сессии, что и Profiler '
                    '(они отфильтрованы по @@SPID)'
                )
                cur.close()
                return result

            # 4. Анализируем XML
            xml_str = row[0]
            if isinstance(xml_str, (bytes, bytearray)):
                decoded = None
                for enc in ('utf-16-le', 'utf-16-be', 'utf-8', 'cp1251'):
                    try:
                        decoded = xml_str.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                xml_str = decoded if decoded else str(xml_str)
            elif not isinstance(xml_str, str):
                xml_str = str(xml_str)

            result['xml_length'] = len(xml_str)
            result['xml_preview'] = xml_str[:500]

            # 5. Парсим XML и считаем события
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_str)
                event_elements = list(root.iter('event'))
                result['event_count'] = len(event_elements)

                # Парсим первые 3 для примера
                for ev in event_elements[:3]:
                    parsed = self._parse_xevent(ev)
                    if parsed:
                        result['sample_events'].append({
                            'time': parsed.get('time', ''),
                            'op': parsed.get('operation', ''),
                            'sql': parsed.get('sql_text', '')[:200],
                            'database': parsed.get('database', ''),
                            'login': parsed.get('login', ''),
                        })
            except ET.ParseError as e:
                result['errors'].append(f'Ошибка парсинга XML: {e}')

            cur.close()
        except Exception as e:
            result['errors'].append(f'Ошибка диагностики: {e}')
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return result

    def _parse_xevent(self, ev_elem):
        """
        Распарсить один <event> из XML XEvents.
        Возвращает dict в едином формате (как DMV/PG поллер).
        """
        try:
            ev_name = ev_elem.get('name', '')
            timestamp = ev_elem.get('timestamp', '')

            # Собираем все <data> и <action> в плоский dict
            fields = {}
            for child in ev_elem:
                if child.tag in ('data', 'action'):
                    fname = child.get('name', '')
                    val_elem = child.find('value')
                    if val_elem is not None:
                        fields[fname] = val_elem.text or ''

            # Текст SQL: для sql_statement_completed это data[statement],
            # для rpc_completed — data[statement] тоже. Action[sql_text]
            # содержит весь батч (часто длиннее).
            sql_text = (
                fields.get('statement', '')
                or fields.get('sql_text', '')
                or fields.get('batch_text', '')
            ).strip()

            if not sql_text:
                return None

            duration_us = int(fields.get('duration', 0) or 0)
            duration_ms = duration_us // 1000  # XEvents даёт в микросекундах

            cpu_us = int(fields.get('cpu_time', 0) or 0)
            cpu_ms = cpu_us // 1000

            # Тип операции
            op = self._detect_operation(sql_text, ev_name.upper())

            obj = self._extract_table_name(sql_text, op)

            spid = str(fields.get('session_id', '') or '')
            login = str(fields.get('username', '') or '')
            host = str(fields.get('client_hostname', '') or '')
            app = str(fields.get('client_app_name', '') or '')
            database = str(fields.get('database_name', '') or '')

            # Очищаем timestamp до читаемого вида
            time_str = timestamp.replace('T', ' ')[:19] if timestamp else ''

            # Детерминированный unique_key для дедупликации в ProfilerView.
            # Полный timestamp (с микросекундами) + SPID + хеш SQL —
            # практически гарантирует уникальность.
            unique_key = f"{timestamp}|{spid}|{hash(sql_text)}"

            return {
                'time': time_str,
                'operation': op,
                'object': obj,
                'spid': spid,
                'duration_ms': duration_ms,
                'sql_text': sql_text,
                'login': login,
                'host': host,
                'program': app,
                'database': database,
                'reads': int(fields.get('logical_reads', 0) or 0),
                'writes': int(fields.get('writes', 0) or 0),
                'cpu_ms': cpu_ms,
                'rows': int(fields.get('row_count', 0) or 0),
                'tx_id': '',
                'detail': (
                    f"{login}@{host} ({app}) | {duration_ms}ms | "
                    f"reads:{fields.get('logical_reads', 0)} "
                    f"writes:{fields.get('writes', 0)} "
                    f"cpu:{cpu_ms}ms"
                ),
                'unique_key': unique_key,
            }
        except Exception:
            return None

    def profiler_get_last_lsn(self, db_type, params):
        """Совместимость со старым API. В XEvents-режиме маркер начинается с 0."""
        return '0'

    def _profiler_poll_mssql_dmv(self, cur, last_marker):
        """
        FALLBACK: если XEvents недоступны (нет прав ALTER ANY EVENT SESSION).
        Старый DMV-режим — видим только активные запросы прямо сейчас.
        """
        query = """
            SELECT
                r.session_id,
                r.start_time,
                r.status,
                r.command,
                DB_NAME(r.database_id)         AS db_name,
                s.login_name,
                s.host_name,
                s.program_name,
                r.cpu_time,
                r.total_elapsed_time,
                r.reads,
                r.writes,
                r.logical_reads,
                r.row_count,
                r.transaction_id,
                SUBSTRING(
                    t.text,
                    (r.statement_start_offset / 2) + 1,
                    CASE
                        WHEN r.statement_end_offset = -1
                            THEN DATALENGTH(t.text)
                        ELSE (r.statement_end_offset - r.statement_start_offset) / 2 + 1
                    END
                ) AS statement_text,
                t.text AS full_text
            FROM sys.dm_exec_requests r
            INNER JOIN sys.dm_exec_sessions s ON r.session_id = s.session_id
            OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t
            WHERE r.session_id >= 50
              AND r.session_id <> @@SPID
              AND s.is_user_process = 1
              AND r.status NOT IN ('background', 'sleeping')
        """
        try:
            cur.execute(query)
        except pyodbc.Error as e:
            err = str(e).lower()
            if 'permission' in err or 'denied' in err or 'view server state' in err:
                return {
                    'events': [{
                        'error': 'Для DMV-режима нужно право VIEW SERVER STATE.'
                    }],
                    'new_marker': last_marker,
                }
            return {'events': [{'error': str(e)}], 'new_marker': last_marker}

        columns = [desc[0] for desc in cur.description]
        events = []

        for row in cur.fetchall():
            d = dict(zip(columns, row))
            sql_text = (d.get('statement_text') or d.get('full_text') or '').strip()
            if not sql_text:
                continue

            op = self._detect_operation(sql_text, d.get('command'))
            obj = self._extract_table_name(sql_text, op)

            spid = str(d.get('session_id', ''))
            start_time = d.get('start_time')
            time_str = str(start_time)[:19] if start_time else ''
            elapsed = d.get('total_elapsed_time', 0) or 0

            unique_key = f"{spid}|{time_str}|{hash(sql_text)}"

            events.append({
                'time': time_str,
                'operation': op,
                'object': obj,
                'spid': spid,
                'duration_ms': elapsed,
                'sql_text': sql_text,
                'login': str(d.get('login_name', '') or ''),
                'host': str(d.get('host_name', '') or ''),
                'program': str(d.get('program_name', '') or ''),
                'database': str(d.get('db_name', '') or ''),
                'reads': d.get('logical_reads', 0) or 0,
                'writes': d.get('writes', 0) or 0,
                'cpu_ms': d.get('cpu_time', 0) or 0,
                'rows': d.get('row_count', 0) or 0,
                'tx_id': str(d.get('transaction_id', '') or ''),
                'detail': (
                    f"[DMV] {d.get('login_name', '')}@{d.get('host_name', '')} "
                    f"| {elapsed}ms"
                ),
                'unique_key': unique_key,
            })

        cur.close()
        return {'events': events, 'new_marker': last_marker}

    def _profiler_poll_postgres(self, cur, last_marker):
        """
        PostgreSQL Profiler. Стратегия (по приоритету):

          1. pg_stat_monitor (Percona) — ЛУЧШИЙ режим. Видит все запросы
             И умеет показывать РЕАЛЬНЫЕ значения параметров.

          2. pg_stat_statements — стандартное расширение. Видит все запросы,
             но параметры только в виде плейсхолдеров ($1, $2).

          3. pg_stat_activity — fallback. Только активные сейчас запросы.

        Маркером в каждом режиме служит счётчик calls/queryid в памяти
        (in-memory словарь self._pgsm_seen / self._pgss_seen).
        """
        # Проверяем по приоритету: pgsm > pgss > pg_activity
        has_pgsm = False
        has_pgss = False
        try:
            cur.execute("""
                SELECT extname FROM pg_extension
                WHERE extname IN ('pg_stat_monitor', 'pg_stat_statements')
            """)
            installed = {row[0] for row in cur.fetchall()}
            has_pgsm = 'pg_stat_monitor' in installed
            has_pgss = 'pg_stat_statements' in installed
        except Exception:
            pass

        if has_pgsm:
            return self._profiler_poll_pgsm(cur, last_marker)
        elif has_pgss:
            return self._profiler_poll_pgss(cur, last_marker)
        else:
            return self._profiler_poll_pg_activity(cur, last_marker)

    def _profiler_poll_pgsm(self, cur, last_marker):
        """
        Профайлер через pg_stat_monitor (Percona).

        КЛЮЧЕВОЕ ОТЛИЧИЕ от pg_stat_statements: показывает РЕАЛЬНЫЕ значения
        параметров вместо $1, $2 — это управляется параметром
        pg_stat_monitor.pgsm_normalized_query.

        Включаем на уровне сессии: SET pg_stat_monitor.pgsm_normalized_query = false;
        Это не требует рестарта сервера и применяется сразу.

        Колонки (документация Percona):
          queryid, query, calls, total_exec_time, mean_exec_time, rows,
          shared_blks_read, shared_blks_written, datname, userid,
          client_ip, application_name, cmd_type_text

        Дедупликация — через словарь self._pgsm_seen по queryid+bucket.
        """
        # КРИТИЧНО: переключаем сессию на показ реальных значений
        # (по умолчанию в v1.1+ может быть выключено, проверяем явно).
        # Если параметр не существует (например, старая версия) — игнорируем.
        try:
            cur.execute("SET pg_stat_monitor.pgsm_normalized_query = false")
        except Exception:
            pass

        # Запрашиваем данные. ORDER BY queryid — для стабильной дедупликации.
        # Используем подзапрос на колонки, чтобы быть терпимыми к разным
        # версиям pg_stat_monitor (некоторые поля могут отсутствовать).
        try:
            cur.execute("""
                SELECT
                    queryid::text                  AS queryid,
                    bucket::text                   AS bucket,
                    query                          AS query,
                    calls                          AS calls,
                    total_exec_time                AS total_exec_time,
                    mean_exec_time                 AS mean_exec_time,
                    rows                           AS rows,
                    shared_blks_read               AS reads,
                    shared_blks_written            AS writes,
                    datname                        AS datname,
                    userid::regrole::text          AS username,
                    COALESCE(client_ip::text, '')  AS client_ip,
                    COALESCE(application_name, '') AS application_name,
                    COALESCE(cmd_type_text, '')    AS cmd_type
                FROM pg_stat_monitor
                WHERE datname = current_database()
                ORDER BY queryid, bucket
            """)
        except Exception as e:
            # Возможно версия pg_stat_monitor не имеет каких-то колонок.
            # Падаем на pg_stat_statements.
            return self._profiler_poll_pgss(cur, last_marker)

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        # In-memory словарь: (queryid, bucket) → calls_seen на момент пред. poll'а.
        # Ключ включает bucket, потому что после смены бакета счётчик calls
        # сбрасывается и начинается заново.
        if not hasattr(self, '_pgsm_seen'):
            self._pgsm_seen = {}

        events = []
        from datetime import datetime as _dt
        now_str = _dt.now().strftime('%Y-%m-%d %H:%M:%S')

        for row in rows:
            d = dict(zip(columns, row))
            sql_text = (d.get('query') or '').strip()
            if not sql_text:
                continue

            # Фильтр служебных запросов Profiler'а самого
            sql_upper = sql_text.upper()
            if (('PG_STAT_MONITOR' in sql_upper)
                    or ('PG_STAT_STATEMENTS' in sql_upper)
                    or ('PG_STAT_ACTIVITY' in sql_upper)
                    or ('PG_EXTENSION' in sql_upper)):
                continue

            queryid = str(d.get('queryid', ''))
            bucket = str(d.get('bucket', ''))
            calls = int(d.get('calls', 0) or 0)

            seen_key = (queryid, bucket)
            prev_calls = self._pgsm_seen.get(seen_key, None)

            # Первый запуск — пропускаем (чтобы не выгрузить всю историю)
            if prev_calls is None:
                self._pgsm_seen[seen_key] = calls
                continue

            new_calls = calls - prev_calls
            if new_calls <= 0:
                continue

            self._pgsm_seen[seen_key] = calls

            # Тип операции из cmd_type или из текста SQL
            cmd_type = (d.get('cmd_type') or '').upper().strip()
            if cmd_type in ('SELECT', 'INSERT', 'UPDATE', 'DELETE',
                            'MERGE', 'UTILITY'):
                op = cmd_type
            else:
                op = self._detect_operation(sql_text)

            obj = self._extract_table_name(sql_text, op)

            mean_ms = float(d.get('mean_exec_time', 0) or 0)
            total_ms = float(d.get('total_exec_time', 0) or 0)

            unique_key = f"pgsm|{queryid}|{bucket}|{calls}"

            detail_str = (
                f"{d.get('username', '')}@{d.get('datname', '')} | "
                f"вызовов: {new_calls} (всего {calls} в бакете) | "
                f"avg {mean_ms:.2f}мс | total {total_ms:.1f}мс"
            )

            events.append({
                'time': now_str,
                'operation': op,
                'object': obj,
                'spid': queryid[:8],  # короткий queryid в SPID
                'duration_ms': int(mean_ms),
                'sql_text': sql_text,
                'login': str(d.get('username', '') or ''),
                'host': str(d.get('client_ip', '') or ''),
                'program': str(d.get('application_name', '') or ''),
                'database': str(d.get('datname', '') or ''),
                'reads': int(d.get('reads', 0) or 0),
                'writes': int(d.get('writes', 0) or 0),
                'cpu_ms': 0,
                'rows': int(d.get('rows', 0) or 0),
                'tx_id': '',
                'detail': detail_str,
                'unique_key': unique_key,
            })

        return {'events': events, 'new_marker': last_marker}

    def _profiler_poll_pgss(self, cur, last_marker):
        """
        Профайлер через pg_stat_statements — видим все выполненные запросы
        с агрегированной статистикой.

        Дедупликация:
          - pg_stat_statements хранит для каждого нормализованного запроса
            (queryid) общее число вызовов (`calls`) с момента сброса.
          - last_marker хранит словарь {queryid: calls_seen} на момент
            предыдущего опроса. При новом опросе показываем только те queryid,
            у которых calls вырос — и говорим «было N, стало M, +K новых».
          - Для упрощения сериализации в str — последний маркер
            храним как timestamp + используем in-memory словарь self._pgss_seen.
        """
        try:
            cur.execute("""
                SELECT
                    s.queryid::text       AS queryid,
                    s.query               AS query,
                    s.calls               AS calls,
                    s.total_exec_time     AS total_exec_time,
                    s.mean_exec_time      AS mean_exec_time,
                    s.rows                AS rows,
                    s.shared_blks_read    AS reads,
                    s.shared_blks_written AS writes,
                    r.rolname             AS username,
                    d.datname             AS dbname
                FROM pg_stat_statements s
                LEFT JOIN pg_roles r ON r.oid = s.userid
                LEFT JOIN pg_database d ON d.oid = s.dbid
                WHERE d.datname = current_database()
                ORDER BY s.queryid
            """)
        except Exception as e:
            # Какая-то ошибка чтения pg_stat_statements (например изменилась
            # схема в новой версии PG). Падаем на pg_stat_activity.
            return self._profiler_poll_pg_activity(cur, last_marker)

        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()

        # In-memory словарь предыдущих значений calls по queryid.
        # Хранится в самом коннекторе — переживает между poll'ами.
        if not hasattr(self, '_pgss_seen'):
            self._pgss_seen = {}

        events = []
        from datetime import datetime as _dt
        now_str = _dt.now().strftime('%Y-%m-%d %H:%M:%S')

        for row in rows:
            d = dict(zip(columns, row))
            sql_text = (d.get('query') or '').strip()
            if not sql_text:
                continue

            queryid = str(d.get('queryid', ''))
            calls = int(d.get('calls', 0) or 0)
            prev_calls = self._pgss_seen.get(queryid, None)

            # Первый запуск (нет в seen) — пропускаем, чтобы не вывалить
            # всю историю с момента инсталляции расширения.
            if prev_calls is None:
                self._pgss_seen[queryid] = calls
                continue

            # Не было новых вызовов с прошлого poll'а — пропускаем
            new_calls = calls - prev_calls
            if new_calls <= 0:
                continue

            self._pgss_seen[queryid] = calls

            # Фильтр служебных запросов Profiler'а самого
            sql_upper = sql_text.upper()
            if (('PG_STAT_STATEMENTS' in sql_upper)
                    or ('PG_STAT_ACTIVITY' in sql_upper)
                    or ('PG_EXTENSION' in sql_upper)):
                continue

            op = self._detect_operation(sql_text)
            obj = self._extract_table_name(sql_text, op)

            mean_ms = float(d.get('mean_exec_time', 0) or 0)
            total_ms = float(d.get('total_exec_time', 0) or 0)

            unique_key = f"pgss|{queryid}|{calls}"

            detail_str = (
                f"{d.get('username', '')}@{d.get('dbname', '')} | "
                f"вызовов: {new_calls} (всего {calls}) | "
                f"avg {mean_ms:.2f}мс | total {total_ms:.1f}мс"
            )

            events.append({
                'time': now_str,
                'operation': op,
                'object': obj,
                'spid': queryid[:8],  # короткий queryid в колонке SPID
                'duration_ms': int(mean_ms),
                'sql_text': sql_text,
                'login': str(d.get('username', '') or ''),
                'host': '',
                'program': '',
                'database': str(d.get('dbname', '') or ''),
                'reads': int(d.get('reads', 0) or 0),
                'writes': int(d.get('writes', 0) or 0),
                'cpu_ms': 0,
                'rows': int(d.get('rows', 0) or 0),
                'tx_id': '',
                'detail': detail_str,
                'unique_key': unique_key,
            })

        return {'events': events, 'new_marker': last_marker}

    def _profiler_poll_pg_activity(self, cur, last_marker):
        """
        Fallback: pg_stat_activity. Видны только активные сейчас запросы.
        Быстрые запросы пропустим между poll'ами — это ограничение метода.
        """
        cur.execute("""
            SELECT pid,
                   state,
                   usename,
                   datname,
                   client_addr,
                   application_name,
                   COALESCE(LEFT(query, 2000), '') AS query,
                   query_start,
                   xact_start,
                   backend_xid,
                   wait_event_type,
                   wait_event,
                   EXTRACT(EPOCH FROM (now() - query_start)) * 1000 AS duration_ms
            FROM pg_stat_activity
            WHERE pid != pg_backend_pid()
              AND datname = current_database()
              AND state IS NOT NULL
              AND state != 'idle'
              AND query NOT ILIKE 'SET %%'
              AND query NOT ILIKE 'SHOW %%'
              AND query NOT ILIKE '%%pg_stat_activity%%'
              AND query NOT ILIKE '%%pg_stat_statements%%'
              AND query NOT ILIKE '%%pg_extension%%'
            ORDER BY query_start ASC NULLS LAST
        """)

        columns = [desc[0] for desc in cur.description]
        events = []

        for row in cur.fetchall():
            d = dict(zip(columns, row))
            sql_text = (d.get('query') or '').strip()
            if not sql_text:
                continue

            op = self._detect_operation(sql_text, d.get('state'))
            obj = self._extract_table_name(sql_text, op)
            pid = str(d.get('pid', ''))
            qstart = d.get('query_start')
            time_str = str(qstart)[:19] if qstart else ''
            duration_ms = int(d.get('duration_ms', 0) or 0)

            unique_key = f"pgact|{pid}|{time_str}|{hash(sql_text)}"

            events.append({
                'time': time_str,
                'operation': op,
                'object': obj,
                'spid': pid,
                'duration_ms': duration_ms,
                'sql_text': sql_text,
                'login': str(d.get('usename', '') or ''),
                'host': str(d.get('client_addr', '') or ''),
                'program': str(d.get('application_name', '') or ''),
                'database': str(d.get('datname', '') or ''),
                'reads': 0,
                'writes': 0,
                'cpu_ms': 0,
                'rows': 0,
                'tx_id': str(d.get('backend_xid', '') or ''),
                'detail': (
                    f"[pg_stat_activity] {d.get('usename', '')}"
                    f"@{d.get('client_addr', '')} | state: {d.get('state', '')} "
                    f"| wait: {d.get('wait_event', '') or '-'}"
                ),
                'unique_key': unique_key,
            })

        cur.close()
        return {'events': events, 'new_marker': last_marker}

    @staticmethod
    def _detect_operation(sql_text: str, fallback: str = None) -> str:
        """Определить тип операции по первому ключевому слову SQL."""
        if not sql_text:
            return (fallback or 'QUERY').upper()
        upper = sql_text.upper().lstrip()
        for kw in ('INSERT', 'UPDATE', 'DELETE', 'SELECT',
                   'MERGE', 'EXECUTE', 'EXEC'):
            if upper.startswith(kw):
                return 'EXEC' if kw in ('EXECUTE', 'EXEC') else kw
        return (fallback or 'QUERY').upper()

    @staticmethod
    def _extract_table_name(sql_text: str, op: str) -> str:
        """Простой парсер имени таблицы. Не претендует на полноту."""
        if not sql_text:
            return ''

        upper = sql_text.upper()
        try:
            if op == 'INSERT':
                idx = upper.find('INTO')
                if idx >= 0:
                    rest = sql_text[idx + 4:].strip()
                    return rest.split()[0].rstrip('(').strip('[]"`') if rest else ''
            elif op == 'UPDATE':
                idx = upper.find('UPDATE')
                if idx >= 0:
                    rest = sql_text[idx + 6:].strip()
                    return rest.split()[0].strip('[]"`') if rest else ''
            elif op == 'DELETE':
                idx = upper.find('FROM')
                if idx >= 0:
                    rest = sql_text[idx + 4:].strip()
                    return rest.split()[0].strip('[]"`') if rest else ''
            elif op == 'SELECT':
                idx = upper.find('FROM')
                if idx >= 0:
                    rest = sql_text[idx + 4:].strip()
                    return rest.split()[0].strip('[]"`,') if rest else ''
        except Exception:
            pass
        return ''