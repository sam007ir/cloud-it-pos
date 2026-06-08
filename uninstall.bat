@echo off
setlocal

echo ================================================
echo   Cloud IT POS - Uninstaller
echo ================================================
echo.

echo This will remove the virtual environment.
echo Your database and configuration will NOT be deleted.
echo.

set /p confirm="Are you sure you want to uninstall? (Y/N): "
if /i not "%confirm%"=="Y" (
    echo Uninstallation cancelled.
    pause
    exit /b 0
)

:: Remove virtual environment
if exist venv (
    echo Removing virtual environment...
    rmdir /s /q venv
    echo Virtual environment removed.
) else (
    echo No virtual environment found.
)

:: Remove desktop shortcut if it exists
if exist "%USERPROFILE%\Desktop\Cloud IT POS.lnk" (
    echo Removing desktop shortcut...
    del "%USERPROFILE%\Desktop\Cloud IT POS.lnk" /f /q
    echo Desktop shortcut removed.
)

echo.
echo Uninstallation complete.
echo.
echo Note: Your data is still safe in the 'instance' folder.
pause
endlocal