@echo off
call "%~dp0deploy\windows\launcher.cmd" check %*
exit /b %ERRORLEVEL%
