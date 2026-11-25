@echo off
rem fPKG2Zar launcher: checks dependencies, then opens the GUI.
rem Drag .pkg files onto this .bat to queue them.
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  exit /b 1
)

%PY% -c "import PyQt6.QtWidgets, zstandard" >nul 2>nul
if errorlevel 1 (
  echo Installing required Python packages: PyQt6, zstandard ...
  %PY% -m pip install --user -r requirements.txt
  if errorlevel 1 (
    echo Package installation failed.
    pause
    exit /b 1
  )
)

if not exist "fpkg2zar\kraken.dll" (
  echo fpkg2zar\kraken.dll is missing, building it with MSVC...
  call native\build.bat
  if errorlevel 1 (
    echo Could not build kraken.dll. Install "Visual Studio Build Tools" with the C++ workload, or download a release that includes it.
    pause
    exit /b 1
  )
)

rem Use the windowed interpreter so no console stays open behind the GUI.
set "PYW="
if "%PY%"=="py -3" (
  where pyw >nul 2>nul && set "PYW=pyw -3"
) else (
  where pythonw >nul 2>nul && set "PYW=pythonw"
)
if defined PYW (
  start "" %PYW% -m fpkg2zar.gui %*
) else (
  %PY% -m fpkg2zar.gui %*
)
