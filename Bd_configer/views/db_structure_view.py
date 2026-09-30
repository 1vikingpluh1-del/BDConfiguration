# ==============================================================================
# views/db_structure_view.py — окно просмотра структуры БД
# ==============================================================================
# Раньше этот код жил внутри DBConnectionApp как _show_db_structure() и
# _on_tree_node_open(). Вынесен в отдельный класс для читаемости.
#
# Использование:
#     from views.db_structure_view import DBStructureView
#     DBStructureView(parent_frame, connector, db_type, params, db_name,
#                     on_close=callback, on_open_profiler=callback)
# ==============================================================================

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from constants import (
    COLOR_INTERACT_BTN,
    COLOR_CLOSE_BTN,
)


class DBStructureView:
    """
    Виджет (Frame) со структурой БД, встраиваемый в главное окно.

    Параметры:
        parent: окно/фрейм, в который встраиваемся
        connector: DBConnector
        db_type: тип СУБД ('PostgreSQL' / 'Microsoft SQL Server')
        params: параметры подключения
        db_name: имя текущей БД (для заголовка и имени файла отчёта)
        on_close: callback, вызывается при нажатии «Закрыть»
        on_open_profiler: callback, вызывается при нажатии «Profiler»
    """

    def __init__(self, parent, connector, db_type, params, db_name,
                 on_close=None, on_open_profiler=None):
        self._parent = parent
        self._connector = connector
        self._db_type = db_type
        self._params = params
        self._db_name = db_name
        self._on_close = on_close
        self._on_open_profiler = on_open_profiler

        # --- Создаём фрейм ---
        self.frame = tk.Frame(parent, bd=2, relief="groove")
        self.frame.grid(row=14, column=0, columnspan=7,
                        sticky="nsew", padx=10, pady=10)

        # --- Заголовок ---
        tk.Label(
            self.frame,
            text=f"Структура БД: {db_name}",
            font=("Arial", 10, "bold"),
        ).pack(pady=(5, 0))

        # --- Treeview с колонками ---
        tree_frame = tk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        v_scroll = tk.Scrollbar(tree_frame, orient=tk.VERTICAL)
        h_scroll = tk.Scrollbar(tree_frame, orient=tk.HORIZONTAL)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=("type", "nullable", "default"),
            show="tree headings",
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set,
            height=15,
        )

        self._tree.heading("#0", text="Имя", anchor="w")
        self._tree.heading("type", text="Тип данных", anchor="w")
        self._tree.heading("nullable", text="NULL", anchor="w")
        self._tree.heading("default", text="По умолчанию", anchor="w")

        self._tree.column("#0", width=280, minwidth=200)
        self._tree.column("type", width=120, minwidth=80)
        self._tree.column("nullable", width=70, minwidth=50)
        self._tree.column("default", width=150, minwidth=80)

        v_scroll.config(command=self._tree.yview)
        h_scroll.config(command=self._tree.xview)

        self._tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # --- Заполняем дерево ---
        self._populate()

        # --- Ленивая загрузка столбцов ---
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_node_open)

        # --- Кнопки ---
        btn_frame = tk.Frame(self.frame)
        btn_frame.pack(pady=(5, 10))

        tk.Button(
            btn_frame, text="Выгрузить отчёт",
            command=self._export_db_structure,
            bg=COLOR_INTERACT_BTN, fg="white", width=20,
        ).pack(side="left", padx=10)

        tk.Button(
            btn_frame, text="Profiler",
            command=self._open_profiler,
            bg="#9C27B0", fg="white", width=15,
        ).pack(side="left", padx=10)

        tk.Button(
            btn_frame, text="Закрыть",
            command=self._close,
            bg=COLOR_CLOSE_BTN, fg="white", width=15,
        ).pack(side="left", padx=10)

    # -------------------------------------------------------------------------

    def destroy(self):
        """Уничтожить виджет (вызывается извне при необходимости)."""
        if self.frame and self.frame.winfo_exists():
            self.frame.destroy()

    # -------------------------------------------------------------------------

    def _populate(self):
        """Загрузить и отобразить таблицы и хранимые процедуры."""
        try:
            tables = self._connector.get_tables_list(self._db_type, self._params)

            schemas = {}
            for schema, table_name, column_count in tables:
                schemas.setdefault(schema, []).append((table_name, column_count))

            if not schemas:
                self._tree.insert(
                    "", "end",
                    text="(Таблицы не найдены в этой базе данных)",
                    values=("", "", ""),
                )
            else:
                for schema, table_list in schemas.items():
                    schema_node = self._tree.insert(
                        "", "end",
                        text=f"📁 {schema} ({len(table_list)} таблиц)",
                        open=False,
                    )
                    for table_name, column_count in table_list:
                        table_node = self._tree.insert(
                            schema_node, "end",
                            text=f"📋 {table_name} ({column_count} столбцов)",
                            values=("", "", ""),
                        )
                        self._tree.item(table_node, tags=(schema, table_name))
                        # Placeholder для ленивой загрузки колонок
                        self._tree.insert(table_node, "end", text="Загрузка...")

            # Хранимые процедуры
            proc_count = 0
            try:
                procedures = self._connector.get_procedures_list(
                    self._db_type, self._params
                )
                proc_count = len(procedures)
                if procedures:
                    procs_node = self._tree.insert(
                        "", "end",
                        text=f"⚙ Хранимые процедуры ({proc_count})",
                        open=False,
                    )
                    for proc in procedures:
                        proc_name = proc[0] if proc else '???'
                        self._tree.insert(
                            procs_node, "end",
                            text=f"  {proc_name}",
                            values=("", "", ""),
                        )
            except Exception:
                pass

            summary = f"Таблиц: {len(tables)} в {len(schemas)} схемах"
            if proc_count:
                summary += f" | Процедур: {proc_count}"
            tk.Label(
                self.frame, text=summary,
                font=("Arial", 9), fg="gray",
            ).pack(pady=(0, 5))

        except Exception as e:
            messagebox.showerror(
                "Ошибка",
                f"Не удалось получить структуру БД:\n{str(e)}"
            )

    def _on_tree_node_open(self, event):
        """Ленивая загрузка столбцов при раскрытии узла таблицы."""
        node = self._tree.focus()
        children = self._tree.get_children(node)

        if (len(children) == 1
                and self._tree.item(children[0], "text") == "Загрузка..."):
            self._tree.delete(children[0])

            tags = self._tree.item(node, "tags")
            if len(tags) < 2:
                return

            schema, table_name = tags[0], tags[1]

            try:
                columns = self._connector.get_table_columns(
                    self._db_type, self._params, schema, table_name
                )

                self._tree.item(
                    node, text=f"📋 {table_name} ({len(columns)} столбцов)"
                )

                for col in columns:
                    col_name = col[0] or '???'
                    col_type = col[1] or '???'
                    nullable = ('NULL'
                                if (col[2] or '').upper() == 'YES'
                                else 'NOT NULL')
                    default = str(col[3]) if col[3] else ''

                    self._tree.insert(
                        node, "end",
                        text=f"  {col_name}",
                        values=(col_type, nullable, default),
                    )
            except Exception as e:
                self._tree.insert(
                    node, "end",
                    text=f"  Ошибка: {str(e)}",
                    values=("", "", ""),
                )

    def _export_db_structure(self):
        """Выгрузить полную структуру БД в текстовый файл."""
        filepath = filedialog.asksaveasfilename(
            title="Сохранить отчёт по структуре БД",
            defaultextension=".txt",
            initialfile=f"structure_{self._db_name}.txt",
            filetypes=[("Текстовые файлы", "*.txt"), ("Все файлы", "*.*")],
        )

        if not filepath:
            return

        try:
            report = self._connector.get_full_structure_for_export(
                self._db_type, self._params
            )
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report)
            messagebox.showinfo("Успех", f"Отчёт сохранён:\n{filepath}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось выгрузить отчёт:\n{str(e)}")

    def _open_profiler(self):
        if self._on_open_profiler:
            self._on_open_profiler()

    def _close(self):
        if self._on_close:
            self._on_close()
