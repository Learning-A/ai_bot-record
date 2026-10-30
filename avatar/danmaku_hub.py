# -*- coding: utf-8 -*-
"""大肥鱼互动核心（danmaku_hub）—— 平台无关的事件总线

本模块不知道任何直播平台/直播软件的存在。它只做三件事：
1. :8642 HTTP 静态服务（直播形象页 / 监控台 / 动画素材）
2. :8643 WebSocket 总线 —— 任何客户端可接入：
   - 弹幕适配器 上报 {"type":"danmaku","user","text"}（自动去重）
   - 监控台按钮   上报 {"type":"cmd","text"}（主播手动，免冷却）
   - 适配器状态   上报 {"type":"source_status","online":bool,"popularity":N,"roomid":X}
3. 关键词引擎：弹幕文本 → 动作名，广播 {"type":"action","name","text","user"}

对接其他直播平台：写一个独立适配器进程（参考 danmaku_bilibili.py），
把弹幕事件发到 ws://127.0.0.1:8643 即可，本核心零修改。
"""
import base64, hashlib, json, os, queue, random, socket, struct, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
HTTP_PORT, HUB_PORT = 8642, 8643
COOLDOWN_SEC = 5.0

ANIMS = ["三球抛接","下五子棋","东张西望","中秋赏月吃月饼","优雅女仆舞","余额-分文不剩","余额-数金皱眉","余额-袋空如洗","余额-金袋叮当","余额-钱袋如常","余额-钱袋满溢","偷吃零食被抓住","写代码","写福字","凭空生花","动物环绕","原地专心玩魔方","原地小憩沉眠","原地左转奔跑","原地敲击桌面互动","原地漂浮踏步","原地跳跃抓碎头顶物品","原地蹲下玩玩具汽车","原地重力下蹲压缩","变鸽子","可爱宅舞","吃Token","吃冰淇淋融化","吃午餐","吃大闸蟹","吃年糕","吃早餐","吃晚餐","吃汤圆","吃白饭","吃粽子","吃糖葫芦","吃腊八粥","吃西瓜","吃重阳糕","吃长寿面","吃青团","吃饺子","吹气球","吹笛子","哈欠连天","堆雪人","大口吃零食","女仆屈膝礼仪","小幅度原地360度旋转展示","小提琴演奏","工作状态-原地踱步张望","工作状态-垂头叹气冒汗","工作状态-忙碌点按","工作状态-思考冒泡","工作状态-清点归档","工作状态-雀跃庆祝","待机呼吸休闲","悠闲哼歌","扑克魔术","打瞌睡被惊醒","抽陀螺","拆礼物","插茱萸赏菊","摇扇纳凉","撸猫","收红包","放孔明灯","放河灯","放烟花","放风筝","整体换装试色","是啊，吃什么","晨间刷牙","涮火锅","深度思考碎碎念","点击回应-傲娇生气","点击回应-元气挥手","点击回应-害羞惊讶","点击回应-开心跃动","点击回应-挠痒咯咯笑","照镜子","玩水枪","玩游戏气急败坏","用鲸鱼尾巴拍打地面","碎碎念-发呆碎碎念","碎碎念-对屏碎碎念","碎碎念-擦桌碎碎念","穿针乞巧","舞狮头","荡秋千","萌化小幽灵","蓝鲸现世","蝴蝶蜜蜂环绕头顶开花","螃蟹走路","被吓一跳","被落叶淹没","被鼠标拖拽悬空反馈","装点圣诞树","讨糖南瓜灯","超大伸懒腰","踢毽子","轻快摇摆舞","轻快记录","骑木马","鲸鱼吐泡泡特效"]
ANIM_SET = set(ANIMS)

ALIASES = {
    "吃饭": ["吃白饭", "吃大闸蟹", "涮火锅", "吃早餐", "吃晚餐", "吃西瓜", "吃饺子", "吃汤圆", "吃粽子", "吃糖葫芦", "大口吃零食"],
    "干饭": ["吃白饭", "涮火锅", "吃大闸蟹"],
    "白饭": ["吃白饭"],
    "跳舞": ["可爱宅舞", "轻快摇摆舞", "优雅女仆舞"],
    "舞": ["可爱宅舞", "轻快摇摆舞", "优雅女仆舞"],
    "烟花": ["放烟花"],
    "放烟花": ["放烟花"],
    "睡觉": ["原地小憩沉眠"],
    "晚安": ["原地小憩沉眠"],
    "困了": ["原地小憩沉眠"],
    "打瞌睡": ["打瞌睡被惊醒"],
    "写代码": ["写代码", "工作状态-忙碌点按"],
    "敲代码": ["写代码"],
    "上班": ["写代码", "工作状态-清点归档"],
    "干活": ["写代码", "工作状态-忙碌点按"],
    "摸鱼": ["东张西望", "碎碎念-发呆碎碎念", "待机呼吸休闲"],
    "休息": ["超大伸懒腰", "东张西望"],
    "伸懒腰": ["超大伸懒腰"],
    "加油": ["工作状态-雀跃庆祝"],
    "打气": ["工作状态-雀跃庆祝"],
    "庆祝": ["工作状态-雀跃庆祝"],
    "唱歌": ["悠闲哼歌", "小提琴演奏", "吹笛子"],
    "哼歌": ["悠闲哼歌"],
    "小提琴": ["小提琴演奏"],
    "撸猫": ["撸猫"],
    "喵": ["撸猫"],
    "挥手": ["点击回应-元气挥手"],
    "你好": ["点击回应-元气挥手"],
    "hi": ["点击回应-元气挥手"],
    "打招呼": ["点击回应-元气挥手"],
    "惊吓": ["被吓一跳"],
    "吓一跳": ["被吓一跳"],
    "魔术": ["扑克魔术", "变鸽子"],
    "变鸽子": ["变鸽子"],
    "秋千": ["荡秋千"],
    "荡秋千": ["荡秋千"],
    "堆雪人": ["堆雪人"],
    "过年": ["收红包", "放烟花", "舞狮头"],
    "红包": ["收红包"],
    "舞狮": ["舞狮头"],
    "抛球": ["三球抛接"],
    "魔方": ["原地专心玩魔方"],
    "五子棋": ["下五子棋"],
    "下棋": ["下五子棋"],
    "放风筝": ["放风筝"],
    "刷牙": ["晨间刷牙"],
    "鲸鱼": ["蓝鲸现世"],
    "泡泡": ["鲸鱼吐泡泡特效"],
}

STRIP_CHARS = " \t\r\n!！?？。.,，、~～;；:："


# ---------------------------------------------------------------- WebSocket 工具
def ws_accept_key(key):
    guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    return base64.b64encode(hashlib.sha1((key + guid).encode()).digest()).decode()


def make_text_frame(payload: str) -> bytes:
    """服务端→客户端文本帧（无 mask）"""
    data = payload.encode()
    head = bytearray([0x81])
    n = len(data)
    if n < 126:
        head.append(n)
    elif n < 65536:
        head.append(126); head += struct.pack(">H", n)
    else:
        head.append(127); head += struct.pack(">Q", n)
    return bytes(head) + data


class HubClient:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def read_msg(self):
        while True:
            b1, b2 = self._need(2)
            op = b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._need(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._need(8))[0]
            if b2 & 0x80:
                mask = self._need(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._need(n)))
            else:
                payload = self._need(n)
            if op == 0x8:
                raise ConnectionError("client close")
            if op == 0x9:
                self.sock.sendall(bytes([0x8A, len(payload)]) + payload)
                continue
            if op == 0x1:
                return json.loads(payload.decode())


# ---------------------------------------------------------------- 本地总线
hub_lock = threading.Lock()
hub_clients = []
source_state = {"online": False, "popularity": 0, "roomid": None}
ENGINE_Q = queue.Queue()
DUP_LOCK = threading.Lock()
SEEN = {}


def broadcast(obj):
    data = make_text_frame(json.dumps(obj, ensure_ascii=False))
    with hub_lock:
        dead = []
        for c in hub_clients:
            try:
                c.sock.sendall(data)
            except Exception:
                dead.append(c)
        for c in dead:
            hub_clients.remove(c)


def hub_handle(conn):
    client = None
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(4096)
            if not chunk:
                return
            req += chunk
        key = ""
        for line in req.decode("utf-8", "ignore").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {ws_accept_key(key)}\r\n\r\n"
        ).encode())
        client = HubClient(conn)
        with hub_lock:
            hub_clients.append(client)
        broadcast({"type": "status", "danmaku": source_state["online"],
                   "popularity": source_state["popularity"], "roomid": source_state["roomid"]})
        skip = False
        while True:
            msg = client.read_msg()
            t = msg.get("type")
            if t == "cmd":                       # 监控台按钮（主播，免冷却）
                ENGINE_Q.put({"user": "主播", "text": str(msg.get("text", ""))[:40], "manual": True})
            elif t == "danmaku":                 # 弹幕适配器上报（3 秒去重）
                k = (msg.get("user", ""), msg.get("text", ""))
                now = time.time()
                with DUP_LOCK:
                    if k in SEEN and now - SEEN[k] < 3:
                        skip = True
                    else:
                        SEEN[k] = now
                if skip:
                    skip = False
                    continue
                ENGINE_Q.put({"user": msg.get("user", "匿名"),
                              "text": str(msg.get("text", ""))[:60], "manual": False})
            elif t == "source_status":           # 适配器状态 → 转播给所有页面
                source_state["online"] = bool(msg.get("online"))
                if "popularity" in msg:
                    source_state["popularity"] = msg.get("popularity", 0)
                if msg.get("roomid"):
                    source_state["roomid"] = msg.get("roomid")
                broadcast({"type": "status", "danmaku": source_state["online"],
                           "popularity": source_state["popularity"],
                           "roomid": source_state["roomid"]})
    except Exception:
        pass
    finally:
        with hub_lock:
            try:
                if client in hub_clients:
                    hub_clients.remove(client)
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def hub_server():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", HUB_PORT))
    srv.listen(8)
    print(f"[hub] ws://127.0.0.1:{HUB_PORT} 事件总线就绪（平台无关）", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=hub_handle, args=(conn,), daemon=True).start()


# ---------------------------------------------------------------- 关键词引擎
def match_action(text):
    t = text.strip(STRIP_CHARS).lower()
    if not t:
        return None
    if t in ANIM_SET:
        return t
    if t in ALIASES:
        return random.choice(ALIASES[t])
    return None


def engine():
    last_trigger_ts = 0.0
    while True:
        ev = ENGINE_Q.get()
        user, text, manual = ev["user"], ev["text"], ev["manual"]
        action = match_action(text)
        now = time.time()
        if action:
            if not manual and now - last_trigger_ts < COOLDOWN_SEC:
                broadcast({"type": "danmaku", "user": user, "text": text,
                           "action": action, "state": "冷却中"})
                continue
            last_trigger_ts = now
            print(f"[触发] {user} 「{text}」→ {action}", flush=True)
            broadcast({"type": "danmaku", "user": user, "text": text,
                       "action": action, "state": "已触发"})
            broadcast({"type": "action", "name": action, "text": text, "user": user})
        else:
            broadcast({"type": "danmaku", "user": user, "text": text,
                       "action": "", "state": ""})


# ---------------------------------------------------------------- HTTP 静态服务
def http_server():
    os.chdir(ROOT)
    Handler = SimpleHTTPRequestHandler
    Handler.log_message = lambda *a, **k: None
    srv = ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), Handler)
    print(f"[http] http://127.0.0.1:{HTTP_PORT} 静态服务就绪", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    print(f"[核心] 关键词引擎启动 | 冷却 {COOLDOWN_SEC}s | 动作 {len(ANIMS)} 个 | 别名 {len(ALIASES)} 组", flush=True)
    threading.Thread(target=http_server, daemon=True).start()
    threading.Thread(target=hub_server, daemon=True).start()
    engine()
