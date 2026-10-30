@echo off
title CivicSync - Public Tunnel
echo ============================================
echo   CivicSync v1.0 - Public tunnel starter
echo   Keeps this window OPEN. Your internet-wide
echo   URL prints in a couple of seconds.
echo ============================================
echo.
echo Node must already be running (run.bat).
echo.
npx -y tunnelmole 8000
pause
