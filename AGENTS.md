# 大肥鱼 QQ 机器人 运行规则

唯一规则正文。旧规则全部作废，不要再建 RULES、HANDOFF、控制台说明等第二套；仓库一份同步副本，工作区只放入口。

## 底线

简单、易懂、方便管理。优先小改，不重写，不删核心功能。范围不清先问，不猜。

一次只改一个点。用户一次提多件，拆开做，做完一个验证一个。

## 返工原因

以前返工主要是这几件事：

1. 没查代码和数据就猜字段。比如把 conversations.token_usage 当成累计，其实它是最近一次回复。
2. 一次改太多，UI、后端、文案、文档混在一起，出问题找不到是哪一步。
3. 旧逻辑没删干净，新旧入口和两套数字同时存在。
4. 页面说明写太长、口径重复，甚至把别的页面的说明抄过来。
5. 改完没截图、没调接口就交付，用户先发现问题。
6. 规则和文档开了多套，改来改去。

所以固定做法：先查数据源，再动手；一次一个点；旧路径同次删；界面只留短标签；改完截图和接口都验证；规则只留一份。

## 控制台

总览只放四块：连接、插件、Token、健康。

总览不要快捷开关、最近会话、表情数量、插件数量、柱状图。

插件按名称一行一个，绿灯运行，红灯停用，不显示计数。

Token 四格一样大排一排：总消耗、今日消耗、最近 1 分钟、最近 5 分钟。

Token 只读 data\dafeiyu\tokens.db。插件每次回复写一行，字段是 ts、uid、gid、tokens。

总消耗、今日、最近 1 分钟、最近 5 分钟都从这一个账本算。私聊按 uid，群按 gid，群成员按 gid 加 uid，个人总计按 uid 全加，私聊和群聊自然叠加。

provider_stats、conversations.token_usage、profiles.tokens 只保留给 AstrBot 自己用，控制台不再拿它们显示 token。

清上下文不清 tokens.db，token 是累计消费；页面要写“累计 token”，不能当成当前上下文。

管理页放模块、插件、工具、备份、维护、体检、名单、AstrBot 入口。

设置页只放常用开关和路径。

页面副标题必须和内容对位。所有卡片尽量同高同宽。

## 控制台验收

每次改完截图总览、管理、设置。检查标题、数字、换行、按钮和结果。

截图放 logs\panel_*.png，只留最新两张。发现问题先改再交付。

## 数据和语音

删文件前先打包到 logs\backups 或 logs\deleted_plugins，验证压缩包再删。

不直接删 data、personas、models、secrets、backups。

不升级 AstrBot 4.29.1，不改 AstrBot 核心，补丁走 patches。

RVC 只能变声和唱歌，不能文字转语音。GPT-SoVITS 不关，音色不换，除非用户明确同意。

## 验证

当前没有 scripts\selfcheck.py，不要假装跑。

面板后端跑 py_compile，前端提取 script 跑 node --check，插件跑 smoke，环境跑 doctor，接口实测 /api/status 和 /api/token_rate，控制台截图，提交后看 CI。

报告贴实际输出，不编结果。

## 文档

规则正文：D:\QQBot\AGENTS.md。仓库副本：D:\dafeiyu-qqbot\AGENTS.md。工作区只放入口。

文档：仓库 docs 有 AI_SETUP、CONFIG、DIRECTORY、MODULES、V2_TEST；仓库根有 ARCHITECTURE、INSTALL、README、THIRD_PARTY；工作区副本在 D:\Users\21903\Documents\蓝色大肥鱼\文档。

不要写 D:\QQBot\docs 这种不存在的路径。改文档后跑 scripts\sync_docs.py。

## 交付

固定三段：改了什么；实际验证结果；怎么退回。

退回写上一个提交号或备份压缩包路径。没做和跑不了的直接说。
