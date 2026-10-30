"""按需语音 v4：AI 自主决定何时发语音（按人格多引擎路由）。

引擎路由（按会话当前人格）：
- 小汤圆(tangyuan) / 泡泡(paopao) → GPT-SoVITS 微调音色（预训练GPT+微调SoVITS）：
  汤圆：①GPU隧道 9881→远端9880（A40） ②本地CPU 9880（30~60秒/句）
  泡泡：①GPU隧道 9883→远端9882（GPU1，清唱微调）
  失败一律回退 SiliconFlow 云端克隆
- 其余人格 → SiliconFlow /audio/speech mp3 直合成（克隆音色 wav 有服务端
  bug，绕过 provider 的 get_audio 路径）
"""

import json
import os
import uuid
from pathlib import Path

import aiohttp

from astrbot.api import AstrBotConfig, llm_tool
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Record
from astrbot.api.star import Context, Star
from astrbot.core import logger

CMD_CONFIG = Path(__file__).resolve().parents[2] / "cmd_config.json"

# 情感参考库：LLM 给每句话打情绪标签 → 路由到 matching 语调的参考段
# （GPT-SoVITS 的情感/语调由参考音频决定——社区标准情感控制法）
GSV_BY_PERSONA = {
    "tangyuan": [
        # GPU 服务器（SSH 隧道 9881 → 远端 9880，满穗干声数据集微调 v2）
        {"url": "http://127.0.0.1:9881/tts", "timeout": 180, "refs": {
            "calm": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_mansui/"
                     "mansui_full.mp3_0001096960_0001235200.wav", "尹三他是人牙子，我怕他问的多了会起疑心。"),
            "cheerful": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_mansui/"
                         "mansui_full.mp3_0019852480_0020014080.wav", "才这么干的嘛，对了，梁烨，今天还想学影子戏吗？"),
            "gentle": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_mansui/"
                       "mansui_full.mp3_0027235520_0027408000.wav", "这是尹三和你说的，那你别信他，也别怕他。"),
            "curious": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_mansui/"
                        "mansui_full.mp3_0020014080_0020135680.wav", "为什么呀？良野之前是为什么那样啊？"),
            "urgent": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_mansui/"
                       "mansui_full.mp3_0000852800_0001096960.wav", "别杀我，我什么都说我之前装成哑巴，是怕被银三认出来。"),
        }},
        # 本地 CPU 回退（仅默认 calm 参考，30~60 秒/句）
        {"url": "http://127.0.0.1:9880/tts", "timeout": 400, "refs": {
            "calm": (r"F:\apps\gpt-sovits-work\ref_mansui.wav", "尹三他是人牙子，我怕他问的多了会起疑心。"),
        }},
    ],
    "paopao": [
        # GPU 服务器（SSH 隧道 9883 → 远端 9882，GPU1，电台54分钟微调 v2）
        {"url": "http://127.0.0.1:9883/tts", "timeout": 180, "refs": {
            "calm": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_radio/"
                     "vocals.wav_0000157440_0000273280.wav", "欢迎回到叽里呱啦电台，辛苦啦。"),
            "cheerful": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_radio/"
                         "vocals.wav_0081250560_0081381120.wav", "嗯，哎，如果有人会说，我说挺正宗，那我很开心了，哈哈。"),
            "gentle": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_radio/"
                       "vocals.wav_0003765120_0003896640.wav", "有也没关系，我想到了一个好玩的测试。"),
            "curious": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_radio/"
                        "vocals.wav_0003909120_0004052480.wav", "大家想不想测一下和叽里呱啦电台的缘分呢？"),
        }},
    ],
    # 小鱼干：IndexTTS 零样本芙宁娜音色（无 GSV 权重，url 留空跳过 GSV 回退）
    "default": [
        {"url": "", "timeout": 180, "refs": {
            "calm": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_furina/"
                     "furina_full.mp3_0000079680_0000283840.wav", "富有的、贫穷的，带着酒杯或一无所有的子民们。"),
            "cheerful": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_furina/"
                         "furina_full.mp3_0002925760_0003046400.wav", "你们对我的才华感到诧异很正常。"),
            "angry": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_furina/"
                      "furina_full.mp3_0005888320_0006004160.wav", "我亲爱的子民，我准许你反驳我。"),
            "gentle": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_furina/"
                       "furina_full.mp3_0001610240_0001761920.wav", "你大可放心，你的前程我看得一清二楚。"),
            "curious": ("/home/<用户名>/gpt-sovits-work/GPT-SoVITS/output/slicer_furina/"
                        "furina_full.mp3_0003256320_0003424960.wav", "我应该说点什么吗？"),
        }},
    ],
}
FFMPEG = r"C:/Users/<你>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"

# 推理参数（用户定稿：原速+top_k默认15；满穗v2模型原速即4.0字/秒，变速会引入atempo电音）
GSV_TOP_K = 15
GSV_SPEED = 1.0

# IndexTTS-2.5（GPU服务器:9884，SSH隧道9885）：情感-音色解耦引擎。
# 情绪化语音优先走它（情感参考可外挂任意情绪音频），失败落 GPT-SoVITS。
ITTS_URL = "http://127.0.0.1:9885/tts"
ITTS_TIMEOUT = 180

# 人格 → 音色引擎（gsv=IndexTTS/GPT-SoVITS 链；sf=SiliconFlow 云端克隆）
PERSONA_VOICE = {
    "tangyuan": {"engine": "gsv"},
    "paopao": {"engine": "gsv"},
    "default": {"engine": "gsv"},
}


class VoiceOnDemand(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config

    async def _current_persona(self, umo: str) -> str | None:
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(umo)
            if cid:
                conv = await cm.get_conversation(umo, cid)
                return conv.persona_id if conv else None
        except Exception:
            pass
        return None

    async def _synth_itts(self, text: str, persona: str, mood: str) -> str | None:
        """IndexTTS-2.5：音色用会话人格的平静参考，情感用情绪参考（解耦）。"""
        import subprocess

        eps = GSV_BY_PERSONA.get(persona, [])
        server_refs = next(
            (ep["refs"] for ep in eps
             if ep["url"].endswith("9881/tts") or ep["url"].endswith("9883/tts")),
            next((ep["refs"] for ep in eps if ep.get("refs")), None),
        )
        if not server_refs:
            return None
        spk_ref, _ = server_refs.get("calm") or next(iter(server_refs.values()))
        emo_ref, _ = (server_refs.get(mood) if mood != "calm" else (None, None)) or (None, None)
        body = {
            "text": text,
            "spk_audio": spk_ref,
            "emo_audio": emo_ref,   # None=不用情感参考（由音色参考自带情感）
            "emo_alpha": 1.0,
            "lang": "zh",
        }
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    ITTS_URL, json=body, timeout=aiohttp.ClientTimeout(total=ITTS_TIMEOUT)
                ) as r:
                    if r.status != 200:
                        logger.warning(f"voice_on_demand: ITTS HTTP {r.status}")
                        return None
                    wav = await r.read()
            if len(wav) < 20000:
                logger.warning(f"voice_on_demand: ITTS 音频过短 {len(wav)}B")
                return None
            wav_path = (
                Path(os.environ.get("TEMP", "."))
                / f"vod_{uuid.uuid4().hex[:10]}.wav"
            )
            mp3_path = wav_path.with_suffix(".mp3")
            wav_path.write_bytes(wav)
            subprocess.run(
                [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(wav_path), "-b:a", "128k", str(mp3_path)],
                check=True, timeout=60,
            )
            wav_path.unlink(missing_ok=True)
            logger.info(f"voice_on_demand: ITTS 合成成功(mood={mood})")
            return str(mp3_path)
        except Exception as e:
            logger.warning(f"voice_on_demand: ITTS 失败 {e}")
            return None

    async def _synth_gsv(self, text: str, persona: str, mood: str = "calm") -> str | None:
        """GPT-SoVITS 合成：按人格+情绪选参考段，GPU 隧道优先 → 本地 CPU 回退。"""
        import subprocess

        for ep in GSV_BY_PERSONA.get(persona, []):
            if not ep.get("url"):  # 仅配置参考段、无 GSV 端点（如小鱼干 ITTS 专属）
                continue
            refs = ep.get("refs", {})
            ref, prompt = refs.get(mood) or refs.get("calm") or next(iter(refs.values()))
            body = {
                "text": text,
                "text_lang": "zh",
                "ref_audio_path": ref,
                "prompt_text": prompt,
                "prompt_lang": "zh",
                "streaming_mode": False,
                "speed_factor": GSV_SPEED,
                "top_k": GSV_TOP_K,
            }
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        ep["url"], json=body,
                        timeout=aiohttp.ClientTimeout(total=ep["timeout"]),
                    ) as r:
                        if r.status != 200:
                            logger.warning(
                                f"voice_on_demand: GSV {ep['url']} HTTP {r.status}"
                            )
                            continue
                        wav = await r.read()
                if len(wav) < 20000:
                    logger.warning(
                        f"voice_on_demand: GSV {ep['url']} 音频过短 {len(wav)}B"
                    )
                    continue
                wav_path = (
                    Path(os.environ.get("TEMP", "."))
                    / f"vod_{uuid.uuid4().hex[:10]}.wav"
                )
                mp3_path = wav_path.with_suffix(".mp3")
                wav_path.write_bytes(wav)
                subprocess.run(
                    [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                     "-i", str(wav_path), "-b:a", "128k", str(mp3_path)],
                    check=True, timeout=60,
                )
                wav_path.unlink(missing_ok=True)
                logger.info(f"voice_on_demand: GSV {ep['url']} 合成成功")
                return str(mp3_path)
            except Exception as e:
                logger.warning(f"voice_on_demand: GSV {ep['url']} 失败 {e}")
                continue
        return None

    async def _synth_mp3(self, umo: str, text: str) -> str | None:
        """按会话当前选中的 openai_tts_api 提供商配置，直接合成 mp3。"""
        try:
            prov = await self.context.get_using_tts_provider_async(
                umo=umo
            )
            pid = prov.meta().id if prov else None
        except Exception:
            pid = None
        if not pid:
            return None
        cfg = json.loads(CMD_CONFIG.read_text(encoding="utf-8-sig"))
        entry = next(
            (
                p
                for p in cfg.get("provider", [])
                if p.get("id") == pid and p.get("type") == "openai_tts_api"
            ),
            None,
        )
        if not entry:
            return None
        body = {
            "model": entry.get("model", "FunAudioLLM/CosyVoice2-0.5B"),
            "input": text,
            "voice": entry.get("openai-tts-voice", ""),
            "response_format": "mp3",
        }
        url = entry.get("api_base", "").rstrip("/") + "/audio/speech"
        headers = {"Authorization": f"Bearer {entry.get('api_key', '')}"}
        out = (
            Path(os.environ.get("TEMP", ".")) / f"vod_{uuid.uuid4().hex[:10]}.mp3"
        )
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=120)
                ) as r:
                    if r.status != 200:
                        logger.warning(f"voice_on_demand: 合成 HTTP {r.status}")
                        return None
                    data = await r.read()
            if len(data) < 1000:
                logger.warning(f"voice_on_demand: 合成音频过短 {len(data)}B")
                return None
            out.write_bytes(data)
            return str(out)
        except Exception as e:
            logger.warning(f"voice_on_demand: mp3合成失败 {e}")
            return None

    @llm_tool(name="send_voice")
    async def send_voice(self, event: AstrMessageEvent, text: str, mood: str = "calm"):
        """把这句话用语音消息发出去（就像真人发语音条）。两种情况使用：①用户明说要语音时**必须调用**——比如"发语音""用语音说""唱一句""语音撒娇""说给我听"等，不许只回文字；②你自己特别想用声音表达时（情绪浓烈、哼歌、撒娇、想安慰人）。平时正常打字即可。合成约需几秒到半分钟，调用后耐心等待结果，不要重试。文字请写成真的会说出口的话（带语气词、感叹，别书面化）。

        Args:
            text(string): 要用语音说出的完整内容（口语化，一两句即可）
            mood(string): 这句话的情绪，决定用哪种语调说：cheerful=开心兴奋、gentle=温柔安抚、curious=好奇期待、urgent=着急急切、calm=平静日常（默认）
        """
        umo = event.unified_msg_origin
        text = text.strip()
        mood = (mood or "calm").strip().lower() or "calm"
        persona = await self._current_persona(umo)
        vcfg = PERSONA_VOICE.get(persona or "default") or {"engine": "sf"}

        mp3 = None
        if vcfg.get("engine") == "gsv":
            # 全量 IndexTTS 优先（用户定稿）；失败落 GSV，最后落 SiliconFlow
            mp3 = await self._synth_itts(text, persona or "tangyuan", mood)
            if mp3 is None:
                mp3 = await self._synth_gsv(text, persona or "tangyuan", mood)
        if mp3 is None:
            # 云端路径（也作为 GSV 失败的回退）
            mp3 = await self._synth_mp3(umo, text)
        if mp3:
            await event.send(MessageChain(chain=[Record(file=mp3, url=mp3, text=text)]))
            logger.info(f"voice_on_demand: 语音已发送({vcfg.get('engine')}→成功, {len(text)}字)")
            return "语音已发出。后续回复继续用文字，不要重复语音内容。"
        # 最后回退：provider 原生路径（EdgeTTS 等）
        try:
            tts = await self.context.get_using_tts_provider_async(
                umo=umo
            )
            if not tts:
                return "当前没有可用语音服务，直接文字回复"
            audio_path = await tts.get_audio(text)
            await event.send(
                MessageChain(chain=[Record(file=audio_path, url=audio_path, text=text)])
            )
            logger.info(f"voice_on_demand: 语音已发送(回退路径, {len(text)}字)")
            return "语音已发出。后续回复继续用文字，不要重复语音内容。"
        except Exception as e:
            logger.warning(f"voice_on_demand: 语音失败 {e}")
            return f"语音发送失败，请直接文字回复"
