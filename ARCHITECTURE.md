# 架构

一句话：**两套引擎并存，一个开关切换；新引擎是一个插件、一层公共 core、一堆互不认识的模块。**

```
engines/
├─ legacy/plugins/      旧引擎：8 个独立插件，线上现在跑的这套
└─ v2/plugin/           新引擎：一个插件 + 模块
   ├─ main.py           固定入口：建内核、挂钩子、分发
   ├─ dafeiyu_core/     公共层，所有模块共用
   └─ dafeiyu_modules/  功能模块，一个文件一个功能
switch.ps1              在 legacy 和 v2 之间切换
```

## 切换引擎

```powershell
# 切到新引擎
powershell -ExecutionPolicy Bypass -File switch.ps1 -Engine v2

# 切回旧引擎
powershell -ExecutionPolicy Bypass -File switch.ps1 -Engine legacy

# 只换插件不重启
... -NoRestart
```

脚本会停 AstrBot、清掉 `data\plugins` 里两套 dafeiyu 插件、装选中的一套、再启动。
两套代码都在仓库里，随时切回。

## 新引擎内部

```
engines/v2/plugin/
├─ main.py              固定入口：只做三件事——建内核、挂钩子、分发
├─ dafeiyu_core/        公共层，所有模块共用
│  ├─ paths.py          项目根、数据目录、日志目录（自动识别，可 QQBOT_ROOT 覆盖）
│  ├─ config.py         统一配置 data/dafeiyu/config.json
│  ├─ onebot.py         OneBot HTTP 客户端（URL/token 只在这里读）
│  ├─ stream.py         写控制台消息流
│  ├─ media.py          ffmpeg / mp3 / 归一化
│  ├─ module.py         模块基类 + 注册表
│  └─ kernel.py         加载模块、分发钩子、注册工具
└─ dafeiyu_modules/     功能模块，一个文件一个功能
   ├─ stream.py         消息流
   ├─ oneline.py        回复收成一条
   ├─ card.py           音乐卡片解析
   ├─ maintenance_guard.py 维护模式时压住 AstrBot 回复
   ├─ reply_gate.py     群聊里先判断这句要不要接
   ├─ memory_scheduler.py 按消息量或定时把群聊整理进个人档案
   ├─ access.py         黑白名单守门
   ├─ profile.py        统一上下文：人物档案、当前说话人、群名单、维护补记
   ├─ face.py           表情包
   ├─ voice.py          语音 + 唱歌
   ├─ asr.py            收到语音先转文字，避免主模型不认音频而整条报错
   ├─ sendto.py         跨群发消息
   └─ qzone.py          QQ 空间
```

## 一次请求的上下文顺序

1. AstrBot 人设（system prompt）
2. AstrBot 会话历史（按群/私聊）
3. 我们统一的动态块：当前说话人 → 本群最近说话的人 → 维护期间补记

第 3 块由 `dafeiyu_modules/profile.py` 一次拼好、只注入一次，不再由多个模块各塞一段。
长期记忆也收在人物档案里，不再装第三方记忆插件。

语音消息在组装请求之前由 `asr.py` 转成文字，并替换掉语音段；模型收到的是
`[语音] 说了什么`，不是音频。这样 DeepSeek 这类纯文字模型也不会因为收到音频而 422。

AstrBot 自带的群上下文块也标了 `_no_save`，只参与当前请求、不写会话历史；
4.29.1 的空消息 IndexError 用 `messages and` 补掉。

## 定时任务

- 插件内统一在 `memory_scheduler`：一个群每攒 25 条新消息整理一次，另外每 30 分钟兜底扫一遍。
- 系统级两个任务留在 Windows：`QQBot-DailyBackup` 每天 03:00 跑 `backup.ps1`，
  `QQBot-Watchdog` 看门狗。这两个必须在机器人挂了的时候也能跑，所以不搬进插件。
- 每日备份覆盖 `AstrBot\data`、`data`（资料卡、名单、token 记录）、`SnowLuma\config`、
  `secrets`、`personas`、音色状态和 RVC 映射；控制台总览的「备份」卡可以手动触发，
  `/backups` 页能看和下载最近 14 份。
- 主动聊天、QQ 空间是第三方插件自己的定时器，没有并进来。

## 模块契约

每个功能模块就是一个类，继承 `Module`，用 `@register` 注册：

```python
@register
class FaceModule(Module):
    name = "face"
    description = "表情包"
    default_enabled = True

    async def setup(self):
        """启动时调一次：加载模型、建目录、读自己的配置。"""

    def tools(self):
        """给模型暴露的工具，返回 ToolSpec 列表。"""

    async def on_message(self, event):
        """每条消息进来时调。"""

    async def on_waiting_llm_request(self, event):
        """确定要进模型、但请求还没组装时调；改消息本身（比如语音转文字）放这里。"""

    async def on_llm_request(self, event, req):
        """请求模型前调，可以往 system prompt / 图片里塞东西。"""

    async def on_decorating_result(self, event):
        """回复发出前调，可以改文字、加图。"""
```

只有实现需要的钩子，其他不用写。

## 加一个功能

1. 在 `dafeiyu_modules/` 新建 `xxx.py`
2. 写一个 `@register` 的类，起 `name`，实现需要的钩子
3. 要在控制台配参数，就在 `module_cfg` 里加一段默认值
4. 重启 AstrBot。内核会自动发现、按开关加载

不用改 `main.py`，不用改其他模块。

## 删一个功能

1. 删掉对应的 `dafeiyu_modules/xxx.py`
2. 重启

配置里残留的 section 不影响。

## 例子：维护模式

调试、升级的时候打开维护模式。插件压住所有正常回复，并由插件自己直接回一句维护提示，不经过模型。

```powershell
python scripts\maintenance.py on
python scripts\maintenance.py off
python scripts\maintenance.py status
python scripts\maintenance.py message "本鱼在换引擎，晚点再来。"
```

行为：

- 每个用户默认 120 秒内只回一次，不刷屏
- 管理员 QQ 默认放行，方便自己上来测
- 改配置即时生效，不用重启（每条消息会检查配置文件修改时间）

配置在 `data/dafeiyu/config.json`：

```json
{
  "modules": {
    "maintenance": {
      "enabled": true,
      "message": "唔，本鱼正在升级……",
      "cooldown_seconds": 120,
      "admin_bypass": true
    }
  }
}
```

这个模块就是“加功能只动一个文件”的样板：一个文件、一个类、一个 `on_message`，没改 main.py。

## 开关和配置

`data/dafeiyu/config.json`：

```json
{
  "admin_qq": "",
  "bot_qq": "",
  "group": "",
  "modules": {
    "stream": {"enabled": true},
    "voice": {"enabled": false, "max_chars": 150}
  }
}
```

模块自己的参数放在 `modules.<name>` 里，模块用 `self.cfg` 读。
控制台以后只读写这一份文件。

## 钩子怎么走

```
QQ -> 协议端 -> AstrBot
   -> main.on_message        -> kernel.dispatch("on_message")
   -> main.on_waiting_llm_request -> kernel.dispatch("on_waiting_llm_request")
   -> main.on_llm_request    -> kernel.dispatch("on_llm_request")
   -> main.on_decorating_result -> kernel.dispatch("on_decorating_result")
```

模块之间不互相调用；要共享就放 core。

## 模块状态

| 模块 | 状态 |
|---|---|
| stream / oneline / card / maintenance_guard | 稳定 |
| access / profile / face / voice / sendto / qzone | 稳定，都在 `dafeiyu_modules` |
| asr | 稳定，收语音先转文字；模型和依赖见 `docs/CONFIG.md` |

没有旧插件依赖，新功能只加一个模块文件。
