@echo off
cd /d E:\TINHNX\PyProjects\Poll_Slack_NPU
start "Uvicorn Server" cmd /k "cd /d E:\TINHNX\PyProjects\Poll_Slack_NPU && python -m uvicorn main:app --reload --port 8000"
timeout /t 3 > nul
start "ngrok Tunnel" cmd /k "ngrok http --url=wallaby-allowing-quickly.ngrok-free.app 8000"
