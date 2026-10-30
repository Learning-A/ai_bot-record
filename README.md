# QQ AI Bot · 全景地图

> 一个跑在 Windows 上的自托管 QQ AI 伴侣：三人格情感语音（IndexTTS-2.5 + GPT-SoVITS 双引擎）、四层记忆、表情包节奏镜像、主动聊天与情绪引擎、四层安全护栏——日常对话成本锁在 **¥1/天** 以内。

本项目是完整的实战记录：**8 个自研 AstrBot 插件 + 全套运维脚本 + 音色训练流水线 + 38 条踩坑复盘**。所有密钥与个人信息已脱敏。

---

## 架构总览

```
┌─ 腾讯QQ ── 机器人QQ小号（NapCat 注入 QQ NT，OneBot11 反向WS）────────┐
├─ 框架层 AstrBot（WebUI :6185 / OneBot WS :6199，一切皆数据）─────────┤
├─ 模型层（各司其职）                                                    │
│   DeepSeek 对话大脑（¥1/天限额）｜bge-m3 向量·免费                     │
│   GLM-4V-Flash 视觉·免费｜Tavily 搜索·免费                            │
├─ 自研插件 ×8（plugins/）                                               │
│   core_memory   L4核心记忆（AI自编辑+按人隔离）                         │
│   quota_guard   ¥1/天花费限额（跨重启记账）                            │
│   style_switcher 每人独立切人格（中文别名+会话级）                      │
│   qq_face_eyes  QQ表情四级理解（官方表→faceText→视觉→可教学）          │
│   meme_rhythm   表情包节奏镜像（按人统计浓度→对标注入+口味画像）        │
│   voice_on_demand 按需情感语音（人格×情绪路由 IndexTTS/GSV/云端三级）  │
│   safety_guard  安全护栏（消毒+铁律+工具剥离+限流 四层纵深）            │
│   dafeiyu_live  直播助手（已下线，代码保留）                            │
├─ 社区插件 ×4（market 安装：LivingMemory / meme_manager /               │
│   proactive_chat / self_learning）                                     │
└─ 语音推理层（可选 GPU 服务器）                                          │
    主引擎 IndexTTS-2.5（情感-音色解耦）→ 回退 GPT-SoVITS 微调            │
    → 本地CPU → SiliconFlow 云端；SSH隧道+看门狗自动重连                   │
```

## 核心特性

| 特性 | 一句话 |
|---|---|
| 三人格情感语音 | 泡泡/汤圆/小鱼干各有专训音色；LLM 按 mood 打标 → IndexTTS 情感参考注入语调 |
| 音色训练流水线 | B站音源 → 评估四指标（成句率/呓语/语速/BGM）→ 切片 → whisper 标注 → SoVITS 微调，~1h/音色 |
| 四层记忆 | 会话24轮 + 情景TTL7天 + 语义180天 + 核心记忆永不遗忘，按聊天对象隔离 |
| 表情三向闭环 | 看得懂（四级翻译）→ 发得出（语义检索+节奏镜像）→ 收藏回抛（口味画像） |
| 主动与情绪 | 冷场主动搭话（带失落语气）；好感度+每日情绪注入每轮回复 |
| 安全护栏 | 伪装标记转义、安全铁律、非主人剥离花钱工具（架构级）、限流审计 `/安全报告` |

## 仓库地图

```
├── README.md                 ← 本文件（全景地图）
├── docs/
│   ├── QQ机器人使用手册.md    ← 使用与运维手册（功能/命令/FAQ）
│   └── 技术复盘.md           ← 14阶段演进史 + 38条踩坑全记录（精华）
├── plugins/                  ← 8个自研插件（放入 astrbot/data/plugins/）
├── scripts/                  ← 启动/停止/隧道/看门狗脚本（Windows bat + ps1）
├── tools/
│   ├── 克隆音色.py           ← B站视频→参考音频→云端克隆→质检 流水线
│   └── gpu_finetune_pipeline.sh ← GPU服务器无头微调流水线（切片→ASR→特征→SoVITS训练）
└── config-examples/
    └── cmd_config.example.json ← AstrBot 主配置结构示例（值已脱敏）
```

## 快速上手（三步）

1. **基础链路**：安装 [NapCat](https://github.com/NapNeko/NapCatQQ) + [AstrBot](https://github.com/AstrBotDevs/AstrBot)，扫码登录机器人QQ小号，反向WS对接（`scripts/启动QQ机器人.bat` 参考）
2. **填配置**：按 `config-examples/cmd_config.example.json` 填 DeepSeek/SiliconFlow/智谱 Key 与你的QQ号；把 `plugins/` 拷入 AstrBot 插件目录并在 WebUI 启用
3. **（可选）情感语音**：按 `docs/技术复盘.md` v10~v13 章节部署 GPT-SoVITS/IndexTTS（无 GPU 可只用云端 TTS，全链路自动降级）

## 安全设计（为什么值得看）

- **最小权限是主防线**：花钱工具（生图/视频）对非主人会话**直接从 LLM 请求中剥离**——模型不可见即不可调用，不依赖"提示词劝阻"
- 注入检测离线测试：5 种攻击载荷全拦截、正常消息零误伤
- 详见 `plugins/safety_guard/` 与复盘坑 #38

## 致谢（社区插件）

- [astrbot_plugin_livingmemory](https://github.com/LoSealL/AstrBot_Plugin_LivingMemory)（记忆）
- [astrbot_plugin_meme_manager](https://github.com/anka-202/astrbot_plugin_meme_manager)（表情包）
- [astrbot_plugin_proactive_chat](https://github.com/svtter/astrbot-plugin-proactive-chat)（主动消息）
- [astrbot_plugin_self_learning](https://github.com/raymenthwy/AstrBot_plugin_self_learning)（情绪/好感度）
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) / [IndexTTS](https://github.com/index-tts/index-tts)（语音引擎）

## License

MIT
