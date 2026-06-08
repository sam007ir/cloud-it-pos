@echo off
setlocal

echo ================================================
echo   Cloud IT POS - Installation Script
echo ================================================
echo.

:: Check if Python is installed
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not found in PATH.
    echo.
    echo Please install Python 3.10 or higher from:
    echo https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo [1/4] Python found. Checking version...
python --version

:: Create virtual environment
echo.
echo [2/4] Creating virtual environment...
if exist venv (
    echo Virtual environment already exists. Skipping creation.
) else (
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate virtual environment and install requirements
echo.
echo [3/4] Installing required packages...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install dependencies.
    echo Please check your internet connection and try again.
    pause
    exit /b 1
)

:: Create instance directory if it doesn't exist
if not exist instance (
    mkdir instance
)

echo.
echo [4/4] Installation completed successfully!
echo.

:: Create desktop shortcut
echo Creating desktop shortcut...
set "shortcutPath=%USERPROFILE%\Desktop\Cloud IT POS.lnk"
set "targetPath=%CD%\start.bat"
set "iconPath=%CD%\app\static\icon.ico"

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%shortcutPath%'); $s.TargetPath = '%targetPath%'; $s.WorkingDirectory = '%CD%'; $s.IconLocation = '%targetPath%'; $s.Save()" >nul 2>&1

if exist "%USERPROFILE%\Desktop\Cloud IT POS.lnk" (
    echo Desktop shortcut created successfully.
) else (
    echo Could not create desktop shortcut automatically.
    echo You can manually create one pointing to start.bat
)

echo.
echo ================================================
echo   Installation Complete!
echo ================================================
echo.
echo   To start the application:
echo   - Double-click "start.bat", OR
echo   - Use the desktop shortcut "Cloud IT POS"
echo.
echo   Then open your browser and go to:
echo   http://127.0.0.1:5000
echo.
echo   First time? Go through the Setup Wizard at /setup
echo ================================================
echo.

pause
endlocal