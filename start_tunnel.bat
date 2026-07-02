@echo off
echo Starting SOCKS5 Tunnel to AWS Mumbai (13.206.135.201)...
echo routing via 127.0.0.1:1081 (AWS Mumbai)
echo.

:: Set proxy environment variables for this session
set HTTP_PROXY=socks5h://127.0.0.1:1081
set HTTPS_PROXY=socks5h://127.0.0.1:1081
set NO_PROXY=localhost,127.0.0.1

:: Path to your PEM key
set KEY="c:\Users\Kush Tejani\Downloads\openalgo_v2.1\openalgo\openalgo-aws.pem"

:start_tunnel
echo [%time%] Attempting to connect to AWS Mumbai...

:: Start the tunnel with Keep-Alive heartbeats
:: ServerAliveInterval=60 sends a ping every minute to keep the connection active
ssh -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -i %KEY% -D 1081 -N ubuntu@13.206.135.201

echo.
echo [%time%] Tunnel disconnected. Retrying in 5 seconds...
timeout /t 5 >nul
goto start_tunnel

