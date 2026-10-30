# avatar/ · 形象与互动核心

平台无关的虚拟形象系统本体。部署方法见仓库根 README 与 `docs/部署与接口指南.md`。

## 文件

| 文件 | 说明 |
|---|---|
| `danmaku_hub.py` | 核心：静态服务(:8642) + 事件总线(:8643) + 关键词引擎 |
| `danmaku_bilibili.py` | B 站弹幕适配器（独立进程，可替换为其他平台） |
| `index.html` | 形象页（推流软件以浏览器源或色键方式挂载） |
| `console.html` | 主播监控台（同步形象 + 按钮 + 弹幕流） |
| `anims_index.js` | 106 个动作清单 |
| `download_anims.py` | 素材补拉脚本（assets/anims 误删时重跑） |
| `cover.html` | 直播封面生成页（改文案后浏览器截图即得封面） |
| `assets/anims/` | 106 个透明 WebM 动画 |

## 形象页 URL 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `scale` | 1 | 视觉缩放 0.3~3 |
| `interval` | 40 | 自动表演间隔秒数（0 = 关） |
| `bubble` | 1 | 台词气泡 0/1 |
| `roam` | 0 | 1 = 在源范围内游动（源设 1920×1080） |
| `pool` | all | all=全部 / classic=精选 / 逗号分隔自定义 |
| `bg` | 无 | 色键模式：`green`/`black`/任意 CSS 颜色（无浏览器源的软件用） |

## 关键词/冷却配置

编辑 `danmaku_hub.py` 顶部的 `ALIASES`（别名→动作池）与 `COOLDOWN_SEC`（观众触发冷却秒数），重启核心生效。主播监控台按钮不受冷却限制。
