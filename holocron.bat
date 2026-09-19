@echo off
rem Holocron baslatici (Windows).
rem Sira: tasinabilir python-embed > mevcut .venv > yeni .venv kurulumu.
rem "holocron.bat --console" hata ayiklama kipi: python.exe on planda, pencere kapanmaz.
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "ENTRY=%~dp0holocron_run.py"
set "LOGFILE=%~dp0holocron.log"
set "PORTFILE=%~dp0holocron.port"

rem Calisma dizinine guvenme: paket klasoru her zaman aranan yollara girsin.
rem (PYTHONSAFEPATH/-P acikken cwd sys.path'e hic eklenmiyor, "No module
rem named app" hatasi buradan cikiyordu.)
set "PYTHONPATH=%~dp0"

set "CONSOLE="
if /i "%~1"=="--console" set "CONSOLE=1"
if /i "%~1"=="-c" set "CONSOLE=1"
if /i "%~1"=="/console" set "CONSOLE=1"

rem Cift baslatma korumasi: zaten calisan bir ornek varsa yeni surec acma,
rem yalnizca tarayiciyi o adrese getir. (Argumanla calistirildiysa atlanir:
rem --console ya da --port veren kullanici bilerek ikinci ornek istiyordur.)
if not "%~1"=="" goto pick_python
if not exist "%PORTFILE%" goto pick_python
set "PORT="
set /p PORT=<"%PORTFILE%"
if not defined PORT goto pick_python
call :probe_health %PORT%
if not errorlevel 1 goto already_running
rem Port dosyasi bayat: cevap yok, normal baslatmaya devam.
goto pick_python

:already_running
echo [holocron] Zaten calisiyor: http://127.0.0.1:%PORT%/
start "" "http://127.0.0.1:%PORT%/"
goto :eof

:pick_python
if exist "python-embed\python.exe" goto run_embed
if exist "%VENV_PY%" goto run_venv
goto create_venv

:run_embed
rem Tam yol: start ile acilan surec goreli yolu baska bir klasorde arayabilir.
set "RUN_PY=%~dp0python-embed\python.exe"
set "RUN_PYW=%~dp0python-embed\pythonw.exe"
if not exist "python-embed\pythonw.exe" set "RUN_PYW=%RUN_PY%"
call :sync_deps "%RUN_PY%" "python-embed\holocron-req.sha"
goto launch

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
rem wheels/ paketin yanindaki tam listedir.
call :find_links
if defined PIP_LINKS goto offline_install
echo [holocron] Bagimliliklar indiriliyor...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto deps_failed
goto stamp_venv

:offline_install
echo [holocron] Bagimliliklar yerel tekerleklerden kuruluyor...
"%VENV_PY%" -m pip install %PIP_LINKS% -r requirements.txt
if errorlevel 1 goto online_install
goto stamp_venv

:online_install
rem Tekerlekler baska bir Python surumu icin olabilir; agdan denenir.
echo [holocron] Cevrimdisi kurulum olmadi, agdan deneniyor...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 goto deps_failed
goto stamp_venv

:stamp_venv
rem Kurulan liste iste buydu: bir sonraki acilis ozete bakip bosuna kosmaz.
call :req_hash
if not defined REQ_HASH goto run_venv
> ".venv\holocron-req.sha" echo %REQ_HASH%
goto run_venv

:run_venv
call :sync_deps "%VENV_PY%" ".venv\holocron-req.sha"
set "RUN_PY=%~dp0%VENV_PY%"
set "RUN_PYW=%~dp0%VENV_PYW%"
if not exist "%VENV_PYW%" set "RUN_PYW=%RUN_PY%"
goto launch

:launch
if defined CONSOLE goto launch_console

rem Konsol penceresi acik kalmasin diye pythonw tercih edilir.
rem Eski port dosyasi yaniltmasin.
if exist "%PORTFILE%" del "%PORTFILE%" >nul 2>&1
rem /d: start ile acilan surec de paket klasorunde dogsun.
start "" /d "%~dp0" "%RUN_PYW%" "%ENTRY%" %*
rem Bekleme icin ping: dahili sayac komutu yonlendirilmis girdide hata veriyor.
ping -n 7 127.0.0.1 >nul
goto health_check

:launch_console
echo [holocron] Konsol kipi: hata mesajlari bu pencerede kalir.
"%RUN_PY%" "%ENTRY%" %*
echo.
echo [holocron] Uygulama kapandi.
pause
goto :eof

:health_check
set "PORT=8765"
rem Yonlendirme tek satirlik if icinde erken cozuluyor; dal ayri satirda.
if not exist "%PORTFILE%" goto have_port
set /p PORT=<"%PORTFILE%"

:have_port
call :probe_health %PORT%
if not errorlevel 1 goto started
goto start_failed

:started
echo [holocron] Calisiyor: http://127.0.0.1:%PORT%/
goto :eof

:start_failed
echo.
echo [holocron] Uygulama acilmadi. Son log:
echo ------------------------------------------------------------
if not exist "%LOGFILE%" set "LOGFILE=%LOCALAPPDATA%\Holocron\holocron.log"
if exist "%LOGFILE%" goto show_log
echo [holocron] Log dosyasi bulunamadi: %~dp0holocron.log
echo [holocron] Ayrinti icin: holocron.bat --console
goto failed_end

:show_log
powershell -NoProfile -Command "Get-Content -Tail 40 -LiteralPath '%LOGFILE%'" 2>nul
if not errorlevel 1 goto failed_end
type "%LOGFILE%"
goto failed_end

:failed_end
echo ------------------------------------------------------------
echo [holocron] Tam hatayi gormek icin: holocron.bat --console
pause
exit /b 1

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

rem --- alt yordam: requirements.txt ozeti --------------------------------
rem Donus: REQ_HASH (SHA256). certutil her Windows'ta var; yoksa PowerShell.
:req_hash
set "REQ_HASH="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "requirements.txt" SHA256 2^>nul') do if not defined REQ_HASH set "REQ_HASH=%%H"
if defined REQ_HASH set "REQ_HASH=%REQ_HASH: =%"
if defined REQ_HASH exit /b 0
for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'requirements.txt').Hash" 2^>nul`) do set "REQ_HASH=%%H"
exit /b 0

rem --- alt yordam: yanimizdaki tekerlek klasorleri -----------------------
rem Donus: PIP_LINKS. Bos ise yerel tekerlek yok, kurulum agdan gider.
:find_links
set "PIP_LINKS="
if exist "wheels\*" set "PIP_LINKS=--find-links wheels"
if defined PIP_LINKS set "PIP_LINKS=--no-index %PIP_LINKS%"
exit /b 0

rem --- alt yordam: bagimliliklari requirements.txt ile esitle ------------
rem Cagri: call :sync_deps <python.exe> <ozet dosyasi>.
rem Eski surumun uzerine yeni paket acildiginda pip yalnizca .venv ILK
rem yaratilirken kosuyordu, yeni bagimlilik hic
rem kurulmuyordu. Artik her acilista requirements.txt'in ozeti kayitli
rem ozetle karsilastirilir. Kurulum dusse bile uygulama ACILIR: eksik paket
rem yalnizca ilgili ozelligi kapatir.
:sync_deps
set "SYNC_PY=%~1"
set "SYNC_SHA=%~2"
call :req_hash
if not defined REQ_HASH exit /b 0
set "OLD_HASH="
if exist "%SYNC_SHA%" set /p OLD_HASH=<"%SYNC_SHA%"
if /i "%OLD_HASH%"=="%REQ_HASH%" exit /b 0
rem Embed dagitiminda pip yoktur: paketler pakete gomulu gelir, is yok.
"%SYNC_PY%" -m pip --version >nul 2>&1
if errorlevel 1 exit /b 0
echo [holocron] Bagimliliklar guncelleniyor...
call :find_links
if not defined PIP_LINKS goto sync_online
"%SYNC_PY%" -m pip install %PIP_LINKS% -r requirements.txt
if not errorlevel 1 goto sync_ok
echo [holocron] Yerel tekerlekler yetmedi, agdan deneniyor...
:sync_online
"%SYNC_PY%" -m pip install -r requirements.txt
if not errorlevel 1 goto sync_ok
echo [holocron] UYARI: bagimliliklar guncellenemedi. Uygulama aciliyor;
echo [holocron] eksik paket yalnizca ilgili ozelligi kapatir.
exit /b 0

:sync_ok
> "%SYNC_SHA%" echo %REQ_HASH%
exit /b 0

rem --- alt yordam: /api/health yoklamasi ---------------------------------
rem Cagri: call :probe_health <port>. Donus: errorlevel 0 ise ayakta.
:probe_health
set "HEALTH=http://127.0.0.1:%~1/api/health"
curl.exe -s -o nul --max-time 5 "%HEALTH%" >nul 2>&1
if not errorlevel 1 exit /b 0
rem Windows 10 1803 oncesinde curl.exe yok; PowerShell yedegi.
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 '%HEALTH%' } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 exit /b 0
exit /b 1
