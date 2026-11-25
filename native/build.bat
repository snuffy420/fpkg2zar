@echo off
rem Builds fpkg2zar\kraken.dll with the MSVC Build Tools (x64).
setlocal
set "HERE=%~dp0"
set "VCVARS="
for %%V in (18 2022 2019) do (
  for %%E in (BuildTools Community Professional Enterprise) do (
    if not defined VCVARS if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%ProgramFiles(x86)%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat"
    if not defined VCVARS if exist "%ProgramFiles%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat" set "VCVARS=%ProgramFiles%\Microsoft Visual Studio\%%V\%%E\VC\Auxiliary\Build\vcvars64.bat"
  )
)
if not defined VCVARS (
  echo Could not find vcvars64.bat. Install "Visual Studio Build Tools" with the C++ workload.
  exit /b 1
)
call "%VCVARS%" >nul || exit /b 1
pushd "%HERE%"
cl /nologo /O2 /W3 /LD /MT kraken.c /Fe:..\fpkg2zar\kraken.dll /link /NOLOGO || (popd & exit /b 1)
del /q kraken.obj ..\fpkg2zar\kraken.exp ..\fpkg2zar\kraken.lib 2>nul
popd
echo Built fpkg2zar\kraken.dll
