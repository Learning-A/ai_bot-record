# -*- coding: utf-8 -*-
"""OBS 一键配置脚本（便携版，可在任何电脑运行）

自动完成：
1. 读取/启用 OBS 的 obs-websocket 接口（密码自动发现，无需手填）
2. 在当前场景中创建：显示器采集（垫底）+ 大肥鱼直播形象（右下角置顶）
3. 幂等：重复运行不会重复创建

用法：先启动 OBS，再运行  python setup_obs.py
"""
import base64, hashlib, json, os, socket, struct, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
FISH_PAGE = os.path.join(os.path.dirname(HERE), "avatar", "index.html").replace("\\", "/")
OBS_CFG = os.path.expandvars(r"%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json")

WS_CLIENT = r'''
import socket as _s, os as _os

def _connect(host, port):
    global s, recv_buf
    s = _s.create_connection((host, port), timeout=8)
    key = _os.urandom(16).hex()
    s.sendall(("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (host, port, key)).encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            raise ConnectionError("handshake no reply")
        resp += chunk
    if b"101" not in resp.split(b"\r\n")[0]:
        raise ConnectionError("upgrade failed")
    recv_buf = resp.split(b"\r\n\r\n", 1)[1]

def _send_text(payload):
    data = payload.encode()
    mask = _os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    head = bytearray([0x81])
    n = len(data)
    if n < 126: head.append(0x80 | n)
    elif n < 65536: head.append(0x80 | 126); head += struct.pack(">H", n)
    else: head.append(0x80 | 127); head += struct.pack(">Q", n)
    head += mask
    s.sendall(bytes(head) + masked)

def _recv_msg():
    global recv_buf
    def need(n):
        global recv_buf
        while len(recv_buf) < n:
            chunk = s.recv(65536)
            if not chunk: raise ConnectionError("closed")
            recv_buf += chunk
        out, recv_buf = recv_buf[:n], recv_buf[n:]
        return out
    while True:
        b1, b2 = need(2)
        op = b1 & 0x0F
        n = b2 & 0x7F
        if n == 126: n = struct.unpack(">H", need(2))[0]
        elif n == 127: n = struct.unpack(">Q", need(8))[0]
        if b2 & 0x80:
            mask = need(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(need(n)))
        else:
            payload = need(n)
        if op == 0x8: raise ConnectionError("closed by OBS")
        if op == 0x1: return json.loads(payload.decode())

def _sha(s):
    return base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()
'''


def read_ws_config():
    """读 OBS websocket 配置；未启用则顺手启用。"""
    if not os.path.exists(OBS_CFG):
        return None, 4455, False
    cfg = json.load(open(OBS_CFG, encoding="utf-8"))
    enabled = bool(cfg.get("server_enabled"))
    if not enabled:
        cfg["server_enabled"] = True
        json.dump(cfg, open(OBS_CFG, "w", encoding="utf-8"), indent=2)
    return cfg.get("server_password", ""), int(cfg.get("server_port", 4455)), enabled


def main():
    password, port, was_enabled = read_ws_config()
    if not was_enabled:
        print(">> 检测到 OBS 接口未启用，已自动开启。请【重启 OBS】后重新运行本脚本")
        return 1
    if password is None:
        print(">> 未找到 OBS 配置（OBS 从未启动过？）。请先启动一次 OBS 再运行本脚本")
        return 1

    ns = {"json": json, "struct": struct, "base64": base64,
          "hashlib": hashlib, "os": os, "socket": socket}
    exec(WS_CLIENT, ns)
    try:
        ns["_connect"]("127.0.0.1", port)
    except (ConnectionRefusedError, OSError):
        print(">> 连不上 OBS 接口（端口 %d）：请先启动 OBS 再运行本脚本" % port)
        return 1

    hello = ns["_recv_msg"]()
    secret = ns["_sha"](password + hello["d"]["authentication"]["salt"])
    auth = ns["_sha"](secret + hello["d"]["authentication"]["challenge"])
    ns["_send_text"](json.dumps({"op": 1, "d": {"rpcVersion": 1, "authentication": auth,
                                                "eventSubscriptions": 0}}))
    ident = ns["_recv_msg"]()
    if ident.get("op") != 2:
        print(">> 认证失败，请重启 OBS 后重试（配置文件密码可能已轮换）")
        return 1

    mid = [0]
    def req(t, data=None):
        mid[0] += 1
        rid = str(mid[0])
        d = {"requestType": t, "requestId": rid}
        if data is not None:
            d["requestData"] = data
        ns["_send_text"](json.dumps({"op": 6, "d": d}))
        while True:
            m = ns["_recv_msg"]()
            if m.get("op") == 7 and m["d"]["requestId"] == rid:
                if not m["d"]["requestStatus"]["result"]:
                    raise RuntimeError(f"{t}: {m['d']['requestStatus'].get('comment')}")
                return m["d"].get("responseData") or {}

    scene = req("GetSceneList")["currentProgramSceneName"]
    items = req("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    print(f">> 当前场景[{scene}] 现有: " + ", ".join(i["sourceName"] for i in items))

    if not any(i["sourceName"] == "显示器采集" for i in items):
        req("CreateInput", {"sceneName": scene, "inputName": "显示器采集",
                            "inputKind": "monitor_capture",
                            "inputSettings": {"monitor": 0, "show_cursor": True},
                            "sceneItemEnabled": True})
        print(">> + 显示器采集")

    if not any(i["sourceName"] == "大肥鱼直播形象" for i in items):
        req("CreateInput", {"sceneName": scene, "inputName": "大肥鱼直播形象",
                            "inputKind": "browser_source",
                            "inputSettings": {"is_local_file": True,
                                              "local_file": FISH_PAGE,
                                              "width": 640, "height": 360,
                                              "css": "body { background-color: rgba(0,0,0,0); margin: 0px; overflow: hidden; }",
                                              "reroute_audio": False, "shutdown": False,
                                              "visible": True},
                            "sceneItemEnabled": True})
        print(f">> + 大肥鱼直播形象 ({FISH_PAGE})")

    items = req("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    fish = next(i for i in items if i["sourceName"] == "大肥鱼直播形象")
    disp = next(i for i in items if i["sourceName"] == "显示器采集")
    tr = fish["sceneItemTransform"]
    if int(tr.get("positionX", 0)) == 0 and int(tr.get("positionY", 0)) == 0:
        req("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": fish["sceneItemId"],
                                      "sceneItemTransform": {"positionX": 1200, "positionY": 660,
                                                             "scaleX": 1, "scaleY": 1}})
    req("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": fish["sceneItemId"], "sceneItemIndex": 0})
    req("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": disp["sceneItemId"], "sceneItemIndex": len(items)})
    print(">> 完成：大肥鱼右下角置顶，显示器采集垫底。OBS 里可随意拖动调整")
    return 0


if __name__ == "__main__":
    sys.exit(main())
