@echo off
setlocal

set "INSTALL_DIR=%LOCALAPPDATA%\Programs\rclone-tray"
set "DATA_DIR=%LOCALAPPDATA%\rclone-tray"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DESKTOP=%USERPROFILE%\Desktop"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not exist "%PYW%" (
    echo Could not find pythonw.exe under %%LOCALAPPDATA%%\Programs\Python\.
    echo Install Python 3.11+ from python.org first.
    exit /b 1
)

echo === Installing Python dependencies ===
python -m pip install --user --upgrade pystray pillow psutil
if errorlevel 1 goto :err

echo.
echo === Copying program files to %INSTALL_DIR% ===
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%~dp0rclone_tray.pyw" "%INSTALL_DIR%\" >nul
if errorlevel 1 goto :err
if exist "%~dp0LICENSE"  copy /Y "%~dp0LICENSE"  "%INSTALL_DIR%\" >nul
if exist "%~dp0README.md" copy /Y "%~dp0README.md" "%INSTALL_DIR%\" >nul
if exist "%~dp0FSD.md"    copy /Y "%~dp0FSD.md"    "%INSTALL_DIR%\" >nul
if exist "%~dp0uninstall.bat" copy /Y "%~dp0uninstall.bat" "%INSTALL_DIR%\" >nul

echo.
echo === Ensuring data directory %DATA_DIR% ===
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

echo.
echo === Creating shortcuts ===
set "TARGET=%INSTALL_DIR%\rclone_tray.pyw"

powershell -NoProfile -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $s.CreateShortcut('%STARTUP%\Rclone Tray.lnk');" ^
  "$lnk.TargetPath = '%PYW%';" ^
  "$lnk.Arguments = '\"%TARGET%\"';" ^
  "$lnk.WorkingDirectory = '%INSTALL_DIR%';" ^
  "$lnk.WindowStyle = 7;" ^
  "$lnk.Save()"
if errorlevel 1 goto :err

powershell -NoProfile -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $s.CreateShortcut('%DESKTOP%\Rclone Tray.lnk');" ^
  "$lnk.TargetPath = '%PYW%';" ^
  "$lnk.Arguments = '\"%TARGET%\"';" ^
  "$lnk.WorkingDirectory = '%INSTALL_DIR%';" ^
  "$lnk.WindowStyle = 7;" ^
  "$lnk.Save()"
if errorlevel 1 goto :err

echo.
echo Installed.
echo   Program : %INSTALL_DIR%
echo   Data    : %DATA_DIR%
echo.
echo Launch "Rclone Tray" from the Desktop. The app will autostart at next login.
exit /b 0

:err
echo.
echo Install failed.
exit /b 1
