@echo off
setlocal
set "LOGGER_ROOT=%~dp0..\.."
set "LOGGER_EXIT=1"
for %%A in (%*) do if /I "%%~A"=="--no-pause" set "RADIO_LOGGER_NO_PAUSE=1"
pushd "%LOGGER_ROOT%"
if errorlevel 1 goto failed_directory
if /I "%~1"=="setup" goto setup
if not exist ".venv\Scripts\python.exe" goto missing_environment
".venv\Scripts\python.exe" "%~dp0launcher.py" %*
set "LOGGER_EXIT=%ERRORLEVEL%"
goto done

:setup
if exist ".venv\Scripts\python.exe" goto local_python
py -3.12 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto py_launcher
py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto py_latest
python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1
if not errorlevel 1 goto path_python
echo Python 3.12 or newer was not found.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo Tick "Add python.exe to PATH" and the Python launcher option.
echo Close this window, then run Setup Windows.cmd again.
goto done

:local_python
".venv\Scripts\python.exe" "%~dp0launcher.py" %*
set "LOGGER_EXIT=%ERRORLEVEL%"
goto done

:py_launcher
py -3.12 "%~dp0launcher.py" %*
set "LOGGER_EXIT=%ERRORLEVEL%"
goto done

:path_python
python "%~dp0launcher.py" %*
set "LOGGER_EXIT=%ERRORLEVEL%"
goto done

:py_latest
py -3 "%~dp0launcher.py" %*
set "LOGGER_EXIT=%ERRORLEVEL%"
goto done

:missing_environment
echo The local Python environment is missing. Run Setup Windows.cmd first.
goto done

:failed_directory
echo Cannot open the project folder. Extract the full project to a local folder first.
if not "%RADIO_LOGGER_NO_PAUSE%"=="1" pause
exit /b 1

:done
popd
if not "%RADIO_LOGGER_NO_PAUSE%"=="1" pause
exit /b %LOGGER_EXIT%
