@echo off
cd /d E:\TINHNX\PyProjects\Simple
start "Uvicorn Server" cmd /k "cd /d E:\TINHNX\PyProjects\Simple && python -m uvicorn main:app --reload --port 8000"
timeout /t 3 > nul
start "ngrok Tunnel" cmd /k "ngrok http 8000"
