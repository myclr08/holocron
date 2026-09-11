@echo off
rem Holocron baslatici (Windows).
rem Sira: tasinabilir python-embed > mevcut .venv > yeni .venv kurulumu.
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"

if exist "python-embed\python.exe" goto run_embed
if exist "%VENV_PY%" goto run_venv
goto create_venv

:run_embed
if exist "python-embed\pythonw.exe" (
  start "" "python-embed\pythonw.exe" -m app %*
) else (
  start "" "python-embed\python.exe" -m app %*
)
goto :eof

:create_venv
set "BOOT_PY="
py -3 --version >nul 2>&1 && set "BOOT_PY=py -3"
if not defined BOOT_PY python --version >nul 2>&1 && set "BOOT_PY=python"
if not defined BOOT_PY goto no_python

echo [holocron] Sanal ortam kuruluyor...
%BOOT_PY% -m venv .venv
if not exist "%VENV_PY%" goto venv_failed
"%VENV_PY%" -m pip install --upgrade pip >nul

rem Cevrimdisi kurulum: paketler yanimizda geldiyse agi hic kullanma.
if exist "wheels\*" goto offline_install
echo [holocron] Bagimliliklar indiriliyor...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto deps_failed
goto run_venv

:offline_install
echo [holocron] Bagimliliklar wheels klasorunden kuruluyor...
"%VENV_PY%" -m pip install --no-index --find-links wheels -r requirements.txt
if errorlevel 1 goto online_install
goto run_venv

:online_install
rem Tekerlekler baska bir Python surumu icin olabilir; agdan denenir.
echo [holocron] Cevrimdisi kurulum olmadi, agdan deneniyor...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto deps_failed
goto run_venv

:run_venv
rem Konsol penceresi acik kalmasin diye pythonw tercih edilir.
if exist "%VENV_PYW%" (
  start "" "%VENV_PYW%" -m app %*
) else (
  start "" "%VENV_PY%" -m app %*
)
goto :eof

:no_python
echo [holocron] Python bulunamadi. Python 3.11+ kurun ya da tasinabilir paketi kullanin.
pause
exit /b 1

:venv_failed
echo [holocron] Sanal ortam kurulamadi.
pause
exit /b 1

:deps_failed
echo [holocron] Bagimliliklar kurulamadi.
pause
exit /b 1
