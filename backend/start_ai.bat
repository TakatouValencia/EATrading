@echo off
cd /d "%~dp0"
chcp 65001 >nul
title NOVAire EA - Institutional SMC AI Trading Engine
color 0A

echo =========================================================
echo   NOVAire EA - Institutional SMC Trading Engine v2.0
echo =========================================================
echo.

echo [1/2] Mengaktifkan Virtual Environment...
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
    echo [OK] Virtual Environment Aktif.
) else (
    echo [INFO] Menggunakan Python bawaan sistem.
)

echo.
echo [2/2] Menjalankan Diagnostik Cepat Sistem...
python diagnostic_tool.py
echo.

:loop
echo.
echo =========================================================
echo   [ %date% %time% ] ENGINE AKTIF & MEMANTAU PASAR LIVE
echo =========================================================
python main.py

echo.
echo [!] AI Engine terhenti atau koneksi terputus.
echo [!] Mencoba menyambung kembali dalam 5 detik...
timeout /t 5
goto loop
