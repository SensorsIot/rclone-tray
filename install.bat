@echo off
setlocal

echo === Installing Python dependencies ===
python -m pip install --user --upgrade pystray pillow psutil
if errorlevel 1 goto :err

echo.
echo === Creating Start Menu / Startup shortcut ===
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%~dp0rclone_tray.pyw"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if not exist "%PYW%" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"

powershell -NoProfile -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $s.CreateShortcut('%STARTUP%\Rclone Tray.lnk');" ^
  "$lnk.TargetPath = '%PYW%';" ^
  "$lnk.Arguments = '\"%TARGET%\"';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.WindowStyle = 7;" ^
  "$lnk.Save()"
if errorlevel 1 goto :err

echo.
echo === Creating Desktop shortcut ===
powershell -NoProfile -Command ^
  "$s = New-Object -ComObject WScript.Shell;" ^
  "$desk = [Environment]::GetFolderPath('Desktop');" ^
  "$lnk = $s.CreateShortcut(\"$desk\Rclone Tray.lnk\");" ^
  "$lnk.TargetPath = '%PYW%';" ^
  "$lnk.Arguments = '\"%TARGET%\"';" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.WindowStyle = 7;" ^
  "$lnk.Save()"
if errorlevel 1 goto :err

echo.
echo Done. Launch "Rclone Tray" from the desktop, then right-click the
echo tray icon and toggle "Autostart + watchdog" for the mounts you want
echo at Windows login.
exit /b 0

:err
echo.
echo Install failed.
exit /b 1
