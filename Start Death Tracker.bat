@echo off
cd /d "%~dp0"

REM Prefer pyw / pythonw so no black console window stays open
where pyw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pyw -3 death_tracker.py
  exit /b 0
)

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw death_tracker.py
  exit /b 0
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" py -3 death_tracker.py
  exit /b 0
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" python death_tracker.py
  exit /b 0
)

echo.
echo  Python not found.
echo  Install Python 3 from https://www.python.org/downloads/
echo  and check "Add python.exe to PATH".
echo.
echo  Then run:  py -3 -m pip install -r requirements.txt
echo.
pause
exit /b 1
