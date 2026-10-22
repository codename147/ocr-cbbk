@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Cai dat phan mem Tra cuu ho so
echo ==========================================================
echo    CAI DAT PHAN MEM TRA CUU HO SO
echo ==========================================================
echo.

rem Python phu hop: ban THUONG (khong phai ban "free-threaded" 3.13t/3.14t), co tkinter
set "CHK=import sys,sysconfig,tkinter;assert sys.version_info>=(3,9);sys.exit(1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0)"

rem ---- 1. Tim Python ----
set "PYCMD="
where py >nul 2>nul
if errorlevel 1 goto trypython
for %%V in (3.12 3.13 3.11 3.10 3.14) do (
  if not defined PYCMD (
    py -%%V -c "%CHK%" >nul 2>nul && set "PYCMD=py -%%V"
  )
)
if defined PYCMD goto havepy

:trypython
python -c "%CHK%" >nul 2>nul && set "PYCMD=python"
if defined PYCMD goto havepy
for %%D in (Python312 Python313 Python311) do (
  if not defined PYCMD (
    if exist "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" (
      "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" -c "%CHK%" >nul 2>nul && set PYCMD="%LOCALAPPDATA%\Programs\Python\%%D\python.exe"
    )
  )
)
if defined PYCMD goto havepy

echo [1/4] Chua co Python phu hop. Dang cai Python 3.12 (can Internet)...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set PYCMD="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  goto havepy
)
echo.
echo KHONG CAI DUOC PYTHON TU DONG.
echo Hay tai Python 3.12 tai https://www.python.org/downloads/ (ban thuong, KHONG chon "free-threaded"),
echo khi cai nho tick "Add python.exe to PATH", roi chay lai file CAI_DAT.bat nay.
pause
exit /b 1

:havepy
echo [1/4] Python: %PYCMD%

rem ---- 2. Moi truong rieng + thu vien ----
rem Kiem tra venv cu qua file pyvenv.cfg (khong chay python.exe cua venv de tranh hop thoai loi)
if not exist ".venv\Scripts\python.exe" goto mkvenv
if not exist ".venv\pyvenv.cfg" goto badvenv
set "VHOME="
for /f "usebackq tokens=1,* delims== " %%A in (".venv\pyvenv.cfg") do if /i "%%A"=="home" set "VHOME=%%B"
if not defined VHOME goto badvenv
if not exist "%VHOME%\python.exe" goto badvenv
".venv\Scripts\python.exe" -c "%CHK%" >nul 2>nul
if errorlevel 1 goto badvenv
echo [2/4] Da co moi truong Python rieng.
goto instlibs

:badvenv
echo [2/4] Moi truong cu tao bang Python da go hoac khong phu hop - xoa va tao lai...
rmdir /s /q ".venv"

:mkvenv
echo [2/4] Tao moi truong Python rieng cho phan mem...
%PYCMD% -m venv .venv
if errorlevel 1 ( echo Loi tao moi truong Python. & pause & exit /b 1 )

:instlibs
echo [3/4] Cai thu vien PyMuPDF, openpyxl...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install --only-binary=pymupdf -r requirements.txt -q
if errorlevel 1 (
  echo.
  echo LOI CAI THU VIEN. Nguyen nhan thuong gap:
  echo  - Mat mang hoac mang cong ty chan pypi.org
  echo  - Phien ban Python qua moi, chua co ban cai san cua PyMuPDF
  echo Hay chup man hinh nay gui lai de duoc ho tro.
  pause
  exit /b 1
)

rem ---- 3. Tesseract OCR ----
echo [4/4] Kiem tra Tesseract-OCR (doc PDF scan)...
".venv\Scripts\python.exe" setup_helper.py check
if not errorlevel 2 goto shortcut
echo.
choice /C YN /M "Chua co Tesseract-OCR. Cai tu dong bang winget"
if errorlevel 2 goto notess
winget install -e --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
".venv\Scripts\python.exe" setup_helper.py check
if not errorlevel 2 goto shortcut
:notess
echo.
echo Chua co Tesseract: phan mem van chay, nhung PDF scan se chua tim duoc.
echo Tai ban cai tai: https://github.com/UB-Mannheim/tesseract/wiki
echo (Du lieu tieng Viet da kem san trong thu muc tessdata cua phan mem.)

:shortcut
".venv\Scripts\python.exe" setup_helper.py shortcut
echo.
echo ==========================================================
echo  XONG. Mo phan mem bang loi tat "Tra cuu ho so" ngoai Desktop
echo  hoac chay file CHAY_PHAN_MEM.bat
echo ==========================================================
pause
