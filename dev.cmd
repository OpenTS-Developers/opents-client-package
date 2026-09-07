@echo off
setlocal
echo Preparing OpenTS development client...
rem Avoid PowerShell 7 module paths in Windows PowerShell.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
set "dev_exit_code=%errorlevel%"
rem Keep double-clicked failures visible.
if not "%dev_exit_code%"=="0" if "%~1"=="" pause
exit /b %dev_exit_code%
