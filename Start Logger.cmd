@echo off
call "%~dp0deploy\windows\launcher.cmd" start %*
exit /b %ERRORLEVEL%
