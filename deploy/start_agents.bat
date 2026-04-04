@echo off
REM Ally X — startup launcher for always-on agents
REM Scheduled via Task Scheduler to run at logon.
REM Uses pythonw.exe so no console window appears.

set PYTHON=C:\Users\jesse\anaconda3\pythonw.exe
set ROOT=C:\Users\jesse\allyx-agents

REM discord_reminders (claudebot) — always-on bot
start "" "%PYTHON%" "%ROOT%\agents\discord_reminders\agent.py"

REM Add more always-on agents here as needed, e.g.:
REM start "" "%PYTHON%" "%ROOT%\agents\some_other_agent\agent.py"
