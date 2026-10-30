@echo off
title CivicSync v1.0 - ONE NODE
cd /d "%~dp0"
echo.
echo   CIVICSYNC v1.0 - ONE NODE, TWO DOORS
echo   Admin Console : http://localhost:8000
echo   Civil App     : http://localhost:8000/app
echo   Public link   : double-click start_tunnel.bat (keep open)
echo.
python -X utf8 server.py
pause
