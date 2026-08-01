@echo off
cd /d "%~dp0.."
python tests\test_scheduler.py
echo.
echo 日志已保存至: tests\TestLog\
echo 按任意键继续...
pause > nul
