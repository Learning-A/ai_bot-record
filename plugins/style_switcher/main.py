"""风格切换器：每个聊天对象独立切换人格风格，音色自动跟随人设。

- /风格            查看当前风格和可选列表
- /风格 <名字>     切换风格（写入当前会话的 persona_id，只影响自己）
- /风格 重置       回到管理员设定的默认风格

切换会同时清空当前会话上下文，让新风格从干净状态开始；
LivingMemory 的长期记忆不受影响。

音色联动：每个人格绑定一个专属语音音色（edge_tts 提供商），
切人格时同步切换，且每次 LLM 请求前自动对齐（防止其他途径改人格后音色错位）。
"""

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import logger
from astrbot.core.provider.entities import ProviderType

# 人格 -> 专属音色提供商
PERSONA_ALIAS = {"小鱼干": "default", "泡泡": "paopao", "汤圆": "tangyuan", "小汤圆": "tangyuan"}

PERSONA_TTS = {
    "paopao": "sf_tts_paopao",    # CosyVoice2 anna · 阳光青年女声
    "default": "sf_tts_yugan",    # CosyVoice2 alex · 阳光青年男声（小鱼干）
    "tangyuan": "sf_tts_tangyuan",  # CosyVoice2 diana · 温柔女声
}


class StyleSwitcher(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    async def _persona_ids(self) -> set:
        try:
            personas = await self.context.persona_manager.get_all_personas()
            return {p.persona_id for p in personas}
        except Exception as e:
            logger.warning(f"style_switcher: 获取人格列表失败: {e}")
            return set()

    def _descs(self) -> dict:
        d = self.config.get("style_descs", {}) or {}
        return d if isinstance(d, dict) else {}

    def _default_persona_id(self, umo: str) -> str:
        cfg = self.context.get_config(umo=umo)
        return cfg["agent_runner"]["config"]["persona"]["persona_id"]

    async def _sync_tts(self, umo: str, persona_id: str | None):
        """把会话的 TTS 提供商对齐到该人格的专属音色。"""
        target = PERSONA_TTS.get(persona_id or "")
        if not target:
            return
        try:
            current = await self.context.get_using_tts_provider_async(
                umo=umo
            )
            if current and current.meta().id == target:
                return
            await self.context.provider_manager.set_provider(
                provider_id=target,
                provider_type=ProviderType.TEXT_TO_SPEECH,
                umo=umo,
            )
            logger.info(f"style_switcher: {umo} 音色已对齐 {persona_id} -> {target}")
        except Exception as e:
            logger.warning(f"style_switcher: 音色切换失败: {e}")

    @filter.on_llm_request()
    async def auto_sync_voice(self, event: AstrMessageEvent, req):
        """每次 LLM 请求前，把音色对齐到当前会话的人格。"""
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(event.unified_msg_origin)
            persona_id = None
            if cid:
                conv = await cm.get_conversation(event.unified_msg_origin, cid)
                persona_id = conv.persona_id if conv else None
            await self._sync_tts(event.unified_msg_origin, persona_id)
        except Exception as e:
            logger.debug(f"style_switcher: 音色自动对齐跳过: {e}")

    @filter.command("风格", alias={"切换风格"})
    async def style(self, event: AstrMessageEvent, arg: str = ""):
        """查看/切换当前聊天的人格风格（只影响你自己）"""
        umo = event.unified_msg_origin
        cm = self.context.conversation_manager
        arg = (arg or "").strip()
        descs = self._descs()
        ids = await self._persona_ids()
        if not ids:
            yield event.plain_result("风格列表加载失败，稍后再试")
            return

        if not arg:
            # 列表展示
            cid = await cm.get_curr_conversation_id(umo)
            cur = None
            if cid:
                conv = await cm.get_conversation(umo, cid)
                cur = conv.persona_id if conv else None
            default_id = self._default_persona_id(umo)
            cur = cur or default_id
            lines = ["可切换的风格（/风格 <名字> 切换，只影响你自己）："]
            for pid in sorted(ids):
                mark = "  ←当前" if pid == cur else ""
                star = "（默认）" if pid == default_id else ""
                lines.append(f"· {pid}{star} {descs.get(pid, '')}{mark}")
            yield event.plain_result("\n".join(lines))
            return

        # 解析目标人格：中文别名 > 精确ID > 重置关键词 > 模糊匹配
        key = None
        if arg in PERSONA_ALIAS and PERSONA_ALIAS[arg] in ids:
            key = PERSONA_ALIAS[arg]
        elif arg in ids:
            key = arg
        elif arg in ("重置", "默认", "default"):
            key = self._default_persona_id(umo)
        else:
            for pid in ids:
                if arg.lower() in pid.lower():
                    key = pid
                    break
        if key is None:
            yield event.plain_result(
                f"没有「{arg}」这个风格哦，发 /风格 看看可选列表"
            )
            return

        # 写入会话：有会话则更新（顺便清上下文），没有则新建带人格的会话
        cid = await cm.get_curr_conversation_id(umo)
        if cid:
            await cm.update_conversation(umo, cid, persona_id=key, history=[])
        else:
            await cm.new_conversation(umo, persona_id=key)
        # 音色跟随人格
        await self._sync_tts(umo, key)
        yield event.plain_result(
            f"✅ 已切换到「{key}」{descs.get(key, '')}\n声音也换好啦，直接开聊～"
        )
