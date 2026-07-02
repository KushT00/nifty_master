@echo off
echo Configuring Proxy for SEBI Static IP Compliance...
echo routing via 127.0.0.1:1080 (AWS Mumbai)
echo.

:: Set proxy environment variables for this session
set HTTP_PROXY=socks5h://127.0.0.1:1080
set HTTPS_PROXY=socks5h://127.0.0.1:1080

:: Verify the IP before starting (optional but safe)
echo Verifying your current external IP...
curl.exe -s https://api.ipify.org
echo.
echo.

:: Start the Strategy
echo Starting Nifty Weekly Master...
uv run nifty_weekly_master.py

pause
