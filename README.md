# 大肥鱼 QQ 机器人

一个跑在 QQ 群里的陪聊机器人：固定人设、按情绪发语音和表情、会唱歌、会点歌、
记个人档案记忆、管群、发 QQ 空间说说和日记，带一个本地控制台。

v2 架构：**一个插件、一层公共 core、一堆互不认识的模块。**
加功能只新建一个模块文件，删功能只删一个文件，不用动入口。

## 功能

- 聊天：人设、群上下文、个人档案记忆、联网搜索
- 听语音：收到的语音先用本机 faster-whisper 转成文字；主模型不认音频也不会整条消息没回复
- 档案：聊够一定量或每隔半小时自动整理，群/好友详情也能一键整理
- 接话：群消息都看得到，模型按冷场、兴趣和话题自己决定要不要插话，不是每句都回
- 语音：GPT-SoVITS 按情绪合成，随机器人启动，失败回落 edge-tts
- 唱歌：RVC 换声，后台跑，不阻塞聊天
- 表情：按情绪从文件夹随机发
- 音乐：点歌、歌词、收藏
- 群管：禁言、踢人、违禁词（社区插件 qqadmin）
- 好友与名单：黑白名单守门、热加载，白名单外的人和群都不理，新好友新群要手动加白
- 认人：每个聊过的人一份档案，群里说话会标明是谁，禁言踢人先对号
- QQ 空间：AI 说说、草稿、每日日记
- 维护模式：插件直接回维护提示，重启或调试时也能回话
- 控制台：总览只留连接、插件、Token、健康；模块、备份、维护、体检都在「管理」页；右上角能直接开 AstrBot 后台
- 备份：每天 03:00 自动备一次，管理页也能一键手动备份；资料卡、会话、名单、音色状态都在里面

## 架构

```
engines/v2/plugin/
├─ main.py              固定入口：建内核、挂三个钩子
├─ dafeiyu_core/        路径、配置、OneBot、消息流、音频、模块注册、内核
└─ dafeiyu_modules/     一个文件一个功能
panel/                  控制台
scripts/                运维脚本
patches/                AstrBot 4.29.1 核心补丁
docs/                   架构、配置、测试清单
```

模块契约见 [ARCHITECTURE.md](ARCHITECTURE.md)，每个模块的作用和配置见 [docs/MODULES.md](docs/MODULES.md)，整个运行目录见 [docs/DIRECTORY.md](docs/DIRECTORY.md)，配置见 [docs/CONFIG.md](docs/CONFIG.md)。

## 快速开始

完整步骤看 [INSTALL.md](INSTALL.md)；让 AI 助手照着搭看 [docs/AI_SETUP.md](docs/AI_SETUP.md)。
最短路径：

```powershell
git clone https://github.com/void-HaoNan/dafeiyu-qqbot.git
cd dafeiyu-qqbot
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Root D:\QQBot -ApplyPatches
```

然后：

1. 填 `D:\QQBot\data\dafeiyu\config.json` 里的管理员 QQ、机器人 QQ、主群号
2. 在 AstrBot 6185 配模型 key，建人格「大肥鱼」（或跑 `scripts\import_persona.py`）
3. `powershell -ExecutionPolicy Bypass -File D:\QQBot\start.ps1`
4. 跑 `scripts\doctor.py` 体检，再按 `docs/V2_TEST.md` 一条条人工验收

## 维护模式

控制台总览里打开「维护」：AstrBot 停止所有回复（包括管理员），维护提示由插件自己直接回，
不经过模型。开和关都会私聊管理员提醒一次。

手动开关：

```powershell
python scripts\maintenance.py on
```

## 测试

```powershell
python tests\smoke.py
python scripts\doctor.py
```

功能验收按 `docs/V2_TEST.md` 手动一条条过。

## 依赖

AstrBot、SnowLuma（或 NapCat）、GPT-SoVITS、RVC、ffmpeg、faster-whisper（语音识别）。
模型、密钥、QQ 号、表情包、音色模型需要自己准备，详见 [THIRD_PARTY.md](THIRD_PARTY.md)。

## 许可

AGPL-3.0，见 [LICENSE](LICENSE)。
