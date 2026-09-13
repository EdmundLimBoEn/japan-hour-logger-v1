@echo off
call "%~dp0deploy\windows\launcher.cmd" backup %*
exit /b %ERRORLEVEL%
