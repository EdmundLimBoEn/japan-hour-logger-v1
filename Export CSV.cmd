@echo off
call "%~dp0deploy\windows\launcher.cmd" export %*
exit /b %ERRORLEVEL%
