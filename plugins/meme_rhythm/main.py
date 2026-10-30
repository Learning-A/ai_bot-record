"""表情包节奏镜像：对方发图多就多刷，对方高冷就收敛（变相用户画像）。

病根：meme_manager 的发图通道（&&分类键&& 标记 / search_memes 工具）
一直在，但 LLM 没有任何"该多频繁发图"的指引 → 从不主动配图。

方案：被动统计每个聊天对象（按会话隔离）最近 N 条消息里带表情/图的
比例（商城表情与图片记 1 分，经典小黄脸记 0.5 分），滚动窗口保存。
每次 LLM 请求前把"对方节奏 + 对标指令"注入系统提示词，让配图频率
自然镜像对方的使用习惯，并随习惯变化实时漂移。
"""

import json
import time
from collections import deque
from pathlib import Path
from typing import Optional

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core import logger
from astrbot.core.provider.entities import ProviderRequest

_DATA_DIR = Path(__file__).resolve().parents[2] / "plugin_data" / "meme_rhythm"
_STATS_FILE = _DATA_DIR / "stats.json"


def _load_stats() -> dict:
    try:
        return json.loads(_STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_stats(stats: dict):
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATS_FILE.write_text(
            json.dumps(stats, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"meme_rhythm: 保存统计失败 {e}")


def _raw_segments(event: AstrMessageEvent) -> list:
    raw = getattr(event.message_obj, "raw_message", None)
    segs = getattr(raw, "message", None) if raw is not None else None
    out = []
    if isinstance(segs, list):
        for seg in segs:
            if isinstance(seg, dict) and "type" in seg:
                out.append(seg["type"])
    return out


def _collect_profile() -> tuple[int, str, str] | None:
    """读收藏包（meme_manager 自动收集的对方表情）语义元数据，生成口味画像。

    返回 (张数, 分类分布, 高频标签)。收藏包为空或读取失败返回 None。
    """
    from collections import Counter

    meta_path = (
        _DATA_DIR.parent / "meme_manager" / "packs" / "builtin-default"
        / "semantic_metadata.json"
    )
    try:
        imgs = list(json.loads(meta_path.read_text(encoding="utf-8"))["images"].values())
    except Exception:
        return None
    if not imgs:
        return None
    cats = Counter((i.get("category") or "?") for i in imgs)
    tags = Counter()
    for i in imgs:
        for t in i.get("tags") or []:
            if not str(t).startswith("category:"):
                tags[str(t)] += 1
    cat_str = "、".join(f"{c}×{n}" for c, n in cats.most_common(5))
    tag_str = "、".join(t for t, _ in tags.most_common(6)) or "暂无"
    return len(imgs), cat_str, tag_str


def _meme_score(event: AstrMessageEvent) -> float:
    """这条消息的"表情浓度"：图/商城表情=1，经典小黄脸=0.5，纯文字=0。"""
    types = set(_raw_segments(event))
    if "mface" in types or "image" in types:
        return 1.0
    if "face" in types:
        return 0.5
    return 0.0


def _tier(ratio: float, n: int, min_sample: int) -> str:
    """按对方节奏生成对标指令（样本不足时保持克制）。"""
    if n < min_sample:
        return (
            f"对方样本还少（{n}条），配图保持克制，只在特别贴切或对方主动要图时用。"
        )
    pct = round(ratio * 100)
    if ratio >= 0.4:
        return (
            f"对方是表情包重度用户（≈{pct}%）。你的配图节奏对标对方：约每2~3条回复"
            "自然带一张（用 &&分类键&& 标记），接对方表情、整活、情绪起伏时优先；"
            "连续两条带图后要收敛一条，别刷屏。"
        )
    if ratio >= 0.15:
        return (
            f"对方适度使用表情包（≈{pct}%）。你大约每4~6条回复带一张，"
            "只在特别贴切的时机用，其余时候靠文字表达。"
        )
    if ratio >= 0.05:
        return (
            f"对方偶尔用表情包（≈{pct}%）。你保持低频：约每8~12条一张，"
            "优先在对方先发表情或话题最贴切时回应一张。"
        )
    return (
        f"对方几乎不发图（≈{pct}%）。你也基本不主动配图，"
        "除非对方先发表情、或明确要图/斗图。"
    )


class MemeRhythm(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config
        self.window_size = int((config or {}).get("window_size", 60))
        self.min_sample = int((config or {}).get("min_sample", 8))

    @filter.event_message_type(filter.EventMessageType.ALL, priority=2 ** 31 - 10)
    async def observe(self, event: AstrMessageEvent):
        """被动记账：记录对方每条消息的表情浓度（不改消息、不拦截）。"""
        try:
            if event.get_platform_name() != "aiocqhttp":
                return
            if str(event.get_sender_id()) == str(event.get_self_id()):
                return
            umo = event.unified_msg_origin
            if not umo:
                return
            score = _meme_score(event)
            stats = _load_stats()
            rec = stats.get(umo) or {"window": []}
            win = rec.get("window") or []
            win.append(score)
            rec["window"] = win[-self.window_size:]
            rec["sender"] = event.get_sender_name() or rec.get("sender", "")
            rec["updated"] = int(time.time())
            stats[umo] = rec
            _save_stats(stats)
        except Exception as e:
            logger.warning(f"meme_rhythm: 统计失败 {e}")

    @filter.on_llm_request()
    async def inject_rhythm(self, event: AstrMessageEvent, req: ProviderRequest):
        """把对方的表情节奏 + 对标指令注入系统提示词。"""
        try:
            umo = event.unified_msg_origin
            rec = _load_stats().get(umo)
            win = (rec or {}).get("window") or []
            n = len(win)
            ratio = (sum(win) / n) if n else 0.0
            guide = _tier(ratio, n, self.min_sample)
            block = (
                "\n\n[表情包节奏镜像]\n"
                f"对方最近{n}条消息的表情/图占比≈{round(ratio * 100)}%。\n"
                f"{guide}\n"
                "永远优先文字表达，图是调味不是主菜；没有贴切的图就不发。"
            )
            prof = _collect_profile()
            if prof:
                _, _, tag_str = prof
                block += (
                    f"\n对方表情口味（来自其贡献的收藏包）：{tag_str}。"
                    "选图时优先贴合这些口味，也可以从收藏包里挑对方发过的图扔回去。"
                )
            req.system_prompt = (req.system_prompt or "") + block
        except Exception as e:
            logger.warning(f"meme_rhythm: 注入失败 {e}")

    @filter.command("表情节奏")
    async def show_rhythm(self, event: AstrMessageEvent):
        """查看当前会话的表情包节奏画像。"""
        umo = event.unified_msg_origin
        rec = _load_stats().get(umo)
        win = (rec or {}).get("window") or []
        if not win:
            yield event.plain_result("这个会话还没有表情节奏数据，多聊聊就有了~")
            return
        n = len(win)
        ratio = sum(win) / n
        pics = sum(1 for x in win if x >= 1.0)
        faces = sum(1 for x in win if x == 0.5)
        prof = _collect_profile()
        prof_line = (
            f"\n收藏夹口味画像：共{prof[0]}张（{prof[1]}），高频标签：{prof[2]}"
            if prof else "\n收藏夹还是空的，多给机器人发表情包它会自动收藏学习"
        )
        yield event.plain_result(
            f"表情节奏画像（最近{n}条）：带图/表情包 {pics} 条，"
            f"小黄脸 {faces} 条，浓度 ≈{round(ratio * 100)}%。\n"
            f"机器人对标策略：{_tier(ratio, n, self.min_sample)}"
            f"{prof_line}"
        )
