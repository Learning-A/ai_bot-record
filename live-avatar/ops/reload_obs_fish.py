# -*- coding: utf-8 -*-
"""强制刷新 OBS 里的大肥鱼浏览器源（鱼不动/空白时用）。
自动发现 OBS 接口密码（读取 obs-websocket 配置，未启用则自动开启并提示重启 OBS）。"""
import base64, hashlib, json, os, socket, struct, sys, time

OBS_CFG = os.path.expandvars(r"%APPDATA%\obs-studio\plugin_config\obs-websocket\config.json")

BUF = [b""]  # 共享接收缓冲（列表容器保持引用）


def main():
    if not os.path.exists(OBS_CFG):
        print(">> 找不到 OBS 配置：OBS 从未启动过？先启动 OBS")
        return 1
    cfg = json.load(open(OBS_CFG, encoding="utf-8"))
    if not cfg.get("server_enabled"):
        cfg["server_enabled"] = True
        json.dump(cfg, open(OBS_CFG, "w", encoding="utf-8"), indent=2)
        print(">> 已自动开启 OBS 接口，请重启 OBS 后再运行本脚本")
        return 1
    password = cfg.get("server_password", "")
    port = int(cfg.get("server_port", 4455))

    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=6)
    except OSError:
        print(">> 连不上 OBS 接口（端口 %d）：OBS 没在运行？先启动 OBS" % port)
        return 1
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: " + key + "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        c = s.recv(4096)
        if not c:
            print(">> 握手失败"); return 1
        resp += c
    if b"101" not in resp.split(b"\r\n")[0]:
        print(">> 握手被拒"); return 1
    BUF[0] = resp.split(b"\r\n\r\n", 1)[1]

    def send_text(obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        head = bytearray([0x81])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126); head += struct.pack(">H", n)
        head += mask
        s.sendall(bytes(head) + masked)

    def need(k):
        while len(BUF[0]) < k:
            d = s.recv(65536)
            if not d:
                raise ConnectionError("closed")
            BUF[0] += d
        out, BUF[0] = BUF[0][:k], BUF[0][k:]
        return out

    def recv_msg():
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

    hello = recv_msg()
    secret = base64.b64encode(hashlib.sha256((password + hello["d"]["authentication"]["salt"]).encode()).digest()).decode()
    auth = base64.b64encode(hashlib.sha256((secret + hello["d"]["authentication"]["challenge"]).encode()).digest()).decode()
    send_text({"op": 1, "d": {"rpcVersion": 1, "authentication": auth, "eventSubscriptions": 0}})
    if recv_msg().get("op") != 2:
        print(">> OBS 接口认证失败：重启 OBS 后重试（密码可能轮换）")
        return 1

    mid = [0]
    def req(t, data=None):
        mid[0] += 1
        d = {"requestType": t, "requestId": str(mid[0])}
        if data is not None:
            d["requestData"] = data
        send_text({"op": 6, "d": d})
        rid = str(mid[0])
        while True:
            m = recv_msg()
            if m.get("op") == 7 and m["d"]["requestId"] == rid:
                if not m["d"]["requestStatus"]["result"]:
                    raise RuntimeError(t + ": " + str(m["d"]["requestStatus"].get("comment")))
                return m["d"].get("responseData") or {}

    scene = req("GetSceneList")["currentProgramSceneName"]
    items = req("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    fish = next((i for i in items if i["sourceName"] == "大肥鱼直播形象"), None)
    if not fish:
        print(">> 当前场景[%s]没有大肥鱼源 → 运行 修复OBS场景.bat 重建" % scene)
        return 1
    req("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": fish["sceneItemId"], "sceneItemEnabled": False})
    time.sleep(1.5)
    req("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": fish["sceneItemId"], "sceneItemEnabled": True})
    print(">> 已强制刷新大肥鱼浏览器源（场景：%s）" % scene)
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
