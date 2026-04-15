@echo off
setlocal

set "INSTALL_DIR=%LOCALAPPDATA%\Programs\rclone-tray"
set "DATA_DIR=%LOCALAPPDATA%\rclone-tray"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "DESKTOP=%USERPROFILE%\Desktop"

echo Stopping any running tray instance...
taskkill /F /IM pythonw.exe >nul 2>&1

echo Removing shortcuts...
if exist "%DESKTOP%\Rclone Tray.lnk" del "%DESKTOP%\Rclone Tray.lnk"
if exist "%STARTUP%\Rclone Tray.lnk" del "%STARTUP%\Rclone Tray.lnk"

echo Removing program directory %INSTALL_DIR%...
if exist "%INSTALL_DIR%" rmdir /S /Q "%INSTALL_DIR%"

echo.
echo Program removed.
echo Data directory kept: %DATA_DIR%
echo Delete it manually if you also want to remove your mount config.
exit /b 0
