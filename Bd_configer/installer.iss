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

; Папка bmp с картинками — кладём рядом с exe...
Source: "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\bmp\*"; DestDir: "{app}\bmp"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; ...и дублируем во _internal (на случай, если код ищет картинки относительно модулей)
Source: "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\bmp\*"; DestDir: "{app}\_internal\bmp"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Папка фото (опционально)
Source: "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\personnel_photos\*"; DestDir: "{app}\personnel_photos"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist
; Инструкции (кладем в папку docs рядом с exe)
Source: "C:\Users\user\PycharmProjects\BDConfiguration\Bd_configer\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Dirs]
; Пустая папка под фото на будущее (код сам предупредит, если фото нет)
Name: "{app}\personnel_photos"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  Link1: TNewStaticText;
  Link2: TNewStaticText;

procedure OpenDoc1(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', ExpandConstant('{app}\docs\1. Инструкция по настройке пакетов для профилирования.txt'),
            '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure OpenDoc2(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExec('open', ExpandConstant('{app}\docs\2. Инструкция по запуску профилировщика.docx'),
            '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure InitializeWizard();
var
  BaseTop: Integer;
begin
  { Фиксированный отступ 90px от верха FinishedLabel — гарантированно ниже 2 строк текста }
  BaseTop := WizardForm.FinishedLabel.Top + ScaleY(90);

  { Ссылка 1 }
  Link1 := TNewStaticText.Create(WizardForm);
  Link1.Parent := WizardForm.FinishedPage;
  Link1.Caption := 'Открыть инструкцию: Настройка пакетов для профилирования (txt)';
  Link1.Cursor := crHand;
  Link1.Font.Color := clBlue;
  Link1.Font.Style := [fsUnderline];
  Link1.AutoSize := False;
  Link1.WordWrap := True;
  Link1.Left := WizardForm.FinishedLabel.Left;
  Link1.Width := WizardForm.FinishedLabel.Width;
  Link1.Height := ScaleY(32);
  Link1.Top := BaseTop;
  Link1.OnClick := @OpenDoc1;

  { Ссылка 2 }
  Link2 := TNewStaticText.Create(WizardForm);
  Link2.Parent := WizardForm.FinishedPage;
  Link2.Caption := 'Открыть инструкцию: Запуск профилировщика (docx)';
  Link2.Cursor := crHand;
  Link2.Font.Color := clBlue;
  Link2.Font.Style := [fsUnderline];
  Link2.AutoSize := False;
  Link2.WordWrap := True;
  Link2.Left := WizardForm.FinishedLabel.Left;
  Link2.Width := WizardForm.FinishedLabel.Width;
  Link2.Height := ScaleY(32);
  Link2.Top := BaseTop + ScaleY(38);
  Link2.OnClick := @OpenDoc2;

  { Сдвигаем чекбокс "Запустить" ниже ссылок }
  WizardForm.RunList.Top := BaseTop + ScaleY(80);
end;