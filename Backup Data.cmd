@echo off
setlocal DisableDelayedExpansion
"%~dp0deploy\windows\launcher.cmd" backup %*
