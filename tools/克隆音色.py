"""一键音色克隆工具：视频链接 → 专属克隆音色。

用法（在 qq-ai-bot 目录下）：
  python tools/克隆音色.py <视频链接> --name 泡泡真声 [--start 00:01:30] [--dur 45]

参数：
  <视频链接>   B站/YouTube 等 yt-dlp 支持的链接
  --name      克隆音色的名字（英文/拼音更稳，如 paopao_real）
  --start     参考片段起始时间（默认自动取视频 1/4 处）
  --dur       参考片段时长秒数（默认 45，建议 30~60）

流程：下载该段音频 → SenseVoice 转写 → 上传克隆 → 生成试听文件
产出：tools/output/<name>_preview.mp3（试听） + 控制台打印音色 URI
"""

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
KEY_FILE = HERE.parent / "siliconflow-api.txt"
PY = sys.executable
FFMPEG = r"C:/Users/<你>\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe"
SF = "https://api.siliconflow.cn/v1"


def sf_post(path, data=None, headers=None, form=None, timeout=120):
    req = urllib.request.Request(
        SF + path, data=data, headers=headers or {}
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _to_seconds(t: str) -> float:
    parts = [float(x) for x in t.split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def _to_hms(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--name", required=True)
    ap.add_argument("--start", default=None, help="如 00:01:30")
    ap.add_argument("--dur", type=int, default=45)
    ap.add_argument("--separate", action="store_true",
                    help="先用 demucs 分离人声（翻唱/带BGM音源必开）")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    key = KEY_FILE.read_text().strip()
    auth = {"Authorization": f"Bearer {key}"}

    # 1) 下载参考音频（可选时间段）
    ref = OUT / f"{args.name}_ref.mp3"
    cmd = [PY, "-m", "yt_dlp", "-x", "--audio-format", "mp3",
           "--ffmpeg-location", FFMPEG,
           "-o", str(ref.with_suffix(".%(ext)s")), "--force-overwrites",
           "--no-part"]
    if args.start:
        end = _to_hms(_to_seconds(args.start) + args.dur)
        cmd += ["--download-sections", f"*{args.start}-{end}"]
    cmd.append(args.url)
    print("[1/4] 下载音频 ...")
    subprocess.run(cmd, check=True)
    # 只认最终 mp3，排除中间产物 m4a
    cands = [p for p in OUT.glob(f"{args.name}_ref.*") if p.suffix == ".mp3"]
    if not cands:
        raise SystemExit("未找到下载产物 mp3")
    ref = cands[0]
    if args.separate:
        print("[1.5/4] demucs 分离人声（去伴奏）...")
        subprocess.run([PY, "-m", "demucs", "--two-stems=vocals", "-n", "htdemucs",
                        "-j", "4", str(ref), "-o", str(OUT / "sep_run")], check=True)
        voc = next((OUT / "sep_run" / "htdemucs" / ref.stem).glob("vocals.wav"))
        mp3 = OUT / f"{args.name}_vocals.mp3"
        subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(voc), "-b:a", "128k", str(mp3)], check=True)
        ref = mp3
    print("     参考音频:", ref.name, ref.stat().st_size, "bytes")

    # 2) ASR 转写
    print("[2/4] 转写文本 ...")
    import uuid
    boundary = uuid.uuid4().hex
    with open(ref, "rb") as f:
        audio_bytes = f.read()
    parts = []
    for name, val in [("model", "FunAudioLLM/SenseVoiceSmall")]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="ref.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n'.encode() + audio_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{SF}/audio/transcriptions", data=b"".join(parts),
        headers={**auth, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        text = json.load(urllib.request.urlopen(req, timeout=180))["text"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"ASR 转写失败 HTTP {e.code}: {e.read().decode()[:300]}")
    print("     转写结果:", text[:80], "..." if len(text) > 80 else "")

    # 3) 上传克隆音色
    print("[3/4] 上传克隆音色 ...")
    import base64
    b64 = "data:audio/mpeg;base64," + base64.b64encode(audio_bytes).decode()
    body = json.dumps({"customName": args.name, "text": text,
                       "audio": b64, "model": "FunAudioLLM/CosyVoice2-0.5B"}).encode()
    req = urllib.request.Request(f"{SF}/uploads/audio/voice", data=body,
        headers={**auth, "Content-Type": "application/json"})
    uri = json.load(urllib.request.urlopen(req, timeout=120))["uri"]
    print("     音色 URI:", uri)

    # 4) 试听合成
    print("[4/4] 生成试听 ...")
    preview = OUT / f"{args.name}_preview.mp3"
    body = json.dumps({"model": "FunAudioLLM/CosyVoice2-0.5B",
                       "input": "克隆完成啦，这是我的新声音，你听听像不像",
                       "voice": uri, "response_format": "mp3"}).encode()
    req = urllib.request.Request(f"{SF}/audio/speech", data=body,
        headers={**auth, "Content-Type": "application/json"})
    preview.write_bytes(urllib.request.urlopen(req, timeout=120).read())
    print("\n✅ 完成！试听文件:", preview)
    print("   音色 URI（发给 ZCode 接入机器人）:", uri)


if __name__ == "__main__":
    main()
