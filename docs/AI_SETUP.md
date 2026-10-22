# 让 AI 助手（DSH 等）从零搭起来

这份文件是给 AI 助手看的施工单：按阶段做，每阶段验证通过再进下一阶段。
人类照着做也可以，只是会啰嗦一点。

约定：

- `<root>` 是安装根目录，默认 `D:\QQBot`。想装别处就设环境变量 `QQBOT_ROOT`，
  或给 `install.ps1` 传 `-Root`。
- 所有命令在 PowerShell 5.1 里执行。
- 仓库里不存任何 QQ 号、key、cookie；这些只写进本机 `data\`。

## 阶段 0：先问清楚

动手前向用户确认这几项，缺一项就停下来问：

1. 管理员 QQ、机器人 QQ、主群号
2. 安装根目录（默认 `D:\QQBot`）
3. 协议端用 SnowLuma 还是 NapCat（本仓库默认按 SnowLuma 写）
4. 对话模型和视觉模型的 key（DeepSeek / 智谱等）
5. 要不要语音、唱歌（不要就跳过 GPT-SoVITS 和 RVC）

## 阶段 1：装 AstrBot 和协议端

目标：`<root>\AstrBot` 能启动，`<root>\SnowLuma` 能登录机器人 QQ。

1. AstrBot 4.29.1：按官方文档装到 `<root>\AstrBot`，确认
   `<root>\AstrBot\.venv\Scripts\python.exe main.py` 能起来，6185 面板能打开。
2. 协议端 SnowLuma 放到 `<root>\SnowLuma`，登录机器人 QQ。
   OneBot 配置：HTTP 3000、反向 WS 6199。
   配置文件在 `<root>\SnowLuma\config\onebot_<机器人QQ>.json`，
   控制台靠它读 `accessToken`。
3. 把控制台依赖装进 AstrBot 的 venv：

```powershell
<root>\AstrBot\.venv\Scripts\python.exe -m pip install -r <仓库>\requirements.txt
```

## 阶段 2：装本项目

```powershell
git clone https://github.com/void-HaoNan/dafeiyu-qqbot.git
cd dafeiyu-qqbot
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Root D:\QQBot -ApplyPatches
```

`install.ps1` 会：

- 把插件拷到 `<root>\AstrBot\data\plugins\astrbot_plugin_dafeiyu`
- 把 `panel`、`scripts`、`personas`、`patches` 拷到 `<root>`
- 把 `backup.ps1`、`backup.vbs` 拷到 `<root>`；加 `-InstallBackupTask` 时建每天 03:00 的备份任务
- 建好 `data\dafeiyu`、`logs`、`stickers`、`rvc\models` 等目录
- 首次生成 `data\dafeiyu\config.json` 和 `data\access_lists.json`
- 加 `-ApplyPatches` 时给 AstrBot 4.29.1 打两个核心补丁

验证：`<root>\AstrBot\data\plugins\astrbot_plugin_dafeiyu\main.py` 存在。

## 阶段 3：填配置

1. `<root>\data\dafeiyu\config.json`：填 `admin_qq`、`bot_qq`、`group`。
   `paths` 可以留空，控制台会自己探测；也可以填死。
2. 模型：把 `config-examples\cmd_config.snippet.json` 里的 provider 段合并进
   `<root>\AstrBot\data\cmd_config.json`，或者在 6185 面板里配。
   key 只留在本机，不要提交。
3. 名单：`data\access_lists.json` 默认白名单是空的，机器人会谁都不理。
   启动控制台后，在「好友」页把管理员、主群加进白名单。

## 阶段 4：人设

先启动一次 AstrBot，让它生成 `<root>\AstrBot\data\data_v4.db`，然后：

```powershell
<root>\AstrBot\.venv\Scripts\python.exe <root>\scripts\import_persona.py
```

## 阶段 5：启动

```powershell
powershell -ExecutionPolicy Bypass -File <root>\start.ps1
```

或者手动：协议端 → AstrBot → `<root>\panel\panel.vbs`。

验证三个地址：

- OneBot HTTP `127.0.0.1:3000`
- AstrBot 面板 `127.0.0.1:6185`
- 控制台 `127.0.0.1:6868`

## 阶段 6：体检和验收

```powershell
<root>\AstrBot\.venv\Scripts\python.exe <root>\scripts\doctor.py
```

`doctor.py` 会检查 AstrBot 版本、插件、补丁、配置、人设、端口、名单。
有 FAIL 先修，再按 `docs\V2_TEST.md` 一条条人工验收。

## 阶段 7：可选能力

- 备份：装的时候加 `-InstallBackupTask`，每天 03:00 自动备；控制台「管理」页也能手动点。
  备份范围、恢复方式见 [CONFIG.md](CONFIG.md) 的「备份」。
- 收语音转文字：`requirements.txt` 已带 faster-whisper，再下一次模型
  （约 460MB，之后离线跑）：

```powershell
<root>\AstrBot\.venv\Scripts\python.exe <root>\scripts\fetch_asr_model.py
```

  不装也能跑，语音会回「没听清」，不会整条没回复。
- 语音：GPT-SoVITS 放 `<root>\tools\GPT-SoVITS`，模型放 `<root>\models\tts`，
  控制台「声音」页选音色。机器人启动时会自动把语音服务一起拉起来，不用单独开机自启。
- 唱歌：RVC 放 `<root>\rvc`，配 `rvc\model_map.json`。
- 表情：往 `<root>\stickers\<情绪>\` 放图片。
- 网易云：`scripts\netease.py` 登录后控制台「音乐」页能用。
- QQ 空间：装社区插件 `astrbot_plugin_qzone`。

## 常见坑

- **PowerShell 脚本乱码**：仓库里的 `.ps1` 都是 UTF-8 带 BOM，别用记事本另存成 ANSI。
- **补丁被覆盖**：升级 AstrBot 后要重新 `-ApplyPatches`，版本不是 4.29.1 先停。
- **机器人不理人**：先看白名单是不是空的；再看协议端反向 WS 有没有连上 6199。
- **控制台打不开**：`panel.vbs` 按自身位置找根目录，确认 `<root>\AstrBot\.venv` 存在。
- **读不到 OneBot token**：确认 `SnowLuma\config\onebot_*.json` 在，或设 `SNOWLUMA_CONFIG`。
- **不要提交**：`data\`、`logs\`、`backups\`、`secrets\`、任何 key 和 cookie。
