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

rem Bazi Python kurulumlari "venv" modulu olmadan gelir, once onu dogrula.
%BOOT_PY% -m venv --help >nul 2>&1
if errorlevel 1 goto no_venv_module

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
echo [holocron] Python kurmak istemiyorsaniz python-embed iceren tam paketi indirin:
echo [holocron] holocron-windows-x64.zip
pause
exit /b 1

:no_venv_module
echo [holocron] Python bulundu ama "venv" modulu yok, sanal ortam kurulamiyor.
echo [holocron] Cozum 1: python.org uzerinden Python 3.13 kurun, kurulumda
echo [holocron]           pip ve standart kitaplik secenekleri isaretli kalsin.
echo [holocron] Cozum 2: Python gerektirmeyen tam paketi indirin:
echo [holocron]           holocron-windows-x64.zip, python-embed ile gelir.
pause
exit /b 1

:venv_failed
echo [holocron] Sanal ortam kurulamadi, .venv klasoru olusmadi.
echo [holocron] Bu klasorde yazma izniniz var mi? Ag surucusu ya da OneDrive
echo [holocron] altindaysaniz paketi yerel bir diske tasiyip yeniden deneyin.
pause
exit /b 1

:deps_failed
echo [holocron] Bagimliliklar kurulamadi.
pause
exit /b 1
