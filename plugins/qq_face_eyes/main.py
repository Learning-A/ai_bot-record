"""QQ表情翻译官 v4：让机器人看得懂 QQ 表情（经典小黄脸 + 超级表情 + 商城大表情）。

病根有三层：
1. AstrBot 的 aiocqhttp 适配器把商城表情(mface)整个丢弃
   （源码 `elif t == "mface": continue`），纯表情消息因此变成空消息。
2. 经典 face 段只有数字 id，官方表只覆盖 0~484。
3. QQ空间图床的编号和 QQ 新版表情是两套系统，按 id 去图床下载会拿到
   张冠李戴的图（524 拿到五角星，实际是朵莉亚大哭），看错图比不看更糟。

v4 方案（四级递降）：
① 官方表：本机 NapCat 提取的 329 表，覆盖经典表情；
② faceText：NapCat 的 face 段 data.raw.faceText 自带新表情真实名字
   （如 "[朵莉亚]"）——协议级真相，零成本；
③ 缓存：视觉识别过/手动种入的"画面+情绪"描述（与名字可叠加）；
④ 伴随图：超级表情偶尔附带真实渲染图（Temp PNG），GLM 看真图；
   全都没有则退化为"诚实可教学"提示（问对方，教过记住）。
"""

import base64
import json as _json
from pathlib import Path as _Path
from sys import maxsize

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star
from astrbot.core import logger

# 从本机 NapCat 提取的官方表情表（329个，覆盖 id 0~484）
try:
    _official = _json.loads(
        (_Path(__file__).parent / "qq_face_table.json").read_text(encoding="utf-8")
    )
    FACE_MAP = {int(k): v for k, v in _official.items()}
except Exception:
    FACE_MAP = {}

FACE_MAP_FALLBACK = {
    14: "微笑", 21: "偷笑", 28: "憨笑", 174: "无奈",
    194: "裂开", 195: "哭笑", 233: "doge", 305: "吃瓜",
}
if not FACE_MAP:
    FACE_MAP = FACE_MAP_FALLBACK

_CACHE_PATH = _Path(__file__).parent / "face_descriptions.json"


def _load_cache() -> dict:
    try:
        return _json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    _CACHE_PATH.write_text(
        _json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def _glm_key() -> str | None:
    """兼容 AstrBot 新旧两种 provider 配置结构取 GLM key。"""
    try:
        cfg = _json.loads(
            (_Path(__file__).resolve().parents[2] / "cmd_config.json").read_text(
                encoding="utf-8-sig"
            )
        )
        glm = next(
            (p for p in cfg.get("provider", []) if p.get("id") == "glm_4v_flash"), None
        )
        if not glm:
            return None
        if isinstance(glm.get("key"), list) and glm["key"]:
            return glm["key"][0]
        sid = glm.get("provider_source_id")
        src = next(
            (x for x in cfg.get("provider_sources", []) if x.get("id") == sid), None
        )
        if src and isinstance(src.get("key"), list) and src["key"]:
            return src["key"][0]
        if src and isinstance(src.get("key"), str) and src["key"]:
            return src["key"]
    except Exception as e:
        logger.warning(f"qq_face_eyes: 读 GLM key 失败 {e}")
    return None


def _extract_raw_segments(event: AstrMessageEvent) -> list:
    """从 raw OneBot 事件里取消息段列表（兼容数组/对象/CQ字符串三种格式）。"""
    raw = getattr(event.message_obj, "raw_message", None)
    if raw is None:
        return []
    segs = getattr(raw, "message", None)
    if isinstance(segs, str):
        import re

        out = []
        for m in re.finditer(r"\[CQ:(\w+),([^\]]*)\]", segs):
            t = m.group(1)
            kv = {}
            for pair in m.group(2).split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    kv[k] = v
            out.append({"type": t, "data": kv})
        return out
    result = []
    try:
        for seg in segs or []:
            if isinstance(seg, dict) and "type" in seg:
                result.append(seg)
            else:
                d = getattr(seg, "data", None)
                t = getattr(seg, "type", None)
                if t and isinstance(d, dict):
                    result.append({"type": t, "data": d})
                elif t:
                    result.append({"type": t, "data": {}})
    except Exception:
        pass
    return result


def _image_local_path(data: dict) -> str | None:
    """从 OneBot image 段里解析可读的本地路径或 URL。"""
    for k in ("file", "path", "url"):
        v = str(data.get(k) or "")
        if not v:
            continue
        if v.startswith("file://"):
            v = v[7:]
        if v.startswith("http"):
            return v
        p = _Path(v)
        if p.exists():
            return str(p)
        # NapCat 相对路径：相对自身目录
        alt = _Path(__file__).resolve().parents[3] / "NapCat.Shell" / v
        if alt.exists():
            return str(alt)
    return None


class QQFaceEyes(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config

    async def _vision_describe(self, src: str, fid: int | None = None) -> str | None:
        """用 GLM-4V-Flash 看真实表情图。src 为本地路径或 http(s) URL。

        成功后按 fid 缓存（fid 为 None 时也缓存，key 用 src 摘要）。
        """
        cache = _load_cache()
        key = str(fid) if fid is not None else "src:" + src[-60:]
        if key in cache:
            return cache[key]
        key_api = _glm_key()
        if not key_api:
            logger.warning("qq_face_eyes: 找不到 GLM key")
            return None
        try:
            import aiohttp

            b64 = None
            if src.startswith("http"):
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(
                        src, timeout=aiohttp.ClientTimeout(total=15)
                    ) as r:
                        if r.status != 200:
                            return None
                        raw = await r.read()
                b64 = base64.b64encode(raw).decode()
            else:
                b64 = base64.b64encode(_Path(src).read_bytes()).decode()
            body = {
                "model": "glm-4v-flash",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/png;base64," + b64}},
                    {"type": "text", "text":
                        "这是QQ聊天里的一个表情图。用10~16个字描述它的画面内容和"
                        "情绪（例：蓝发小姑娘嚎啕大哭瀑布眼泪）。只输出描述本身。"},
                ]}],
                "max_tokens": 60,
            }
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    json=body,
                    headers={"Authorization": "Bearer " + key_api},
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    if r.status != 200:
                        logger.warning(f"qq_face_eyes: GLM {r.status}")
                        return None
                    data = await r.json()
            desc = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
                .strip('"')
            )
            if desc:
                cache[key] = desc
                _save_cache(cache)
                logger.info(f"qq_face_eyes: 视觉识别{'#' + str(fid) if fid else ''} -> {desc}")
                return desc
        except Exception as e:
            logger.warning(f"qq_face_eyes: 视觉识别失败 {e}")
        return None

    @filter.event_message_type(
        filter.EventMessageType.ALL, priority=maxsize - 5
    )
    async def translate_faces(self, event: AstrMessageEvent):
        """把 QQ 表情翻译成文字补进消息链，让 LLM 和唤醒流水线看得见。"""
        try:
            if event.get_platform_name() != "aiocqhttp":
                return
            segs = _extract_raw_segments(event)
            parts = []
            for i, seg in enumerate(segs):
                t = seg.get("type")
                data = seg.get("data", {}) or {}
                if t == "mface":
                    name = str(data.get("emoji_name") or "").strip() or "表情"
                    parts.append(f"（发了个表情包：{name}）")
                elif t == "face":
                    try:
                        fid = int(data.get("id"))
                    except (TypeError, ValueError):
                        continue
                    # ① 官方表（覆盖经典表情 id 0~484）
                    name = FACE_MAP.get(fid)
                    # ② 协议自带的 faceText：新表情的名字就在段数据里（如 "[朵莉亚]"）
                    if not name:
                        raw_meta = data.get("raw")
                        if isinstance(raw_meta, dict):
                            ft = str(raw_meta.get("faceText") or "").strip().strip("[]【】")
                            if ft and not ft.isdigit():
                                name = ft
                    # ③ 历史/种入的视觉描述缓存（画面+情绪）
                    cached = _load_cache().get(str(fid))
                    if name and cached and name != cached:
                        parts.append(f"（发了QQ表情：{name}，画面：{cached}）")
                    elif name:
                        parts.append(f"（发了QQ表情：{name}）")
                    elif cached:
                        parts.append(f"（发了QQ表情：{cached}）")
                    else:
                        # ④ 消息里伴随的图片兜底（QQ NT 超级表情有时附真实渲染图）
                        img_src = None
                        for nxt in segs[i + 1: i + 3]:
                            if nxt.get("type") == "mface":
                                break
                            if nxt.get("type") == "image":
                                img_src = _image_local_path(nxt.get("data", {}) or {})
                                break
                        desc = await self._vision_describe(img_src, fid) if img_src else None
                        if desc:
                            parts.append(f"（发了QQ表情：{desc}）")
                        else:
                            logger.info(
                                f"qq_face_eyes: 未知表情#{fid} 无名无图，"
                                f"原始段={_json.dumps(segs, ensure_ascii=False)[:300]}"
                            )
                            parts.append(
                                "（发了个没见过的新表情，自然接住情绪即可；"
                                "如果好奇可以问对方这是什么表情，对方教过之后要记住）"
                            )
            if not parts:
                return
            text = "".join(parts)
            event.message_obj.message.append(Plain(text=text))
            # 关键：event.message_str 是流水线守卫读取的字段（副本），必须同步更新
            event.message_obj.message_str = (
                (event.message_obj.message_str or "") + " " + text
            ).strip()
            event.message_str = (
                (getattr(event, "message_str", "") or "") + " " + text
            ).strip()
            logger.info(f"qq_face_eyes: 已翻译表情 -> {text}")
        except Exception as e:
            logger.warning(f"qq_face_eyes: 处理失败 {e}")
