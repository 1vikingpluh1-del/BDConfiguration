# -*- mode: python ; coding: utf-8 -*-
# ==============================================================================
# main.spec — Спецификация для сборки .exe через PyInstaller
# ==============================================================================
# Использование:
#   pyinstaller main.spec
#
# Результат:
#   dist/main.exe — готовое приложение
#
# Примечание:
#   После разделения на модули все .py-файлы подхватываются автоматически
#   через Analysis, т.к. main.py импортирует app.py, который импортирует
#   все остальные модули.
# ==============================================================================


a = Analysis(
    ['main.py'],          # Точка входа
    pathex=[],            # Дополнительные пути поиска модулей (если нужно)
    binaries=[],          # Бинарные файлы (DLL и т.д.)
    datas=[],             # Дополнительные файлы данных (config.ini НЕ включаем — создаётся при запуске)
    hiddenimports=[
    'views',
        'views.db_structure_view',
        'views.profiler_view',
        'views.mass_add_view',
        'views.maraprc_viewer',
        'person_generator',
    ],     # Скрытые импорты (если PyInstaller не находит автоматически)
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='db_tool',                    # Имя выходного файла (было 'main', стало 'db_tool')
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                          # UPX-сжатие (уменьшает размер .exe)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                     # False = без консольного окна (только GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
