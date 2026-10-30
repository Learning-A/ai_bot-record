"""额度管家：每日 token 用量限额。

数据来源是 AstrBot 自己的 provider_stats 统计表（每次 LLM 调用都会记录
输入/输出 token），因此重启不丢账、多提供商共享同一个每日额度。
超限后通过 on_llm_request 钩子拦截 LLM 调用并礼貌告知，次日 0 点自动恢复。
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core import logger

# 插件位于 <data>/plugins/quota_guard/，上两级即数据根目录
_DATA_DB = Path(__file__).resolve().parents[2] / "data_v4.db"


class QuotaGuard(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    # ---------- 统计 ----------

    def _today_usage(self) -> tuple[int, int, int, int]:
        """返回 (输入token, 缓存输入token, 输出token, 调用次数)，统计今天0点起。"""
        midnight = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        try:
            conn = sqlite3.connect(
                f"file:{_DATA_DB}?mode=ro", uri=True, timeout=5
            )
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(token_input_other),0), "
                    "COALESCE(SUM(token_input_cached),0), "
                    "COALESCE(SUM(token_output),0), COUNT(*) "
                    "FROM provider_stats "
                    "WHERE status='completed' AND start_time >= ?",
                    (midnight,),
                ).fetchone()
                return int(row[0]), int(row[1]), int(row[2]), int(row[3])
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"quota_guard: 读取用量统计失败: {e}")
            return 0, 0, 0, 0

    def _estimate_cost(self, inp: int, cached: int, out: int) -> float:
        price_in = float(self.config.get("price_input_per_m", 0.2))
        price_out = float(self.config.get("price_output_per_m", 0.8))
        # 缓存命中的输入实际更便宜，按输入价粗算留点余量
        return ((inp + cached) * price_in + out * price_out) / 1_000_000

    def _limit(self) -> int:
        return max(0, int(self.config.get("daily_token_limit", 400000)))

    # ---------- 拦截 ----------

    @filter.on_llm_request()
    async def guard(self, event: AstrMessageEvent, req):
        if self.config.get("bypass_admins", True) and event.is_admin():
            return
        inp, cached, out, _ = self._today_usage()
        used = inp + cached + out
        cost = self._estimate_cost(inp, cached, out)
        cost_limit = float(self.config.get("daily_cost_limit", 0) or 0)
        token_limit = self._limit()
        exceeded = None
        if cost_limit > 0 and cost >= cost_limit:
            exceeded = f"今日花费已达 ¥{cost_limit:.2f} 上限"
        elif token_limit > 0 and used >= token_limit:
            exceeded = (
                f"今日AI额度用完了（{used / 10000:.1f}万/"
                f"{token_limit / 10000:.0f}万 token）"
            )
        if exceeded:
            await event.send(
                MessageChain().message(f"🐟 {exceeded}，明天0点自动恢复。发 /额度 看明细～")
            )
            event.stop_event()

    # ---------- 指令 ----------

    @filter.command("额度")
    async def quota_report(self, event: AstrMessageEvent):
        """查看今日AI用量、花费与限额"""
        inp, cached, out, calls = self._today_usage()
        used = inp + cached + out
        limit = self._limit()
        cost_limit = float(self.config.get("daily_cost_limit", 0) or 0)
        cost = self._estimate_cost(inp, cached, out)
        lines = [
            f"📊 今日AI用量（0点起）",
            f"调用：{calls} 次",
            f"输入：{(inp + cached) / 10000:.1f} 万 token（其中缓存命中 {cached / 10000:.1f} 万）",
            f"输出：{out / 10000:.1f} 万 token",
            f"合计：{used / 10000:.1f} 万 token",
            f"预计花费：¥{cost:.3f}",
        ]
        if cost_limit > 0:
            lines.append(
                f"花费限额：¥{cost_limit:.2f}/天，今日已用 ¥{cost:.3f}"
                + ("（管理员不受限）" if self.config.get("bypass_admins", True) else "")
            )
        if limit > 0:
            remain = max(0, limit - used)
            lines.append(
                f"token限额：{limit / 10000:.0f} 万/天，剩余 {remain / 10000:.1f} 万"
            )
        if cost_limit <= 0 and limit <= 0:
            lines.append("限额：未启用")
        yield event.plain_result("\n".join(lines))
