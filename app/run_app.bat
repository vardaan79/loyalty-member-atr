@echo off
rem Loyalty Card Member ATR - interactive web app launcher
rem Dashboard: http://127.0.0.1:5015   (Snowflake build runs on demand)
cd /d "E:\ANAND PRAKASH\AUTOMATION\loyalty_member_atr_project\app"
start "" http://127.0.0.1:5015
python server.py
pause
