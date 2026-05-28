@echo off
start "Server" cmd /k python server.py
timeout /t 2 /nobreak >nul
start "Client" cmd /k python Client.py
exit