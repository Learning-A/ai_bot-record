@echo off
C:\Windows\System32\OpenSSH\ssh.exe -N -L 9881:127.0.0.1:9880 -o ProxyCommand="F:/apps/Nmap/ncat.exe --proxy-type socks5 --proxy <代理地址:端口> %%h %%p" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -o ServerAliveCountMax=5 -o ExitOnForwardFailure=yes <用户名>@<GPU服务器IP>
