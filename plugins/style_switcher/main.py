"""风格切换器：每个聊天对象独立切换人格风格，音色与专属知识库自动跟随人设。

- /风格            查看当前风格和可选列表
- /风格 <名字>     切换风格（写入当前会话的 persona_id，只影响自己）
- /风格 重置       回到管理员设定的默认风格
- /风格知识库      查看人格↔知识库绑定状态（管理员）
- /风格知识库 建 <人格id> [描述]  为人格创建专属知识库（管理员）
- /风格知识库 绑   手动重新绑定当前会话的知识库（管理员）

切换会同时清空当前会话上下文，让新风格从干净状态开始；
LivingMemory 的长期记忆不受影响。

音色联动：每个人格绑定一个专属语音音色，切人格时同步切换，
且每次 LLM 请求前自动对齐（防止其他途径改人格后音色错位）。

知识库联动（AstrBot 原生人格与知识库不联动，本插件补齐）：
每个人格绑定一个专属知识库，切换人格时写入会话级 kb_config
（sp 存储，优先级高于全局 kb_names，机制与 /persona 的
session_service_config 同源）。映射的人格库不存在时自动创建。
"""

from astrbot.api import AstrBotConfig, sp
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

# 人格 -> 专属知识库名（kb_manager 按名解析 id；不存在时自动创建）
PERSONA_KB = {
    "paopao": "泡泡专属知识库",
    "default": "小鱼干专属知识库",
    "tangyuan": "汤圆专属知识库",
}

KB_EMBEDDING_PROVIDER = "siliconflow_embedding"


class StyleSwitcher(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._kb_ensure_attempted: set[str] = set()

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

    # ---------- 知识库联动 ----------

    async def _resolve_kb_id(self, persona_id: str) -> str | None:
        """按名解析人格专属库 id；不存在则自动创建（每进程每人格只尝试一次）。"""
        kb_name = PERSONA_KB.get(persona_id)
        if not kb_name:
            return None
        try:
            helper = await self.context.kb_manager.get_kb_by_name(kb_name)
            if helper is not None:
                return helper.kb.kb_id
            if kb_name in self._kb_ensure_attempted:
                return None
            self._kb_ensure_attempted.add(kb_name)
            helper = await self.context.kb_manager.create_kb(
                kb_name=kb_name,
                description=f"人格「{persona_id}」的专属知识库（style_switcher 自动创建）",
                embedding_provider_id=KB_EMBEDDING_PROVIDER,
            )
            logger.info(f"style_switcher: 已自动创建人格知识库「{kb_name}」({helper.kb.kb_id})")
            return helper.kb.kb_id
        except Exception as e:
            logger.warning(f"style_switcher: 解析/创建知识库「{kb_name}」失败: {e}")
            return None

    async def _sync_kb(self, umo: str, persona_id: str | None):
        """把会话级 kb_config 对齐到该人格的专属库（无映射=显式空，防全局库泄漏）。"""
        try:
            target_ids: list[str] = []
            if persona_id:
                kb_id = await self._resolve_kb_id(persona_id)
                if kb_id:
                    target_ids = [kb_id]
            current = await sp.get_async(
                scope="umo", scope_id=umo, key="kb_config", default={},
            ) or {}
            if list(current.get("kb_ids") or []) == target_ids:
                return  # 已一致，免写
            await sp.put_async(
                scope="umo", scope_id=umo, key="kb_config",
                value={"kb_ids": target_ids, "top_k": 5},
            )
            kb_name = PERSONA_KB.get(persona_id or "") or "（无）"
            logger.info(f"style_switcher: {umo} 知识库已联动 {persona_id} -> {kb_name}")
        except Exception as e:
            logger.warning(f"style_switcher: 知识库联动失败: {e}")

    @filter.on_llm_request()
    async def auto_sync_voice(self, event: AstrMessageEvent, req):
        """每次 LLM 请求前，把音色与知识库对齐到当前会话的人格。"""
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(event.unified_msg_origin)
            persona_id = None
            if cid:
                conv = await cm.get_conversation(event.unified_msg_origin, cid)
                persona_id = conv.persona_id if conv else None
            await self._sync_tts(event.unified_msg_origin, persona_id)
            await self._sync_kb(event.unified_msg_origin, persona_id)
        except Exception as e:
            logger.debug(f"style_switcher: 音色/知识库自动对齐跳过: {e}")

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
        # 音色与知识库跟随人格
        await self._sync_tts(umo, key)
        await self._sync_kb(umo, key)
        yield event.plain_result(
            f"✅ 已切换到「{key}」{descs.get(key, '')}\n声音和专属知识库都换好啦，直接开聊～"
        )

    @filter.command("风格知识库")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def persona_kb(self, event: AstrMessageEvent, arg: str = "", desc: str = ""):
        """管理人格↔知识库绑定：无参=状态；建 <人格id> [描述]；绑=重绑当前会话"""
        arg = (arg or "").strip()
        if not arg:
            lines = ["人格 ↔ 专属知识库绑定："]
            try:
                kbs = {k.kb_name: k.kb_id for k in await self.context.kb_manager.list_kbs()}
            except Exception as e:
                yield event.plain_result(f"知识库列表读取失败: {e}")
                return
            for pid, name in PERSONA_KB.items():
                exists = "✅" if name in kbs else "❌未建（首次对话自动创建，或用「建」）"
                lines.append(f"· {pid} -> {name} {exists if name not in kbs else '✅ ' + kbs[name][:8]}")
            umo = event.unified_msg_origin
            cur_kb = await sp.get_async(scope="umo", scope_id=umo, key="kb_config", default={}) or {}
            lines.append(f"\n本会话当前绑定 kb_ids: {cur_kb.get('kb_ids') or []}")
            yield event.plain_result("\n".join(lines))
            return

        if arg == "绑":
            cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
            persona = None
            if cid:
                conv = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, cid)
                persona = conv.persona_id if conv else None
            self._kb_ensure_attempted.discard(PERSONA_KB.get(persona or "", ""))
            await self._sync_kb(event.unified_msg_origin, persona)
            yield event.plain_result(f"已按当前人格「{persona or '未设置'}」重新绑定知识库")
            return

        if arg == "建":
            yield event.plain_result("用法：风格知识库 建 <人格id>，库会按映射名自动创建；无映射的人格请先在代码 PERSONA_KB 中配置")
            return

        # 建 <人格id>
        if arg not in PERSONA_KB:
            yield event.plain_result(f"人格 {arg} 没有知识库映射（可映射：{', '.join(PERSONA_KB)}）")
            return
        self._kb_ensure_attempted.discard(PERSONA_KB[arg])
        kb_id = await self._resolve_kb_id(arg)
        if kb_id:
            yield event.plain_result(f"✅ 「{PERSONA_KB[arg]}」就绪 ({kb_id[:8]})，可在 WebUI 知识库页上传文档")
        else:
            yield event.plain_result("❌ 创建失败，看服务端日志")
