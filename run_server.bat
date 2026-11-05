@echo off
chcp 65001 >nul
title He Thong Tra Cuu Mac Thep & Gang Noi Bo - Cong Ty Duc
cd /d "%~dp0"

if not exist "kts_database.db" (
    echo [1/2] Dang khoi tao Co so du lieu tu Key to Steel v2005.11...
    python build_db.py
)

echo.
echo ============================================================================
echo   HE THONG TRA CUU MAC THEP VA GANG NOI BO - CONG TY DUC
echo   - Truy cap tren may nay: http://localhost:8055
echo   - Chia se qua mang LAN cong ty: http://%COMPUTERNAME%:8055
echo ============================================================================
echo.

start "" "http://localhost:8055"
python -m uvicorn app:app --host 0.0.0.0 --port 8055
pause

