# 配置

所有模块共用一份配置：`data/dafeiyu/config.json`。维护模式每条消息检查修改时间、即时生效；其他模块改完要重启 AstrBot。

```json
{
  "admin_qq": "管理员QQ",
  "bot_qq": "机器人QQ",
  "group": "主群号",
  "paths": {
    "tts_root": "D:\\GPT-SoVITS\\models",
    "rvc_root": "D:\\QQBot\\rvc",
    "rvc_pkg": "D:\\QQBot\\rvc\\pkg\\RVC20260718Nvidia",
    "model_map": "D:\\QQBot\\rvc\\model_map.json",
    "ffmpeg": "D:\\QQBot\\bin\\ffmpeg.exe"
  },
  "modules": {
    "voice": {
      "enabled": true,
      "voice_enable": true,
      "max_chars": 150,
      "use_gpt_sovits": true,
      "gsv_url": "http://127.0.0.1:9880"
    },
    "asr": {
      "enabled": true,
      "model_dir": "D:\\QQBot\\models\\asr\\faster-whisper-small",
      "language": "zh",
      "cpu_threads": 8,
      "auto_download": true
    },
    "sendto": {"enabled": true, "default_group": "", "cooldown_seconds": 5, "max_per_minute": 6},
    "maintenance": {
      "enabled": false,
      "message": "唔，本鱼正在升级……",
      "cooldown_seconds": 120,
      "admin_bypass": true
    }
  }
}
```

## 路径

路径优先读配置，其次环境变量，最后自动检测。控制台「设置」页最下面有「路径」卡，
可以点「自动检测」，自动找 `D:\GPT-SoVITS\models`、`D:\QQBot\rvc`、
`rvc\pkg\*\runtime\python.exe`、`model_map.json`、`bin\ffmpeg.exe`。

- `tts_root`：GPT-SoVITS 模型目录
- `rvc_root`：RVC 根目录
- `rvc_pkg`：带 runtime 的 RVC 整合包目录
- `model_map`：语种到唱歌模型的映射
- `ffmpeg`：ffmpeg.exe 路径

## 模块

| 模块 | 作用 | 默认 |
|---|---|---|
| access | 黑白名单守门，白名单外的人和群不理 | 开 |
| card | 音乐卡片解析 | 开 |
| face | 表情包、收图入库 | 开 |
| oneline | 回复收成一条 | 开 |
| memory_scheduler | 群聊攒够 25 条或每 30 分钟整理进个人档案 | 开 |
| profile | 统一上下文：档案、当前说话人、群名单、维护补记一次注入 | 开 |
| reply_gate | 群聊里按冷场/兴趣/话题决定要不要主动插话 | 开 |
| qzone | AI 说说、日记 | 开 |
| sendto | 跨群发消息 | 开 |
| stream | 消息流 | 开 |
| voice | 语音 + 唱歌 | 开 |
| asr | 收到语音先转成本机文字，失败也只说没听清 | 开 |
| maintenance_guard | 维护时压住回复 | 开 |

关掉模块：在 `modules.<名字>` 里把 `enabled` 设 false，或者在控制台「管理」页点开关。
改完要重启 AstrBot。AstrBot 插件配置页只剩一条说明，不再管模块开关。

## 语音识别

QQ 语音是 SILK，主模型（DeepSeek）不认音频。`asr` 模块在请求组装前用本机
faster-whisper 把语音转成文字，换成 `[语音] 说了什么` 再进模型。
全程在本机跑，不上传语音，不额外调 API。

第一次用要装依赖、下模型（约 460MB）：

```powershell
D:\QQBot\AstrBot\.venv\Scripts\python.exe -m pip install faster-whisper
D:\QQBot\AstrBot\.venv\Scripts\python.exe D:\QQBot\scripts\fetch_asr_model.py
```

`modules.asr` 可调：

| 键 | 作用 | 默认 |
|---|---|---|
| `enabled` | 总开关 | true |
| `model_dir` | 模型目录 | `models/asr/faster-whisper-small` |
| `repo_id` | 自动下载的模型 | `Systran/faster-whisper-small` |
| `language` | 识别语言，`zh` 或 `auto` | `zh` |
| `compute_type` | CPU 量化方式 | `int8` |
| `cpu_threads` | CPU 线程数 | 8 |
| `beam_size` | 搜索宽度，越大越准越慢 | 5 |
| `timeout_seconds` | 单条转写超时 | 90 |
| `auto_download` | 模型缺失时自动下载 | true |
| `hf_endpoint` | 下载源，国内用镜像 | `https://hf-mirror.com` |

改了这些要重启 AstrBot。识别失败、模型没装好都只会回一句「没听清」，
不会把整条消息吞掉。3 秒语音在 i7 上约 1 秒出字。

## 备份

每天 03:00 由 Windows 计划任务 `QQBot-DailyBackup` 跑 `backup.ps1`；
控制台「管理」页的「备份」卡也能立即备份。备份在 `backups\qqbot-日期-时间.zip`。
保留份数在管理页设置，默认 14 份，可以改成 1 到 60 份。

包含什么由管理页的「备份内容设置」决定，默认包含：

- `AstrBot\data`：会话、provider、插件配置、知识库等
- `data`：资料卡 `profiles.json`、token 账本 `tokens.db`、黑白名单、空间状态、网易云登录
- `SnowLuma\config`：协议端 token
- `secrets`、`personas`、控制台代码、`panel\model_state.json`、`panel\rvc_state.json`、`rvc\model_map.json`、`backup.ps1` / `backup.vbs`

表情包默认不备份，勾选后才进 zip。模型、RVC 权重、日志和缓存不进备份：体积太大，而且能重新准备。

备份 zip 里有 API key、协议端 token、网易云 cookie，别传到网盘，也别发给别人。

## 状态文件

这些是数据不是配置，别手改：

- `data/access_lists.json`：黑白名单（新好友、新入群默认都不进名单，要手动加）
- `data/dafeiyu/tokens.db`：唯一 token 账本，一条回复一行（时间、QQ、群号、token）；控制台所有 token 数字都读它
- `data/dafeiyu/backup_config.json`：备份内容开关和保留份数，控制台「管理」页可改
- `data/dafeiyu/profiles.json`：人物档案（昵称、关系、喜好、印象），里面旧的 tokens 字段不再用于显示
- 上下文：当前对话内容仍读 AstrBot 的 conversations，私聊每人一份，群每个一份；群成员共用群上下文
- `data/dafeiyu/summary_state.json`：自动整理进度
- `data/dafeiyu/summary_requests.json`：一键整理请求队列
- `data/dafeiyu/group_alerts.json`：新群提醒去重记录
- `data/qzone_ai_state.json`：说说/日记状态
- `data/qzone_drafts.json`：草稿
- `panel/model_state.json`：当前音色
- `panel/backup_state.json`：手动备份进度和结果
- `logs/backup.log`：每次备份的开始/完成/失败记录

## 环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| QQBOT_ROOT | 项目根 | 自动识别（控制台按自身位置往上找） |
| DAFEIYU_ADMIN_QQ | 管理员 QQ | 空 |
| DAFEIYU_BOT_QQ | 机器人 QQ | 空 |
| DAFEIYU_GROUP | 主群号 | 空 |
| GPT_MODEL_ROOT | 音色模型目录 | 配置里的 tts_root |
| SNOWLUMA_CONFIG | 协议端配置文件 | 自动找 SnowLuma/config/onebot_*.json |
| QQBOT_QQ_PIC_DIRS | QQ 图片缓存目录，分号分隔 | 自动找「文档\Tencent Files\*\nt_qq\nt_data\Pic」 |
| ONEBOT_URL | OneBot HTTP | http://127.0.0.1:3000 |
| ONEBOT_TOKEN | OneBot token | 从协议端配置读 |

控制台的「发送测试」不填号码时，会用配置里的 `admin_qq`（私聊）和 `group`（群聊）。

## 维护模式

开关写在 `modules.maintenance.enabled`。打开后插件压住所有正常回复（包括管理员），
由插件自己通过 OneBot 回一句维护提示，不经过模型。开和关都会私聊管理员提醒。

`modules.maintenance.cooldown_seconds` 控制同一个群/同一个人的回复间隔，默认 3 秒。
`message` 是维护时自动回复别人的话，`open_message` / `close_message` 是开关时私聊管理员的提醒，
三个都能在控制台总览的「维护模式」卡片里改。
