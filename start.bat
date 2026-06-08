@echo off
setlocal

echo Starting Cloud IT POS...
echo.

:: Check if virtual environment exists
if not exist venv\Scripts\activate.bat (
    echo [ERROR] Virtual environment not found.
    echo Please run install.bat first.
    pause
    exit /b 1
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Run the application
python run.py

endlocal