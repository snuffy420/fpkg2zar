@echo off
rem Builds the portable dist\fPKG2Zar-<version>-windows.exe (single file, no Python needed).
setlocal
cd /d "%~dp0"
if not exist "fpkg2zar\kraken.dll" call native\build.bat || exit /b 1
for /f "delims=" %%v in ('py -3 -c "import fpkg2zar; print(fpkg2zar.__version__)"') do set "VER=%%v"
py -3 -m pip install --user pyinstaller PyQt6 zstandard || exit /b 1
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed --name "fPKG2Zar-%VER%-windows" ^
  --icon "fpkg2zar\assets\icon.ico" ^
  --add-binary "fpkg2zar\kraken.dll;fpkg2zar" ^
  --add-data "fpkg2zar\assets;fpkg2zar\assets" ^
  --collect-submodules zstandard ^
  --exclude-module tkinter --exclude-module numpy --exclude-module pyqtgraph ^
  run_gui.py || exit /b 1
echo.
echo Built dist\fPKG2Zar-%VER%-windows.exe
