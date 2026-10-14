; Inno Setup script - run build.bat first (it produces dist\Zapi\).
; The installer carries Lavati Softwares branding (wizard artwork, publisher); the app itself uses Zapi's own icon.
#define AppName "Zapi"
#define AppVersion "0.1"
#define AppExe "Zapi.exe"

[Setup]
AppId={{3C7E2B54-9A1D-4F0B-8E63-5D2A7C91B4E8}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Lavati Softwares
AppCopyright=Copyright (c) 2026 Lavati Softwares (MIT License)
LicenseFile=installer\EULA.txt
#ifdef Sign
SignTool=lavatisign $f
SignedUninstaller=yes
#endif
VersionInfoCompany=Lavati Softwares
VersionInfoDescription=Zapi Setup
VersionInfoProductName=Zapi
WizardSizePercent=115
DisableProgramGroupPage=yes
AllowNoIcons=yes
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\zapi.ico
OutputDir=dist
OutputBaseFilename=Zapi-Setup
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
SolidCompression=yes
WizardStyle=modern
WizardImageFile=installer\wizard-large.bmp
WizardSmallImageFile=installer\wizard-small.bmp
InfoBeforeFile=installer\welcome.txt
DisableWelcomePage=no
DisableReadyPage=no
ShowLanguageDialog=no
SetupMutex=ZapiSetupMutex
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
DirExistsWarning=no
UsePreviousTasks=yes
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ChangesEnvironment=yes

[Messages]
WelcomeLabel1=Welcome to Zapi
WelcomeLabel2=This will install Zapi {#AppVersion} on your computer.%n%nIt is recommended that you close other applications before continuing.
FinishedHeadingLabel=Zapi is ready
FinishedLabel=Setup has finished installing Zapi. Start it from the Start menu or the desktop shortcut.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "addtopath"; Description: "Add the &zapi-cli command (collection runner for scripts and CI) to the PATH"; GroupDescription: "Integration:"

[Files]
Source: "dist\Zapi\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\THIRD_PARTY_NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; per-user PATH entry for zapi-cli.exe (removed again on uninstall)
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[InstallDelete]
; a clean update: remove files of the previous version that the new one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  KeepData: Boolean;

const
  EnvKey = 'Environment';

function NeedsAddPath(Param: string): Boolean;
var
  Paths: string;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then
    Paths := '';
  Result := Pos(';' + Uppercase(Param) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;

procedure RemoveFromPath(Param: string);
var
  Paths, Padded: string;
  P: Integer;
begin
  if not RegQueryStringValue(HKCU, EnvKey, 'Path', Paths) then Exit;
  { match whole entries only: C:\Zapi must not match C:\ZapiTools. The outer ';' make the first and last entry look like the others }
  Padded := ';' + Paths + ';';
  P := Pos(';' + Uppercase(Param) + ';', Uppercase(Padded));
  if P = 0 then Exit;
  Delete(Padded, P, Length(Param) + 1);                    { ";entry" goes, the ';' after it stays as the separator }
  Paths := Copy(Padded, 2, Length(Padded) - 2);
  RegWriteExpandStringValue(HKCU, EnvKey, 'Path', Paths);
end;

procedure DeleteTree(const Path: String);
begin
  if DirExists(Path) then DelTree(Path, True, True, True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    { a running copy keeps its files locked, which would leave the folder behind }
    Exec(ExpandConstant('{cmd}'), '/c taskkill /F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveFromPath(ExpandConstant('{app}'));
    if not KeepData then
      DeleteTree(ExpandConstant('{userappdata}\Zapi'));
    { the uninstaller lives inside the install folder, so remove it a moment after it exits.
      Only our own files go, and rmdir without /s only removes the folder when it is empty, so a
      shared folder chosen at install time is never wiped. }
    Exec(ExpandConstant('{cmd}'), '/c ping 127.0.0.1 -n 3 >nul & del /q "' + ExpandConstant('{app}') + '\unins*.*" & rmdir "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewNoWait, Code);
  end;
end;

function HasParam(const Name: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), Name) = 0 then Result := True;
end;

{ make sure a running copy is closed before an update replaces its files }
function InitializeUninstall(): Boolean;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/c taskkill /F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { silent uninstalls wipe everything unless started with /KEEPDATA; interactive ones ask (default: wipe) }
  if UninstallSilent then
    KeepData := HasParam('/KEEPDATA')
  else
    KeepData := MsgBox('Keep your collections, environments and history?' + #13#10 + #13#10 +
      'Choose Yes to keep them for a future install, or No to remove all Zapi data from this computer.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;
