#!/bin/bash
# 小汤圆奶音微调流水线（GPU 服务器）— 遵守：只在 lmj 目录干活、用卡前查占用
set -e
cd ~/gpt-sovits-work/GPT-SoVITS
PY=.venv/bin/python
EXP=tangyuan
export HF_ENDPOINT=https://hf-mirror.com

# ---- 选卡：挑当前占用最小的卡（他人优先） ----
pick_gpu() {
  nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | sort -t, -k2 -n | head -1 | cut -d, -f1 | tr -d ' '
}
GPU=$(pick_gpu)
USED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $GPU)
echo "[gpu] 选用卡 $GPU（当前占用 ${USED}MiB）"
if [ "$USED" -gt 2000 ]; then echo "[gpu] 警告：两卡都忙，暂停（他人优先）"; exit 42; fi

echo "=== [1/6] 切分音频 ==="
$PY -s tools/slice_audio.py ~/gpt-sovits-work/tangyuan_full.mp3 \
    output/slicer_opt -34 4000 300 10 500 0.9 0.25 0 1
ls output/slicer_opt | wc -l

echo "=== [2/6] faster-whisper ASR（首次自动下 large-v3 模型约3GB） ==="
$PY tools/asr/fasterwhisper_asr.py -i output/slicer_opt -o output/asr_opt -s large-v3 -l zh -p float16
head -3 output/asr_opt/*.list

echo "=== [3/6] 文本特征（1-get-text，用BERT） ==="
export inp_text="$(ls output/asr_opt/*.list | head -1)"
export inp_wav_dir=output/slicer_opt
export exp_name=$EXP
export opt_dir=output/$EXP
export bert_pretrained_dir=GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
export _CUDA_VISIBLE_DEVICES=$GPU
export CUDA_VISIBLE_DEVICES=$GPU
export i_part=0
export all_parts=1
export is_half=True
export version=v2
$PY -s GPT_SoVITS/prepare_datasets/1-get-text.py

echo "=== [4/6] 语义特征（3-get-semantic，用SoVITS底模） ==="
export pretrained_s2G=GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth
$PY -s GPT_SoVITS/prepare_datasets/3-get-semantic.py

echo "=== [5/6] SoVITS 微调 ==="
$PY - <<'PYEOF'
import json
with open("GPT_SoVITS/configs/s2.json") as f:
    data = json.load(f)
data["train"].update({
    "batch_size": 12, "epochs": 4, "text_low_lr_rate": 0.4,
    "pretrained_s2G": "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth",
    "pretrained_s2D": "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2D2333k.pth",
    "if_save_latest": True, "if_save_every_weights": True, "save_every_epoch": 4,
    "gpu_numbers": "0", "grad_ckpt": False, "lora_rank": "16",
})
data["model"]["version"] = "v2"
data["data"]["exp_dir"] = data["s2_ckpt_dir"] = "output/tangyuan"
data["save_weight_dir"] = "SoVITS_weights_v2"
data["name"] = "tangyuan"
data["version"] = "v2"
with open("tmp_s2.json", "w") as f:
    json.dump(data, f)
print("s2 config written")
PYEOF
$PY -s GPT_SoVITS/s2_train.py --config tmp_s2.json 2>&1 | tail -5

echo "=== [6/6] GPT 微调 ==="
$PY - <<'PYEOF'
import yaml
with open("GPT_SoVITS/configs/s1longer-v2.yaml", encoding="utf-8") as f:
    data = yaml.safe_load(f)
data["train"].update({
    "batch_size": 6, "epochs": 8, "precision": "16",
    "pretrained_s1": "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
    "save_every_n_epoch": 8, "if_save_every_weights": True, "if_save_latest": True,
    "if_dpo": False, "exp_name": "tangyuan",
    "half_weights_save_dir": "GPT_weights_v2",
})
data["train_semantic_path"] = "output/tangyuan/6-name2semantic.tsv"
data["train_phoneme_path"] = "output/tangyuan/2-name2text.txt"
data["output_dir"] = "output/tangyuan/logs_s1"
with open("tmp_t2s.yaml", "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True)
print("t2s config written")
PYEOF
$PY -s GPT_SoVITS/s1_train.py --config_file tmp_t2s.yaml 2>&1 | tail -5

echo "=== 完成，产物 ==="
ls -la SoVITS_weights_v2/ GPT_weights_v2/ 2>/dev/null
echo "PIPELINE_ALL_DONE"
