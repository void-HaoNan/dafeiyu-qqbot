# 第三方组件与社区插件

本仓库只包含自己写的代码（AGPL-3.0）。下面这些不在仓库里，需要自己装。

## 核心组件

| 组件 | 版本 | 许可证 | 地址 |
|---|---|---|---|
| AstrBot | 4.29.1 | AGPL-3.0 | https://github.com/AstrBotDevs/AstrBot |
| SnowLuma | 2.0.3（最新 2.1.1） | 自定义（Other，只发二进制） | https://github.com/SnowLuma/SnowLuma |
| NapCat | 最新 | 见仓库 | https://github.com/NapNeko/NapCatQQ |
| Lagrange | 最新 | 见仓库 | https://github.com/LagrangeDev/Lagrange.Core |
| GPT-SoVITS | 最新 | MIT | https://github.com/RVC-Boss/GPT-SoVITS |
| RVC | 最新 | MIT | https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI |
| demucs | 4.x | MIT | https://github.com/facebookresearch/demucs |
| yt-dlp | 最新 | Unlicense | https://github.com/yt-dlp/yt-dlp |
| faster-whisper | 1.2.1 | MIT | https://github.com/SYSTRAN/faster-whisper |
| faster-whisper-small | 最新 | MIT | https://huggingface.co/Systran/faster-whisper-small |
| ffmpeg | 最新 | LGPL/GPL | https://www.gyan.dev/ffmpeg/builds/ |

## 社区插件（原项目在用的版本）

| 插件 | 版本 | 许可证 | 地址 |
|---|---|---|---|
| QQ群管 | v3.4.2 | GPL-3.0 | https://github.com/Zhalslar/astrbot_plugin_qqadmin |
| QQ空间 | v3.1.3 | GPL-3.0 | https://github.com/Zhalslar/astrbot_plugin_qzone |
| 点歌 | v2.2.0 | MIT | https://github.com/Zhalslar/astrbot_plugin_music |
| 专业戳一戳 | v2.1.1 | 见仓库 | https://github.com/Zhalslar/astrbot_plugin_pokepro |
| 主动消息 | v1.2.6 | 见仓库 | https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat |
| 更好的帮助 | v1.2.8 | 见仓库 | https://github.com/tinkerbellqwq/astrbot_plugin_help |
| LLM 遗忘 | v1.1.6 | 见仓库 | https://github.com/SinkAbyss/astrbot_plugin_llm_amnesia |

从 AstrBot 插件市场装，或者 `git clone` 到 `AstrBot\data\plugins\`。
版本不用完全一致，但大版本差太多可能要改配置。

点歌插件本地改过两处：`play_song_by_name` 只放歌、不自动收藏；`collect_song` 一次只收一首，
同一轮重复调用会被拒绝。更新或重装点歌插件后要重新打这两处，不然会退回旧行为。
控制台启动时会跑 `scripts/patch_guard.py` 自检，缺了会私聊提醒管理员。

## 不提供的资源

- 音色模型（洛茜等）、RVC 唱歌模型：自己训练或找授权模型
- 表情包：自己收集，注意版权
- 聊天记录、记忆库、QQ 号、API key、cookie：隐私，不提供
