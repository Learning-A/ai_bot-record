# -*- coding: utf-8 -*-
"""大肥鱼系统组件健康检查（运维脚本/检查.bat 的底层实现）
检查：互动核心端口/页面、B站弹幕适配器进程与连接、OBS进程、控制台可达性。
退出码 = 异常组件数量（0 = 全部健康）。"""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def avatar_dir():
    for cand in (os.path.join(HERE, "..", "直播形象"), os.path.join(HERE, "..", "avatar")):
        if os.path.exists(os.path.join(cand, "danmaku_hub.py")):
            return os.path.normpath(cand)
    return None


def ps_count(pattern):
    """用 PowerShell CIM 统计命令行含 pattern 的 python 进程数"""
    cmd = ("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
           "Where-Object {$_.CommandLine -match '%s'} | Measure-Object).Count" % pattern)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=20)
        return int(r.stdout.decode("utf-8", "ignore").strip() or 0)
    except Exception:
        return -1


def port_open(port):
    s = socket.socket()
    s.settimeout(2)
    ok = s.connect_ex(("127.0.0.1", port)) == 0
    s.close()
    return ok


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def http_ok(url):
    try:
        _NO_PROXY_OPENER.open(url, timeout=3)
        return True
    except Exception:
        return False


def hub_status():
    """连事件总线读状态（弹幕源在线/房间/人气）"""
    try:
        s = socket.create_connection(("127.0.0.1", 8643), timeout=4)
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall(("GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError()
            buf += chunk
        buf = buf.split(b"\r\n\r\n", 1)[1]
        s.settimeout(4)
        deadline = time.time() + 6
        while time.time() < deadline:
            d = s.recv(65536)
            if not d:
                break
            buf += d
            while len(buf) >= 2:
                n = buf[1] & 0x7F
                off = 2
                if n == 126:
                    n = struct.unpack(">H", buf[2:4])[0]; off = 4
                if len(buf) < off + n:
                    break
                st = json.loads(buf[off:off + n])
                if st.get("type") == "status":
                    s.close()
                    return st
                buf = buf[off + n:]
        s.close()
    except Exception:
        pass
    return None


def obs_running():
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq obs64.exe"],
                           capture_output=True, timeout=15)
        return b"obs64.exe" in r.stdout
    except Exception:
        return False


def main():
    bad = 0
    print("=" * 46)
    print(" 大肥鱼直播系统 · 组件健康检查")
    print("=" * 46)

    av = avatar_dir()
    if not av:
        print("[!!] 找不到直播形象目录（直播形象/ 或 avatar/）")
        return 1
    print("[OK] 形象目录 ............ %s" % av)

    print()
    print("--- 互动核心 (danmaku_hub) ---")
    if port_open(8642) and port_open(8643):
        print("[OK] 端口 8642/8643 ...... 监听中")
    else:
        print("[XX] 端口 8642/8643 ...... 未监听  → 双击 重启核心.bat")
        bad += 1
    if http_ok("http://127.0.0.1:8642/console.html"):
        print("[OK] 控制台页面 .......... HTTP 200")
    else:
        print("[XX] 控制台页面 .......... 不可达  → 重启核心.bat")
        bad += 1

    print()
    print("--- B站弹幕适配器 (danmaku_bilibili) ---")
    n = ps_count("danmaku_bilibili")
    if n == 1:
        print("[OK] 适配器进程 .......... 1 个（正常）")
    elif n > 1:
        print("[!!] 适配器进程 .......... %d 个（重复，建议 重启适配器.bat）" % n)
        bad += 1
    else:
        print("[XX] 适配器进程 .......... 未运行  → 双击 重启适配器.bat")
        bad += 1

    st = hub_status()
    if st:
        if st.get("danmaku"):
            print("[OK] B站弹幕连接 ......... 在线（房间 %s，人气 %s）" % (st.get("roomid"), st.get("popularity")))
        else:
            print("[XX] B站弹幕连接 ......... 离线（核心在线但适配器未连上B站）")
            print("     常见原因：凭据失效（重新登录 Bili Live Hime）或网络波动 → 重启适配器.bat")
            bad += 1
    else:
        print("[XX] 事件总线 ............. 无响应  → 重启核心.bat")
        bad += 1

    print()
    print("--- 点歌适配器 (jukebox_bilibili) ---")
    n = ps_count("jukebox_bilibili")
    if n == 1:
        print("[OK] 点歌适配器 .......... 1 个（正常）")
    elif n > 1:
        print("[!!] 点歌适配器 .......... %d 个（重复，建议 重启点歌适配器.bat）" % n)
        bad += 1
    else:
        print("[XX] 点歌适配器 .......... 未运行（点歌不可用，弹幕互动不受影响）")
        print("     → 双击 重启点歌适配器.bat")
        bad += 1
    log = os.path.normpath(os.path.join(HERE, "..", "_jukebox.log"))
    if os.path.exists(log):
        try:
            lines = open(log, "rb").read().decode("utf-8", "ignore").strip().splitlines()
            print("     日志末行: %s" % (lines[-1][:60] if lines else ""))
        except Exception:
            pass

    print()
    print("--- 推流软件 (OBS) ---")
    if obs_running():
        print("[OK] OBS .................. 运行中")
    else:
        print("[--] OBS .................. 未运行（不直播时属正常）")

    print()
    print("=" * 46)
    if bad == 0:
        print(" 结论：全部健康 ✓")
    else:
        print(" 结论：%d 项异常，按上面箭头提示处理" % bad)
    print("=" * 46)
    return bad


if __name__ == "__main__":
    sys.exit(main())
