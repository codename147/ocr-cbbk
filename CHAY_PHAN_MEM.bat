@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto needsetup
if not exist ".venv\pyvenv.cfg" goto needsetup
set "VHOME="
for /f "usebackq tokens=1,* delims== " %%A in (".venv\pyvenv.cfg") do if /i "%%A"=="home" set "VHOME=%%B"
if not defined VHOME goto needsetup
if not exist "%VHOME%\pythonw.exe" goto needsetup
start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
exit /b 0

:needsetup
echo Moi truong Python chua cai hoac da hong (Python cu bi go). Dang cai dat lai...
call "%~dp0CAI_DAT.bat"
