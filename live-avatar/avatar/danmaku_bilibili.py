# -*- coding: utf-8 -*-
"""B站弹幕适配器（danmaku_bilibili）—— 独立进程，与核心解耦

职责单一：把 B 站直播间的弹幕事件送进核心事件总线（ws://127.0.0.1:8643），
核心的关键词引擎负责"弹幕→动作"。换其他直播平台时，照本文件写一个新适配器即可，
核心与形象页零修改。

连接方式（2026-10 实测，B站弹幕服务有 TLS 指纹风控，走 TCP 老协议）：
  裸 TCP 端口 2243 + WBI 签名的 getDanmuInfo token + protover 3 (brotli)

凭据来源（按优先级）：
  1. 命令行参数 --roomid 123 --cookies cookies.json（JSON: {"SESSDATA":"..","bili_jct":"..",...}）
  2. 自动发现：Bili Live Hime 的配置文件 %APPDATA%/com.rsplwe.bili-live-hime/app-config.json
     （该应用登录后自动提供 cookie / 房间号 / WBI key；不用它也可走参数方式）

用法：python danmaku_bilibili.py [--roomid 123] [--cookies 文件] [--host 127.0.0.1 --port 8643]
"""
import argparse, base64, hashlib, json, os, socket, ssl, struct, threading, time, urllib.parse, urllib.request, zlib

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import brotlicffi as brotli
    PROTOVER = 3
except ImportError:
    brotli = None
    PROTOVER = 2  # 无 brotli 时降级 zlib

HUB_DEFAULT = ("127.0.0.1", 8643)
DANMAKU_HOST = "broadcastlv.chat.bilibili.com"
DANMAKU_PORTS = (2243, 2244, 2245)  # 2243 实测首选
WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# 禁代理 opener：无视 HTTP_PROXY 环境变量（本机 Clash 开关不应影响B站API直连）
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ================================================================ 核心总线客户端（裸 socket WebSocket）
class HubLink:
    """连核心事件总线，自动重连。"""

    def __init__(self, addr):
        self.addr = addr
        self.sock = None
        self.buf = b""
        self.lock = threading.Lock()

    def connect(self):
        host, port = self.addr
        self.sock = socket.create_connection((host, port), timeout=8)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("hub handshake no reply")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise ConnectionError("hub upgrade failed")
        self.sock.settimeout(None)  # 连接建立后取消超时：空闲不再误判断线
        self.buf = resp.split(b"\r\n\r\n", 1)[1]
        print("[hub] 已接入核心事件总线", flush=True)

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", n)
        else:
            head.append(0x80 | 127); head += struct.pack(">Q", n)
        head += mask
        with self.lock:
            self.sock.sendall(bytes(head) + masked)

    def send_safe(self, obj):
        try:
            self.send(obj)
            return True
        except Exception:
            return False


# ================================================================ 凭据
def load_credentials(args):
    """返回 (uid, cookie_str, roomid, img_key, sub_key)。优先命令行，其次自动发现。"""
    if args.cookies:
        cks = json.load(open(args.cookies, encoding="utf-8"))
        cookie = "; ".join(f"{k}={v}" for k, v in cks.items())
        uid = int(cks.get("DedeUserID", 0))
        roomid = args.roomid or int(cks.get("roomid", 0))
        return uid, cookie, roomid, None, None
    # 自动发现：Bili Live Hime 配置
    p = os.path.expandvars(r"%APPDATA%/com.rsplwe.bili-live-hime/app-config.json")
    try:
        cfg = json.load(open(p, encoding="utf-8"))
        cks = {c["name"]: c["value"] for c in cfg.get("cookies", [])}
        cookie = "; ".join(f"{k}={v}" for k, v in cks.items())
        uid = int(cks.get("DedeUserID", cfg.get("uid", 0)))
        roomid = args.roomid or int(cfg.get("roomId", 0))
        return uid, cookie, roomid, cfg.get("img_url", ""), cfg.get("sub_url", "")
    except Exception as e:
        print("[凭据] 自动发现失败（未装/未登录 Bili Live Hime？）:", e, flush=True)
        return 0, "", args.roomid or 0, "", ""


def get_danmu_info(cookie, roomid, img_key, sub_key):
    """WBI 签名调 getDanmuInfo → (token, host)。"""
    params = {"id": str(roomid), "type": "0", "wts": str(int(time.time()))}
    token = ""
    host = DANMAKU_HOST
    if img_key and sub_key:
        raw = img_key + sub_key
        mixin = "".join(raw[i] for i in WBI_TAB if i < len(raw))[:32]
        q = urllib.parse.urlencode(sorted(params.items()))
        w_rid = hashlib.md5((q + mixin).encode()).hexdigest()
        url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?" + q + "&w_rid=" + w_rid
    else:
        url = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cookie": cookie,
        "Origin": "https://live.bilibili.com", "Referer": "https://live.bilibili.com/"})
    data = json.load(_NO_PROXY_OPENER.open(req, timeout=15))
    if data.get("code") == 0:
        d = data["data"]
        token = d.get("token", "")
        host = (d.get("host_list") or [{}])[0].get("host", DANMAKU_HOST)
    else:
        print("[B站] getDanmuInfo 返回:", data.get("code"), data.get("message"), flush=True)
    return token, host


# ================================================================ B站弹幕 TCP 客户端
def decompress(ver, body):
    if ver == 3 and brotli:
        return brotli.decompress(body)
    return zlib.decompress(body)


def bili_pkt(op, body=b"", seq=1):
    return struct.pack(">IHHII", 16 + len(body), 16, 1, op, seq) + body


def bili_parse(data):
    out, consumed = [], 0
    while len(data) >= 16:
        total, head, ver, op, seq = struct.unpack(">IHHII", data[:16])
        if total < 16 or total > len(data):
            break
        body = data[head:total]
        data = data[total:]
        consumed += total
        if op == 5 and ver in (2, 3):
            try:
                inner, _ = bili_parse(decompress(ver, body))
                out += inner
                continue
            except Exception:
                pass
        out.append((op, ver, body))
    return out, consumed


def run_bili(link, args):
    backoff = 10
    while True:
        sock = None
        try:
            uid, cookie, roomid, img, sub = load_credentials(args)
            if not roomid:
                raise RuntimeError("缺少房间号（--roomid 或登录 Bili Live Hime）")
            token, host = get_danmu_info(cookie, roomid, img, sub)
            if not token:
                raise RuntimeError("未取到弹幕 token（cookie 失效？）")

            for port in DANMAKU_PORTS:
                try:
                    sock = socket.create_connection((host, port), timeout=8)
                    break
                except OSError:
                    continue
            if sock is None:
                raise ConnectionError("所有端口连接失败: " + host)
            sock.settimeout(60)

            auth = json.dumps({"uid": uid, "roomid": roomid, "protover": PROTOVER,
                               "platform": "web", "type": 2, "key": token}).encode()
            sock.sendall(bili_pkt(7, auth))
            hb_stop = threading.Event()

            def heartbeat():
                while not hb_stop.is_set():
                    try:
                        sock.sendall(bili_pkt(2, b"[object Object]"))
                    except OSError:
                        return
                    hb_stop.wait(30)

            threading.Thread(target=heartbeat, daemon=True).start()
            buf = b""
            authed = False
            link_fail = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionError("closed" + ("" if authed else "（认证前）"))
                buf += chunk
                packets, consumed = bili_parse(buf)
                if consumed:
                    buf = buf[consumed:]
                for op, ver, body in packets:
                    if op == 8:
                        j = json.loads(body.decode("utf-8", "ignore"))
                        if j.get("code") == 0:
                            authed = True
                            backoff = 10
                            print(f"[B站] 已连接房间 {roomid}（{host}:{port}）", flush=True)
                            link.send_safe({"type": "source_status", "online": True, "roomid": roomid})
                        else:
                            print("[B站] 认证被拒:", j, flush=True)
                            raise RuntimeError("auth rejected")
                    elif op == 3:
                        try:
                            pop = struct.unpack(">I", body[:4])[0]
                            link.send_safe({"type": "source_status", "online": True,
                                            "popularity": pop, "roomid": roomid})
                        except Exception:
                            pass
                    elif op == 5:
                        try:
                            cmd = json.loads(body.decode("utf-8", "ignore"))
                        except Exception:
                            continue
                        cmdname = str(cmd.get("cmd", ""))
                        if cmdname.startswith("DANMU_MSG"):
                            info = cmd.get("info") or []
                            if len(info) > 2 and info[1]:
                                user = str((info[2] or ["", "匿名"])[1] or "匿名")
                                if link.send_safe({"type": "danmaku", "user": user,
                                                   "text": str(info[1])[:60]}):
                                    link_fail = 0
                                else:
                                    link_fail += 1
                                    if link_fail > 10:
                                        raise RuntimeError("核心总线链路失效")
                        elif cmdname == "INTERACT_WORD":
                            d = cmd.get("data") or {}
                            if d.get("msg_type") == 1 and d.get("uname"):
                                link.send_safe({"type": "enter", "user": str(d["uname"])[:30]})
                        elif cmdname == "ENTRY_EFFECT":
                            d = cmd.get("data") or {}
                            if d.get("uname"):
                                link.send_safe({"type": "enter", "user": str(d["uname"])[:30], "vip": True})
            hb_stop.set()
        except Exception as e:
            msg = str(e)
            cred_err = any(k in msg for k in ("缺少房间号", "未取到弹幕 token", "auth rejected", "认证被拒"))
            if cred_err:
                backoff = 15      # 凭据问题（未登录 Hime/cookie 失效）快速重试，
                                  # 登录后 15s 内自动接上；网络问题才指数退避
            print(f"[B站] 断开（{e}），{backoff}s 后重试", flush=True)
            link.send_safe({"type": "source_status", "online": False})
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        time.sleep(backoff)
        backoff = min(backoff * 2, 600)


def main():
    ap = argparse.ArgumentParser(description="B站弹幕适配器 → 大肥鱼核心总线")
    ap.add_argument("--roomid", type=int, default=0, help="直播间号（默认自动发现）")
    ap.add_argument("--cookies", help="cookie JSON 文件（默认自动发现 Bili Live Hime）")
    ap.add_argument("--host", default=HUB_DEFAULT[0], help="核心总线地址")
    ap.add_argument("--port", type=int, default=HUB_DEFAULT[1], help="核心总线端口")
    args = ap.parse_args()

    while True:  # 总线断了也自愈
        link = HubLink((args.host, args.port))
        try:
            link.connect()
            run_bili(link, args)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print("[hub] 连接失败:", e, "，5s 重试", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
