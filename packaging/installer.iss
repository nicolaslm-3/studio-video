; Inno Setup 6 — installateur Studio Vidéo
; Compilation : iscc packaging\installer.iss   (après PyInstaller ; version.iss est produit par tools/stamp_build.py)
;
; - Installation par utilisateur, SANS droits administrateur (%LOCALAPPDATA%\Programs\StudioVideo).
; - AppId fixe : chaque nouvelle version remplace la précédente sans désinstallation.
; - Avant de remplacer les fichiers, la version installée est copiée dans
;   %LOCALAPPDATA%\StudioVideo\rollback\app (restauration possible).
; - Les données utilisateur (%LOCALAPPDATA%\StudioVideo) ne sont jamais supprimées,
;   ni à la mise à jour, ni à la désinstallation.

#include "version.iss"

#define AppName "Studio Vidéo"
#define AppExe "StudioVideo.exe"
#define DataDir "{localappdata}\StudioVideo"

[Setup]
AppId={{6E1F3C2A-8B4D-4E7A-9C51-2F0D7A3B9E64}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=StudioVideo
VersionInfoVersion={#AppVersionNumeric}
DefaultDirName={localappdata}\Programs\StudioVideo
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=StudioVideo-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
UninstallDisplayIcon={app}\{#AppExe}
SetupLogging=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; Flags: unchecked

[Files]
Source: "..\dist\StudioVideo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "restore-previous.cmd"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Nettoie les fichiers d'une ancienne version qui n'existent plus (évite les DLL orphelines).
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{#AppName} — restaurer la version précédente"; Filename: "{#DataDir}\rollback\restore-previous.cmd"; Parameters: """{app}"""; Check: HasRollback
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; Relance automatique après installation (y compris en mode /SILENT déclenché par la mise à jour).
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall

[Code]
var
  PreviousVersion: String;

function InstalledVersion(): String;
var V: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E1F3C2A-8B4D-4E7A-9C51-2F0D7A3B9E64}_is1',
                         'DisplayVersion', V) then Result := V;
end;

function HasRollback(): Boolean;
begin
  Result := FileExists(ExpandConstant('{#DataDir}\rollback\app\{#AppExe}'));
end;

procedure BackupPreviousVersion();
var
  Src, Dst: String;
  Code: Integer;
begin
  Src := ExpandConstant('{app}');
  Dst := ExpandConstant('{#DataDir}\rollback\app');
  if not FileExists(Src + '\{#AppExe}') then Exit;
  Log('Sauvegarde de la version ' + PreviousVersion + ' vers ' + Dst);
  // robocopy : codes < 8 = succès
  if Exec(ExpandConstant('{sys}\robocopy.exe'), '"' + Src + '" "' + Dst + '" /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS',
          '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code < 8) then
  begin
    SaveStringToFile(Dst + '\version.txt', PreviousVersion, False);
    FileCopy(ExpandConstant('{src}\restore-previous.cmd'), ExpandConstant('{#DataDir}\rollback\restore-previous.cmd'), False);
  end
  else
    Log('Copie de sauvegarde impossible (code ' + IntToStr(Code) + ') : la restauration ne sera pas proposée.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  PreviousVersion := InstalledVersion();
  if (PreviousVersion <> '') and (PreviousVersion <> '{#AppVersion}') then
    BackupPreviousVersion();
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(ExpandConstant('{#DataDir}\rollback'));
    // Le script de restauration doit rester disponible même si le programme ne démarre plus.
    FileCopy(ExpandConstant('{app}\restore-previous.cmd'), ExpandConstant('{#DataDir}\rollback\restore-previous.cmd'), False);
  end;
end;
