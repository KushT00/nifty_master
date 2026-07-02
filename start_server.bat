@echo off
echo Configuring Proxy for OpenAlgo Server (1081)...
echo routing via 127.0.0.1:1081 (AWS Mumbai Static IP)
echo.

:: Set environment variables
set HTTP_PROXY=socks5h://127.0.0.1:1081
set HTTPS_PROXY=socks5h://127.0.0.1:1081
set NO_PROXY=localhost,127.0.0.1

:: Start the server
uv run app.py

pause

