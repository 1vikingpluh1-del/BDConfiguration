; ==============================================================================
; installer.iss — инсталлятор BDConfiguration (Inno Setup 6)
; ==============================================================================
#define MyAppName "BDConfiguration"
#define MyAppVersion "1.5.3 fix + micro fix"
#define MyAppPublisher "Bolid"
#define MyAppExeName "BDConfiguration.exe"
#define SrcDir "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\dist\BDConfiguration"

[Setup]
AppId={{8A6F2C41-3B7D-4E5A-9C12-7F0E5D3B9A21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\installer_output
OutputBaseFilename={#MyAppName}_setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
;Name: "english"; MessagesFile: "compiler:Default.isl"
; Раскомментируй, если положил Russian.isl в папку Languages:
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; Flags: unchecked

[Files]
; Вся собранная папка PyInstaller
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Папка фото (если есть в проекте); если нет — строка просто пропустится
Source: "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\personnel_photos\*"; DestDir: "{app}\personnel_photos"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
; Пустая папка под фото на будущее (код сам предупредит, если фото нет)
Name: "{app}\personnel_photos"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent