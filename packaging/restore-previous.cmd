@echo off
rem Restaure la version précédente de Studio Vidéo (copie conservée par l'installateur).
rem Usage : restore-previous.cmd "<dossier d'installation>"
chcp 65001 >nul
setlocal
set "DATA=%LOCALAPPDATA%\StudioVideo"
set "SRC=%DATA%\rollback\app"
set "DST=%~1"
if "%DST%"=="" set "DST=%LOCALAPPDATA%\Programs\StudioVideo"

if not exist "%SRC%\StudioVideo.exe" (
  echo Aucune version precedente n'est conservee.
  pause
  exit /b 1
)
set /p PREV=<"%SRC%\version.txt"
echo Restauration de Studio Video %PREV% dans "%DST%".
echo Fermez Studio Video s'il est ouvert, puis appuyez sur une touche.
pause >nul
taskkill /IM StudioVideo.exe /F >nul 2>&1
timeout /t 2 >nul

robocopy "%SRC%" "%DST%" /MIR /XF version.txt /R:3 /W:1 /NFL /NDL /NJH /NJS
if %ERRORLEVEL% GEQ 8 (
  echo Echec de la copie des fichiers.
  pause
  exit /b 1
)

rem Restaure la base telle qu'avant la derniere mise a jour (schema compatible avec l'ancienne version).
set "LAST="
for /f "delims=" %%F in ('dir /b /o-n "%DATA%\backups\studio-*-avant-mise-a-jour-*.db" 2^>nul') do if not defined LAST set "LAST=%%F"
if defined LAST (
  copy /Y "%DATA%\studio.db" "%DATA%\backups\studio-avant-restauration-version.db" >nul
  copy /Y "%DATA%\backups\%LAST%" "%DATA%\studio.db" >nul
  del /Q "%DATA%\studio.db-wal" "%DATA%\studio.db-shm" 2>nul
  echo Base de donnees restauree depuis %LAST%.
)
echo %DATE% %TIME% Restauration manuelle de la version %PREV%>>"%DATA%\logs\updates.log"
echo Restauration terminee.
start "" "%DST%\StudioVideo.exe"
endlocal
