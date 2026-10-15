@echo off
rem Builds the portable edition: ONE file, dist\Zapi-Portable.exe
rem   Copy it anywhere (a USB stick, a shared folder) and run it. No installer, no registry, no admin rights.
rem   It keeps your workspace, logs and caches in a "ZapiData" folder next to the exe.
rem   (A one-file exe unpacks itself to a temporary folder at every start, so it opens a few seconds slower than the
rem   installed app; the temporary files are removed when you close it.)
setlocal
cd /d "%~dp0"
python -m pip install --quiet -r requirements-dev.txt || goto :err
python tools\make_assets.py || goto :err
python tools\make_version.py || goto :err
if exist dist\Zapi-Portable.exe del /q dist\Zapi-Portable.exe
python -m PyInstaller --noconfirm --workpath build\portable --distpath dist ZapiPortable.spec || goto :err
if not exist dist\Zapi-Portable.exe goto :err
rem --- verify the real file: packaged self-test, and the data folder must be beside the exe (not in %APPDATA%) ---
dist\Zapi-Portable.exe --selftest || (type "%TEMP%\zapi-selftest.log" & goto :err)
set CHECK=%TEMP%\zapi-portable-check
if exist "%CHECK%" rmdir /s /q "%CHECK%"
mkdir "%CHECK%"
copy /y dist\Zapi-Portable.exe "%CHECK%\Zapi-Portable.exe" >nul
"%CHECK%\Zapi-Portable.exe" --data-dir-to "%CHECK%\where.txt"
set /p DATADIR=<"%CHECK%\where.txt"
if /i not "%DATADIR%"=="%CHECK%\ZapiData" (
  echo Portable check FAILED: data folder is "%DATADIR%" instead of "%CHECK%\ZapiData"
  goto :err
)
ping -n 3 127.0.0.1 >nul
rmdir /s /q "%CHECK%" 2>nul
echo.
for %%F in (dist\Zapi-Portable.exe) do echo Portable build: dist\Zapi-Portable.exe  ^(%%~zF bytes^)
exit /b 0
:err
echo Portable build failed.
exit /b 1
