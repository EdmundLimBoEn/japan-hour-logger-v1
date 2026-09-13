@echo off
call "%~dp0deploy\windows\launcher.cmd" setup %*
exit /b %ERRORLEVEL%
