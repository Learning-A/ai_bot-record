"""大肥鱼直播助手：B站开播自动提醒 + 直播状态查询。

直播间号默认自动跟随 Bili Live Hime（B站直播姬）的配置，
与直播工具链（danmaku_hub 等）保持同一直播间。
"""

import asyncio
import json
import os

import aiohttp

from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.api import AstrBotConfig
from astrbot.core import logger

BILI_ROOM_INFO = "https://api.live.bilibili.com/room/v1/Room/get_info"
HIME_CONFIG = os.path.expandvars(r"%APPDATA%\com.rsplwe.bili-live-hime\app-config.json")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://live.bilibili.com/",
}


def _detect_room_from_hime() -> int:
    try:
        with open(HIME_CONFIG, encoding="utf-8") as f:
            rid = int(json.load(f).get("roomId") or 0)
        if rid:
            return rid
    except Exception:
        pass
    return 0


class DaFeiYuLive(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._task: asyncio.Task | None = None
        self._was_live: bool | None = None

    async def initialize(self):
        self._task = asyncio.create_task(self._poll_loop())

    async def terminate(self):
        if self._task:
            self._task.cancel()

    # ---------- 内部工具 ----------

    def _room_id(self) -> int:
        if self.config.get("auto_detect_room", True):
            rid = _detect_room_from_hime()
            if rid:
                return rid
        return int(self.config.get("room_id", 0) or 0)

    async def _fetch_room(self) -> dict | None:
        rid = self._room_id()
        if not rid:
            return None
        try:
            async with aiohttp.ClientSession(headers=HEADERS) as s:
                async with s.get(
                    BILI_ROOM_INFO,
                    params={"room_id": rid},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
            if data.get("code") == 0:
                return data["data"]
        except Exception as e:
            logger.warning(f"dafeiyu_live: 查询直播间接口失败: {e}")
        return None

    @staticmethod
    def _status_text(info: dict) -> str:
        live = "🟢 直播中" if info.get("live_status") == 1 else "⚪ 未开播"
        online = info.get("online")
        fans = info.get("attention")
        return (
            f"{live}\n"
            f"标题：{info.get('title') or '?'}\n"
            f"分区：{info.get('parent_area_name') or '?'} / {info.get('area_name') or '?'}\n"
            f"在线：{online if online is not None else '?'}   "
            f"粉丝：{fans if fans is not None else '?'}\n"
            f"房间：https://live.bilibili.com/{info.get('room_id') or '?'}"
        )

    @staticmethod
    def _status_chain(info: dict) -> list:
        """状态组件列表：开播时附带直播画面关键帧截图。"""
        chain = []
        keyframe = (info.get("keyframe") or "").strip()
        if info.get("live_status") == 1 and keyframe:
            chain.append(Image.fromURL(keyframe))
        chain.append(Plain(DaFeiYuLive._status_text(info)))
        return chain

    def _targets(self) -> list[str]:
        """开播/下播通知的推送目标：群聊 + 私聊。"""
        targets: list[str] = []
        for gid in self.config.get("announce_groups", []) or []:
            targets.append(f"aiocqhttp:GroupMessage:{int(gid)}")
        for fid in self.config.get("announce_friends", []) or []:
            targets.append(f"aiocqhttp:FriendMessage:{int(fid)}")
        return targets

    async def _announce(self, info: dict):
        targets = self._targets()
        if not targets:
            return
        components = [Plain("🐟 大肥鱼开播啦！快来鱼塘玩~\n")]
        components += self._status_chain(info)
        chain = MessageChain(chain=components)
        for umo in targets:
            try:
                await self.context.send_message(umo, chain)
            except Exception as e:
                logger.warning(f"dafeiyu_live: 开播通知发送失败 {umo}: {e}")

    async def _poll_loop(self):
        # 等待平台适配器就绪
        await asyncio.sleep(10)
        while True:
            try:
                info = await self._fetch_room()
                if info:
                    is_live = info.get("live_status") == 1
                    if self._was_live is None:
                        # 启动后只记录当前状态，不补发通知
                        self._was_live = is_live
                    elif is_live and not self._was_live:
                        await self._announce(info)
                    elif (
                        not is_live
                        and self._was_live
                        and self.config.get("announce_offline", False)
                    ):
                        for umo in self._targets():
                            try:
                                await self.context.send_message(
                                    umo,
                                    MessageChain().message("🐋 鱼下播了，今天也辛苦啦~"),
                                )
                            except Exception as e:
                                logger.warning(f"dafeiyu_live: 下播通知失败 {umo}: {e}")
                    self._was_live = is_live
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"dafeiyu_live: 轮询异常: {e}")
            await asyncio.sleep(max(15, int(self.config.get("interval_sec", 60))))

    # ---------- 指令 ----------

    @filter.command("直播状态", alias={"直播间"})
    async def live_status(self, event: AstrMessageEvent):
        """查询大肥鱼直播间的实时状态（开播时带直播画面）"""
        info = await self._fetch_room()
        if not info:
            yield event.plain_result("鱼塘失联了…查不到直播间信息（接口超时或房间号未配置）")
            return
        yield event.chain_result(self._status_chain(info))
