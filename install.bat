@echo off
setlocal

set "INSTALL_DIR=%LOCALAPPDATA%\Programs\rclone-tray"
set "DATA_DIR=%LOCALAPPDATA%\rclone-tray"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%D"
if not exist "%DESKTOP%" set "DESKTOP=%USERPROFILE%\Desktop"
if not exist "%DESKTOP%" mkdir "%DESKTOP%"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not exist "%PYW%" (
    echo Could not find pythonw.exe under %%LOCALAPPDATA%%\Programs\Python\.
    echo Install Python 3.11+ from python.org first.
    exit /b 1
)

echo === Installing prerequisites (WinFsp, rclone) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-prereqs.ps1"
if errorlevel 1 goto :err

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
echo === Creating Desktop shortcut ===
set "TARGET=%INSTALL_DIR%\rclone_tray.pyw"

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
echo === Removing legacy Startup-folder shortcut (if present) ===
if exist "%STARTUP%\Rclone Tray.lnk" del "%STARTUP%\Rclone Tray.lnk"

echo.
echo === Registering Scheduled Task "RcloneTray" (trigger: At log on) ===
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$user = \"$env:USERDOMAIN\$env:USERNAME\";" ^
  "$action = New-ScheduledTaskAction -Execute '%PYW%' -Argument '\"%TARGET%\"' -WorkingDirectory '%INSTALL_DIR%';" ^
  "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user;" ^
  "$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited;" ^
  "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable;" ^
  "Register-ScheduledTask -TaskName 'RcloneTray' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null"
if errorlevel 1 goto :err

echo.
echo === Verifying install ===
set "VERIFY_FAIL="
if not exist "%INSTALL_DIR%\rclone_tray.pyw" (
    echo VERIFY: tray program missing at %INSTALL_DIR%\rclone_tray.pyw
    set "VERIFY_FAIL=1"
)
where rclone >nul 2>&1
if errorlevel 1 if not exist "%LOCALAPPDATA%\Programs\rclone\rclone.exe" (
    echo VERIFY: rclone.exe not found on PATH or in %%LOCALAPPDATA%%\Programs\rclone
    set "VERIFY_FAIL=1"
)
powershell -NoProfile -Command "if (-not (Get-Service -Name 'WinFsp.Launcher' -ErrorAction SilentlyContinue)) { exit 1 }"
if errorlevel 1 (
    echo VERIFY: WinFsp.Launcher service not found
    set "VERIFY_FAIL=1"
)
schtasks /query /tn RcloneTray >nul 2>&1
if errorlevel 1 (
    echo VERIFY: Scheduled Task 'RcloneTray' missing
    set "VERIFY_FAIL=1"
)
if not exist "%APPDATA%\rclone\rclone.conf" (
    echo VERIFY: rclone.conf missing at %APPDATA%\rclone\rclone.conf
    set "VERIFY_FAIL=1"
)
if defined VERIFY_FAIL goto :err
echo All checks passed.

echo.
echo === Launching Rclone Tray ===
start "" "%PYW%" "%INSTALL_DIR%\rclone_tray.pyw"

echo.
echo Installed.
echo   Program        : %INSTALL_DIR%
echo   Data           : %DATA_DIR%
echo   Autostart task : RcloneTray  (Task Scheduler -> Task Scheduler Library)
echo.
echo The tray icon is starting now. On first run it will open the
echo Manage Mounts window so you can add your remotes.
echo At next login it will autostart via the Scheduled Task.
exit /b 0

:err
echo.
echo Install failed.
exit /b 1
