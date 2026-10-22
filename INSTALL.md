# 安装

## 需要自己准备

- Windows 10/11，16G 内存
- 机器人 QQ、管理员 QQ
- AstrBot 4.29.1、OneBot v11 协议端（SnowLuma / NapCat）
- 对话模型 key、视觉模型 key
- GPT-SoVITS 程序放 D:\QQBot\tools\GPT-SoVITS，音色模型和参考音频放 D:\QQBot\models\tts（可选，没有就回落 edge-tts）
- faster-whisper（收语音转文字，约 460MB 模型；不装也能跑，语音会回「没听清」）
- RVC 唱歌模型（可选）
- 表情包、ffmpeg

## 目录

```
D:\QQBot\
├─ AstrBot\                  官方 AstrBot
├─ SnowLuma\                 协议端
├─ panel\                    控制台
├─ scripts\                  运维脚本
├─ personas\大肥鱼.txt        人设
├─ models\asr\                语音识别模型
├─ data\dafeiyu\config.json  统一配置
├─ backups\                  每日备份 zip
├─ backup.ps1 / backup.vbs   备份脚本和计划任务入口
├─ stickers\<情绪>\           表情包
├─ datasets\<角色>_emotion\   语音参考
├─ bin\ffmpeg.exe
└─ rvc\                      RVC
```

## 一键安装

AstrBot 装好之后，在仓库目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Root D:\QQBot -ApplyPatches
```

它会拷插件、控制台、脚本、备份脚本、人设、补丁，建好目录，生成配置和空名单。
装完按下面「步骤」里的 5~8 填配置、建人格、启动、体检。
装在别的盘就改 `-Root`；让 AI 助手照着搭看 [docs/AI_SETUP.md](docs/AI_SETUP.md)。

## 步骤

1. 按 AstrBot 官方文档装好，确认能收发消息。
2. 复制插件：

```powershell
copy D:\dafeiyu-qqbot\engines\v2\plugin D:\QQBot\AstrBot\data\plugins\astrbot_plugin_dafeiyu /E
```

3. 复制控制台和脚本：

```powershell
copy D:\dafeiyu-qqbot\panel D:\QQBot\panel /E
copy D:\dafeiyu-qqbot\scripts D:\QQBot\scripts /E
copy D:\dafeiyu-qqbot\personas D:\QQBot\personas /E
```

4. 打 AstrBot 核心补丁（白名单热加载、群图描述）：

```powershell
powershell -ExecutionPolicy Bypass -File D:\dafeiyu-qqbot\patches\apply_core_patches.ps1
```

5. 配置：把 `config-examples\dafeiyu.config.example.json` 复制成
`D:\QQBot\data\dafeiyu\config.json`，填管理员 QQ 和机器人 QQ。
模块开关都在这个文件里，改完即时生效。

6. 建人格：AstrBot 6185 → 人格 → 新建「大肥鱼」，粘贴 `personas\大肥鱼.txt`。
或者 AstrBot 跑过一次后执行：

```powershell
D:\QQBot\AstrBot\.venv\Scripts\python.exe D:\QQBot\scripts\import_persona.py
```

7. 启动：

```powershell
powershell -ExecutionPolicy Bypass -File D:\dafeiyu-qqbot\start.ps1 -Root D:\QQBot
```

8. 体检，再打开控制台（`D:\QQBot\panel\panel.vbs`，127.0.0.1:6868）：

```powershell
D:\QQBot\AstrBot\.venv\Scripts\python.exe D:\QQBot\scripts\doctor.py
```

然后按 `docs/V2_TEST.md` 一条条人工验收。

## 语音识别

收语音要能听懂，装一次依赖和模型（只下一次，之后离线跑）：

```powershell
D:\QQBot\AstrBot\.venv\Scripts\python.exe -m pip install faster-whisper
D:\QQBot\AstrBot\.venv\Scripts\python.exe D:\QQBot\scripts\fetch_asr_model.py
```

不装也能启动：语音消息会被换成「[语音] 没听清」，不会再因为主模型不认音频而整条没回复。
装完重启 AstrBot，发一条语音，日志里出现 `[asr] 听到：…` 就是通了。

## 备份

每天 03:00 自动备一次，装的时候加 `-InstallBackupTask` 就会建计划任务：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Root D:\QQBot -ApplyPatches -InstallBackupTask
```

备份包含 AstrBot 数据、`data`（资料卡、名单、token 记录）、SnowLuma 配置、密钥、人设、
音色状态和 RVC 映射，存在 `D:\QQBot\backups\qqbot-*.zip`，保留最近 14 份。

控制台总览有「备份」卡，点「立即备份」就能手动跑一次；点「查看备份」或打开
`http://127.0.0.1:6868/backups` 可以看和下载。

恢复：先停 AstrBot 和控制台，把 zip 里的 `astrbot-data`、`data`、`panel` 等目录
覆盖回 `D:\QQBot` 对应位置，再启动。

## 维护模式

控制台总览里打开「维护」：AstrBot 停止所有回复（包括管理员），维护提示由插件自己回，
不经过模型。

手动开关：

```powershell
python D:\QQBot\scripts\maintenance.py on
```

## 常见问题

- 机器人不回：协议端反向 WS 有没有连上 6199
- 名单不生效：`patches` 里的 `stage.py` 补丁没打
- 群图没描述：`group_chat_context.py` 补丁没打
- 语音是电子音：GPT-SoVITS 没起，回落 edge-tts
- 发语音没反应：看日志有没有 `[asr]`；没装 faster-whisper 或模型没下，先跑上面的「语音识别」
- 唱歌没声：RVC 路径和 `rvc\model_map.json` 没配
