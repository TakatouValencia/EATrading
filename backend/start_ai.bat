@echo off
title SMC Trading AI Engine

echo Mengaktifkan Virtual Environment...
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
) else (
    echo Peringatan: Virtual environment tidak ditemukan. Menggunakan Python bawaan sistem.
)

:loop
echo.
echo =========================================
echo [ %time% ] Memulai SMC AI Analisis...
echo =========================================
python main.py

echo.
echo [!] AI Crash atau Berhenti! 
echo [!] Me-restart otomatis dalam 5 detik...
timeout /t 5
goto loop
