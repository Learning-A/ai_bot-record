# -*- coding: utf-8 -*-
"""点歌适配器（jukebox_bilibili）—— 独立进程，平台相关部分仅限 B 站

工作方式（纯事件总线客户端，不改动核心任何代码）：
1. 监听总线广播的弹幕（{"type":"danmaku"}）
2. 识别「点歌 歌名」（也支持 主播监控台按钮 发来的 cmd）
3. 用 B 站视频搜索接口（WBI 签名）搜歌，取第一个时长合理的音乐视频
4. 广播鱼的演唱动作 + 气泡，然后在本机浏览器打开视频页
   （声音走系统音频 → OBS 桌面音频采集，与你手动播放完全一致）

点歌规则：弹幕/cmd 文本以「点歌」开头；全局冷却 SONG_COOLDOWN 秒；
时长超过 MAX_DURATION 的结果跳过；搜不到时在弹幕流提示。

B 模式（点歌台页面在线时自动启用）：jukebox.html 通过 relay 心跳宣告存在，
本适配器便不再开浏览器标签，而是广播 {"type":"song",...} 由点歌台内嵌播放器播放；
点歌台页面关闭后心跳超时，自动退回 A 模式（开浏览器标签）。

用法：
  python jukebox_bilibili.py                # 正常模式
  python jukebox_bilibili.py --smoke        # 冒烟模式：只搜索和广播，不真开浏览器
  python jukebox_bilibili.py --hub-port 18643  # 连其他端口的总线（隔离测试用）
"""
import argparse, base64, hashlib, json, os, re, socket, struct, threading, time, urllib.parse, urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
WBI_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]

SONG_PREFIX = "点歌"
SONG_COOLDOWN = 30.0     # 两首点歌之间的全局冷却（秒）
MAX_DURATION = 600       # 结果时长上限（秒），过滤长视频/合集
SONG_ACTIONS = ["悠闲哼歌", "可爱宅舞", "轻快摇摆舞", "小提琴演奏", "吹笛子"]  # 演唱/演奏动作池


def load_credentials():
    p = os.path.expandvars(r"%APPDATA%/com.rsplwe.bili-live-hime/app-config.json")
    cfg = json.load(open(p, encoding="utf-8"))
    cks = {c["name"]: c["value"] for c in cfg.get("cookies", [])}
    cookie = "; ".join(f"{k}={v}" for k, v in cks.items())
    return cookie, cfg.get("img_url", ""), cfg.get("sub_url", "")


def wbi_sign(params, img_key, sub_key):
    raw = img_key + sub_key
    mixin = "".join(raw[i] for i in WBI_TAB if i < len(raw))[:32]
    q = urllib.parse.urlencode(sorted(params.items()))
    return q + "&w_rid=" + hashlib.md5((q + mixin).encode()).hexdigest()


def clean_title(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()


def parse_duration(s):
    try:
        parts = [int(x) for x in str(s).split(":")]
        sec = 0
        for x in parts:
            sec = sec * 60 + x
        return sec
    except Exception:
        return 10 ** 9  # 解析失败当作超长，直接过滤


def search_song(keyword, cookie, img_key, sub_key):
    """返回 (bvid, title, author, duration秒) 或 None"""
    params = {"search_type": "video", "keyword": keyword, "page": 1,
              "wts": str(int(time.time()))}
    url = ("https://api.bilibili.com/x/web-interface/wbi/search/type?"
           + wbi_sign(params, img_key, sub_key))
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cookie": cookie,
        "Origin": "https://www.bilibili.com", "Referer": "https://www.bilibili.com/"})
    data = json.load(urllib.request.urlopen(req, timeout=15))
    if data.get("code") != 0:
        raise RuntimeError("搜索接口返回 %s %s" % (data.get("code"), data.get("message")))
    results = (data.get("data") or {}).get("result") or []
    # 优先：标题包含关键词；其次按时长兜底
    def rank(r):
        title = clean_title(r.get("title"))
        dur = parse_duration(r.get("duration"))
        if dur > MAX_DURATION:
            return None
        has_kw = keyword.lower() in title.lower()
        return (0 if has_kw else 1, abs(dur - 240))  # 关键词命中优先，时长接近4分钟优先
    best, best_key = None, None
    for r in results:
        k = rank(r)
        if k is not None and (best_key is None or k < best_key):
            best, best_key = r, k
    if not best:
        return None
    return best.get("bvid"), clean_title(best.get("title")), best.get("author"), parse_duration(best.get("duration"))


# ---------------------------------------------------------------- 总线客户端（与 danmaku_bilibili 同款）
class HubLink:
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

    def recv_msg(self):
        def need(k):
            while len(self.buf) < k:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("closed")
                self.buf += chunk
            out, self.buf = self.buf[:k], self.buf[k:]
            return out
        while True:
            b1, b2 = need(2)
            op = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", need(8))[0]
            if b2 & 0x80:
                mask = need(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(need(n)))
            else:
                payload = need(n)
            if op == 0x1:
                return json.loads(payload.decode())


def run(args):
    cookie, img, sub = load_credentials()
    # 重连不重置：冷却计时 + 同人同歌去重
    state = {"jukebox_page_ts": 0.0, "last_song_ts": 0.0, "seen": {}}   # seen: {(user, song): ts}
    print("[点歌] 凭据已加载" + ("（冒烟模式：不会真的打开浏览器）" if args.smoke else ""), flush=True)
    while True:
        link = HubLink(("127.0.0.1", args.hub_port))
        try:
            link.connect()
            print(f"[点歌] 已接入事件总线 :{args.hub_port}，等待「{SONG_PREFIX} 歌名」…", flush=True)
            while True:
                msg = link.recv_msg()
                mtype = msg.get("type")
                if mtype == "jukebox_hello":      # 核心转发 relay 时已解包，收到的是原始消息
                    if time.time() - state["jukebox_page_ts"] > 25:
                        print("[点歌] 点歌台页面在线 → B 模式（不再开浏览器标签）", flush=True)
                    state["jukebox_page_ts"] = time.time()
                    continue
                if mtype != "danmaku":
                    continue
                text = str(msg.get("text", "")).strip()
                m = re.match("^" + SONG_PREFIX + r"\s*(.+)$", text)
                if not m:
                    continue
                song, user = m.group(1).strip()[:40], msg.get("user", "匿名")
                now = time.time()
                # 同人同歌 60 秒内不重复入队（防手滑连点堆积队列）
                dup_key = (user, song)
                if dup_key in state["seen"] and now - state["seen"][dup_key] < 60:
                    link.send_safe({"type": "relay", "data": {"type": "danmaku", "user": user, "text": text,
                                                        "action": "刚点过这首，在队列里啦", "state": ""}})
                    continue
                state["seen"][dup_key] = now
                if now - state["last_song_ts"] < SONG_COOLDOWN:
                    link.send_safe({"type": "relay", "data": {"type": "danmaku", "user": user, "text": text,
                                                        "action": "点歌冷却中", "state": "冷却中"}})
                    continue
                try:
                    hit = search_song(song, cookie, img, sub)
                except Exception as e:
                    print("[点歌] 搜索失败:", e, flush=True)
                    hit = None
                if not hit:
                    link.send_safe({"type": "relay", "data": {"type": "danmaku", "user": user, "text": text,
                                                            "action": "没找到这首", "state": ""}})
                    continue
                if len(state["seen"]) > 500:
                    state["seen"] = {k: v for k, v in state["seen"].items() if now - v < 120}
                bvid, title, author, dur = hit
                state["last_song_ts"] = now
                url = "https://www.bilibili.com/video/" + bvid
                print(f"[点歌] {user}「{song}」→ {title}（{author}，{dur//60}:{dur%60:02d}）{url}", flush=True)
                link.send_safe({"type": "relay", "data": {"type": "danmaku", "user": user, "text": text,
                                                        "action": "♪ " + title, "state": "已触发"}})
                import random
                link.send_safe({"type": "relay", "data": {"type": "action",
                                                        "name": random.choice(SONG_ACTIONS),
                                                        "text": "♪ 即将播放：" + title, "user": user}})
                link.send_safe({"type": "relay", "data": {"type": "song", "bvid": bvid,
                                                        "title": title, "user": user,
                                                        "duration": dur, "url": url}})
                page_online = time.time() - state["jukebox_page_ts"] < 70
                if args.smoke:
                    print(f"[点歌][SMOKE] song事件已广播；{'点歌台在线(B模式)' if page_online else '点歌台离线，将开浏览器(A模式)'}: {url}", flush=True)
                elif page_online:
                    print(f"[点歌] 点歌台在线，交给页面播放: {url}", flush=True)
                else:
                    import webbrowser
                    webbrowser.open(url)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print("[点歌] 总线断开:", e, "，5 秒重连", flush=True)
        time.sleep(5)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="B站点歌适配器 → 浏览器播放")
    ap.add_argument("--smoke", action="store_true", help="冒烟模式：不真开浏览器")
    ap.add_argument("--hub-port", type=int, default=8643)
    args = ap.parse_args()
    run(args)
