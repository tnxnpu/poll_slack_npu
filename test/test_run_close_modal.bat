@echo off
cd /d E:\TINHNX\PyProjects\Poll_Slack_NPU
start "Test close modal" cmd /k "cd /d E:\TINHNX\PyProjects\Poll_Slack_NPU\test && python -m uvicorn test_close_modal:app --reload --port 8002"
timeout /t 3 > nul
start "ngrok Tunnel" cmd /k "ngrok http 8002"
