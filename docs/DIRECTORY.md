# QQBOT 目录说明

`D:\QQBot` 是**运行根目录**，不是源码仓库。源码仓库在 `D:\dafeiyu-qqbot`，
`install.ps1` 会把插件、面板、脚本拷到 `D:\QQBot`，运行数据只写在这里。

## 一页看懂

| 位置 | 存什么 | 重要性 |
|---|---|---|
| `AstrBot\` | AstrBot 框架、Python 环境、会话库、插件、配置 | 核心，别乱动 |
| `SnowLuma\` | OneBot 协议端、QQ 登录配置、QQ 缓存 | 核心，别乱动 |
| `panel\` | 自定义控制台（后端 + 页面 + 状态） | 核心，改完要重启面板 |
| `data\` | 大肥鱼自己的数据：名单、档案、token、空间、网易云 | 必须备份 |
| `logs\` | 运行日志、消息流、语音临时文件、备份日志 | 可清理，排错要看 |
| `backups\` | 每日备份 zip | 默认保留 14 份，管理页可改 |
| `scripts\` | 运维脚本：体检、备份下载、唱歌、空间、迁移 | 从仓库同步 |
| `patches\` | AstrBot 4.29.1 核心补丁 | 升级后要重打 |
| `personas\` | 大肥鱼人设提示词和历史版本 | 必须备份 |
| `models\` | 语音模型和语音识别模型 | 大，单独备份 |
| `rvc\` | RVC 唱歌运行时和模型 | 大，单独备份 |
| `tools\` | GPT-SoVITS 程序 | 大，单独备份 |
| `stickers\` | 表情包，按情绪分文件夹 | 可选备份 |
| `bin\` | ffmpeg / ffprobe | 可重新下载 |
| `secrets\` | API key、OneBot token | 必须备份，别外传 |
| `disabled_plugins\` | 停用的 AstrBot 插件 | 想恢复时用 |

旧的 `engines`、`freemote`、`refs`、`tmp`、`downloads`、`stickers.zip` 已经清理，
归档在 `logs\backups\legacy_before_clean_*.zip`，需要时可以从那里找回。

## 启动和运维文件

- `启动机器人.bat`：双击启动，实际调 `start.ps1`
- `start.ps1`：按顺序检查 QQ、SnowLuma、AstrBot，GPT-SoVITS 没起就一起拉起来
- `start_sovits.ps1`：单独启动 GPT-SoVITS 9880
- `stop.ps1`：只停 AstrBot 和 SnowLuma，不动 QQ
- `watchdog.ps1` / `watchdog.vbs`：Windows 计划任务每 5 分钟看门狗
- `backup.ps1` / `backup.vbs`：每日 03:00 备份，`backup.vbs` 是计划任务入口
- `restart_prompt.ps1`：重启前给管理员发提醒的小脚本
- `面板.bat`：启动控制台并打开 6868

## AstrBot

```
AstrBot\
├─ astrbot\            框架源码（含三个核心补丁）
├─ dashboard\          WebUI 前端资源
├─ .venv\              Python 3.12 虚拟环境，依赖都在这里
├─ data\
│  ├─ cmd_config.json  全局配置：模型、白名单、平台设置
│  ├─ data_v4.db       会话历史、人格、token 统计（SQLite）
│  ├─ config\          各插件配置；大肥鱼插件这里只剩一条“说明”
│  ├─ plugins\         插件目录，含 astrbot_plugin_dafeiyu 和社区插件
│  ├─ plugin_data\     插件自己的素材/缓存
│  ├─ knowledge_base\  知识库
│  ├─ attachments\     消息附件
│  ├─ webchat\         WebUI 聊天记录
│  ├─ logs\            AstrBot 自己的日志
│  └─ temp\            临时媒体文件
├─ main.py             启动入口
└─ pyproject.toml      版本 4.29.1
```

`data_v4.db` 里最重要的是 `conversations`（聊天历史）和 `personas`（人格）。
不要在 AstrBot 运行时直接改这个库；要改先备份并停 AstrBot。

## SnowLuma

```
SnowLuma\
├─ node.exe            Node 运行时
├─ index.mjs           协议端主程序
├─ config\             OneBot 配置，token 在这里
├─ data\1834840689\    QQ 账号缓存、头像、表情
├─ client\ native\     协议端自己的依赖
└─ logs\               协议端日志
```

`config\onebot_1834840689.json` 是机器人 QQ 的 OneBot 配置：
HTTP 3000、反向 WS 6199、access token。控制台发消息、查好友都靠它。

## 控制台 panel

```
panel\
├─ app.py              后端，FastAPI，127.0.0.1:6868
├─ index.html          前端，单页，所有页面都在这
├─ panel.vbs           用 pythonw 静默启动
├─ model_state.json    当前音色（洛茜的 ckpt/pth/参考音频）
├─ rvc_state.json      当前唱歌模型
├─ backup_state.json   手动备份进度和结果
├─ log_seen.json       日志红点已读位置
└─ sing_test\          唱歌试听生成的文件
```

控制台只监听本机。页面里的「管理」是模块、插件、工具、备份、维护、体检、名单和 AstrBot 入口；
「设置」只放常用开关和路径；「总览」是连接、插件、上下文、Token 和健康；「好友」里改白名单/黑名单。

## data：大肥鱼的数据

```
data\
├─ access_lists.json       黑白名单（白名单外的人和群不理）
├─ dafeiyu\
│  ├─ config.json          统一配置：模块开关、voice、asr、sendto、paths
│  ├─ profiles.json        人物档案：昵称、关系、喜好、印象；旧的 tokens 字段不再用于显示
│  ├─ tokens.db            唯一 token 账本：私聊、群、群成员都从这里算
│  ├─ summary_state.json   自动整理进度
│  ├─ summary_requests.json 一键整理请求队列
│  └─ group_alerts.json    新群提醒去重
├─ qzone_ai_state.json     空间说说/日记状态
├─ qzone_drafts.json       空间草稿
├─ netease_session.json    网易云登录 cookie
├─ netease_target.json     网易云收藏歌单
├─ music_collected.jsonl   点歌收藏记录
├─ persona_history.jsonl   人设历史版本
├─ persona_test_backup.txt 人设测试备份
├─ new_friends.json        新好友提醒
└─ friend_snapshot.json    好友快照，用来发现新好友
```

`profiles.json` 就是控制台里好友/群“资料卡”的数据源。每日备份 zip 里的
`data\dafeiyu\profiles.json` 就是它。

## logs：排错先看这里

```
logs\
├─ astrbot.out.log / err.log         AstrBot 当前日志
├─ *.prev.log                        上一次启动的日志
├─ snowluma.out.log / err.log        协议端日志
├─ sovits.out.log / err.log          GPT-SoVITS 日志
├─ panel.console.log                 控制台输出
├─ watchdog.log                      看门狗记录
├─ backup.log                        每次备份开始/完成/失败
├─ panel_stream.jsonl                所有收发消息，控制台消息流和自动记忆用
├─ voice\                            发出去的语音 wav/mp3
├─ asr\                              语音转文字的临时 wav
├─ panel_images\                     控制台图片缓存
├─ backups\                          手工/专项备份
├─ deleted_plugins\                  删掉的插件打包
├─ core_patch_backups\               核心补丁备份
└─ legacy 归档                        旧调试脚本/截图在 backups 里
```

`logs` 现在只保留运行日志、消息流和最新两张检查截图；旧 `bench_*`、`test_*`、
截图已经归档进 `logs\backups\legacy_before_clean_*.zip`。

## backups：备份

```
backups\
├─ qqbot-日期-时间.zip     每日/手动备份，默认保留 14 份，管理页可改
├─ qqbot-deploy-*.zip      部署包（不参与自动清理）
└─ dafeiyu-legacy-v1.bundle 旧 v1 仓库备份
```

每日 03:00 由 `QQBot-DailyBackup` 计划任务跑 `backup.ps1`。控制台「管理」页的
「备份」卡可以手动跑，`/backups` 页面可以看和下载。zip 里有密钥和 cookie，别外传。

## 模型和素材

```
models\
├─ tts\洛茜\                 GPT-SoVITS 权重（ckpt/pth）和参考音频
└─ asr\faster-whisper-small\ 语音转文字模型

rvc\
├─ pkg\RVC20260718Nvidia\   RVC 运行时
├─ models\                  唱歌模型（luotianyi/miku/tomori/uzuki 等）
├─ model_map.json           歌手/语种到模型映射
├─ out\                     唱歌输出
└─ RVC20260718Nvidia.7z     7.8GB 安装包，装好后可归档

tools\GPT-SoVITS\            GPT-SoVITS 程序本体和 Python 环境
stickers\<情绪>\             表情包，happy 242 张、confused 39 张等
bin\ffmpeg.exe / ffprobe.exe 音视频转换
secrets\deepseek_key.txt / onebot_token.txt 密钥
```

## 已经清理的遗留

以前根目录的 `engines`、`freemote`、`refs`、`tmp`、`downloads`、`stickers.zip`
已经归档并删除，归档在：

`logs\backups\legacy_before_clean_20261023_221750.zip`

里面包含旧引擎、FreeMote、参考项目、调试脚本、旧表情压缩包和旧日志；
需要找回旧文件时从 zip 解压。`logs\backups\legacy_logs_extra_*.zip` 是剩下的小日志。

## 不要动

- `AstrBot\astrbot\` 和 `.venv\`：升级会覆盖，改核心要走 `patches\`
- `SnowLuma\node.exe`、`index.mjs`：协议端本体
- `data\`、`profiles.json`、`access_lists.json`：先备份再改
- `secrets\`：不要提交、不要发人
- `models\`、`rvc\pkg\`：大文件，别随手删

## 备份和恢复

每日 zip 解压后对应关系：

| zip 里的目录 | 放回 |
|---|---|
| `astrbot-data\` | `D:\QQBot\AstrBot\data\` |
| `data\` | `D:\QQBot\data\` |
| `snowluma-config\` | `D:\QQBot\SnowLuma\config\` |
| `secrets\` | `D:\QQBot\secrets\` |
| `personas\` | `D:\QQBot\personas\` |
| `panel\` | `D:\QQBot\panel\` |
| `rvc\model_map.json` | `D:\QQBot\rvc\model_map.json` |

恢复前先停 AstrBot 和控制台；恢复 `data_v4.db` 时连 `-wal`/`-shm` 一起放回去。
