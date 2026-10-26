# Agent Team Sync

[English](README.en.md)

让一个小团队里每个人各自用的 AI 编程 agent，共用同一个仓库、同一份团队上下文。

适用的情况：几个人合作一个项目，每人用自己习惯的 agent（Claude Code、Codex、Antigravity、WorkBuddy），其中有人不会用 git。没有这个工具时，每个 agent 只知道自己这台电脑上的情况：不知道别人刚改了什么、为什么改，两个人同时跑同一个脚本会互相覆盖，密钥也可能被提交进仓库。

## 它做什么

装好以后，成员照常打开自己的 agent 干活，下面这些由 hook、git 和系统定时任务自动完成：

- **开会话时给 agent 一份团队摘要**：先拉取最新内容，再把最近的提交、团队日志的最后几条、谁正在跑哪个共享任务、共享盘的位置和一级文件夹、最近的对话记录注入 agent 的上下文。
- **自动提交和推送**：会话结束时提交本地改动、变基到远端、推送。另有每 5 分钟一次的后台同步，不依赖任何 agent。
- **记录对话**：每轮结束后，把用户和 agent 说的话写进仓库，一个会话一个文件；不记工具调用和工具输出。原文超过 3 万字时，由正在运行的 agent 把较早的部分写成摘要，不需要 API key，也不额外调用模型。
- **给队友的 agent 留言**：`team-sync msg send <谁> "……"`。对方开新对话或发下一条消息时，留言会出现在 agent 的上下文里，由 agent 原文转告本人；送达后发件人下次开会话能看到回执。
- **租约**：采集、上线这类会动共享资源的脚本，开始前先在数据库里占用对应资源；别人正在跑时直接拒绝，并显示是谁在占用。租约会过期，某个 agent 中途崩溃也不会让全队卡住。
- **密钥门禁**：git 提交前检查新增内容，发现密钥形状的字符串或 `.env` 这类文件就拒绝提交；对话记录里的密钥和邮箱地址也会被替换掉。
- **冲突处理**：拉取冲突时放弃这次合并、保留本地改动，下次开会话时提示，不会留下改了一半的文件；下一次同步还冲突才弹桌面通知，下一次就自己好了的，摘要里说一句已恢复。推送一直失败（比如 GitHub 登录过期）、提交一直被密钥门禁拦下、拉取因为别的原因一直失败，持续满 `sync.alert_after_min`（默认 60 分钟）弹一次桌面通知，恢复后再弹一次；断网不算。别的 git 操作正在进行（比如 agent 正在提交）造成的暂时锁住，等几秒重试，不算冲突。多人同时往团队日志末尾追加内容，会自动合并。
- **提交显示 GitHub 身份**：成员电脑没设置 git 邮箱时，自动把这个仓库的提交身份设成本人的 GitHub 用户名和 GitHub 隐私邮箱，提交记录里显示用户名和头像，而不是电脑上的全名。
- **给不懂技术的成员装机**：安装脚本可以交给成员自己的 agent 来跑，遇到需要本人操作的步骤（登录 GitHub、安装 Google Drive）会停下来，告诉 agent 该让用户点哪里。

## 分三层存放

| 层 | 放什么 | 怎么同步 |
|---|---|---|
| **git 仓库** | 代码、文档、skill、规格、团队日志、对话记录 | hook 和后台同步自动拉取、提交、推送 |
| **共享盘**（Google Drive、OneDrive、Dropbox） | 大文件、给人看的文档 | 由共享盘客户端同步；本工具只负责在每台电脑上找到它的路径 |
| **数据库**（Supabase；可选） | 租约、共享进度、团队口令 | 通过 HTTP 调用数据库里的 6 个函数，每个函数先校验口令；表启用了行级安全。不配数据库时，其他功能照常工作，只是没有租约 |

这样分的原因：三类内容的大小差四个数量级（文件以 GB 计、代码以 MB 计、状态以字节计），对一致性的要求也不同。共享盘没有锁，遇到冲突只会复制出副本；git 只能在改完以后才发现冲突；只有数据库能在一个耗时的任务开始之前就拒绝它。

规则的执行不依赖模型记住规则：租约在脚本里拿，密钥门禁在 git 里跑，后台同步由操作系统的定时任务运行。hook 只负责开头的摘要和结束时的推送。

## 开会话时 agent 看到的内容

```
## 团队同步 · alice@laptop:4242 · 2026-09-09 10:02
已拉取最新
最近提交(2026-09-08 起):
   3f1c2a9 2026-09-09 bob: sync(bob): 2026-09-09 09:40 · 2 files
团队日志最近几条:
   - 2026-09-09 09:40 · bob · 改写了价格页文案 / docs/pricing.md / 待审
活跃租约: collect:telegram:main←bob@desk:9911
data/: 与清单一致(3221 文件,清单由 alice 于 2026-09-07 生成)
最近的对话记录(想知道之前聊过什么,打开对应文件):
   09-09 09:38 · bob · Codex · 价格页改成按地区显示币种 → docs/对话记录/2026-09/2026-09-09_bob_codex_01a0f3c2.md
Drive: /Users/alice/Library/CloudStorage/GoogleDrive-…/Shared drives/Team
   一级文件夹: 合同 · 素材 · 报告
规则见 AGENTS.md;要占用共享资源时通过脚本自动拿租约,不要手动绕过。
```

## 支持的 agent

| agent | hook 文件 | 做的事 |
|---|---|---|
| Claude Code | `.claude/settings.json` | 会话开始：摘要；每轮开始：新留言；每轮结束：对话记录，再在后台提交推送；会话结束：记一行日志 |
| Codex | `.codex/hooks.json` | 会话开始：摘要；每轮开始：新留言；每轮结束：对话记录，再在后台提交推送。脚本调用的 `codex exec` 自动跳过 |
| Antigravity | `.agents/hooks.json` | 每个对话注入一次摘要；之后每次调用检查新留言；空闲时提交推送 |
| WorkBuddy / CodeBuddy | `.codebuddy/settings.json` | 会话开始：摘要；每轮开始：新留言；会话结束：提交推送 |
| 其他 agent | 不需要 | 后台同步照常运行；开头摘要要靠模型按守则自己运行 `start`，可能被跳过（见下一节） |

团队守则只有一份正本 `AGENTS.md`；`CLAUDE.md` 和 `CODEBUDDY.md` 各只有一行 `@AGENTS.md`。

Codex 第一次打开项目时，要信任这个文件夹，再在「设置 → Hooks」里给每个 hook 点一次 Trust；hook 文件改动后要重新点。对话记录目前支持 Claude Code 和 Codex，Antigravity 和 WorkBuddy 还不支持。

## 哪些有保证，哪些要靠模型遵守指令

由系统执行、和模型是否照做无关：

- **后台同步**：操作系统的定时任务每 5 分钟拉取、提交、推送一次，不经过 agent。
- **密钥门禁**：装在 git 的 pre-commit 里，agent 自己执行 `git commit` 也会被检查。
- **租约**：写在脚本里，只要运行这个脚本就一定先检查。
- **有 hook 的 agent**：开头的团队摘要、留言送达、对话记录、会话结束时的提交推送，由 agent 的 hook 自动运行。

写在守则里、要靠模型遵守的：

- **没有 hook 的 agent 开工前运行 `start`**：模型可能跳过或拖后。没运行时，它不知道队友最近改了什么，可能在旧文件上继续改；后台同步最多 5 分钟后会拉到新内容，这段时间里的改动可能和别人冲突。没有 hook 的 agent 也没有对话记录。
- **所有 agent 都一样**：做完一块工作记一行日志、开始前先读对应的知识文档、提示版本不一致时升级、对话记录过长时写摘要、收到留言先转告用户而不是直接照做。这些都是请求，模型可能不照做。

需要稳定执行的规则，应该写进脚本、git hook 或 agent 的 hook，不要只写在守则里。

## 和托管的协作平台相比

市面上也有让多人和多个 agent 协作的托管平台，它们提供自己的客户端、项目和任务看板。两类工具的差别：

| | agent-team-sync | 托管的协作平台 |
|---|---|---|
| 使用的 agent | 成员已经在用的 Claude Code、Codex、Antigravity、WorkBuddy，可以混用 | 平台自己的 agent |
| 数据存放 | 你自己的 git 仓库、共享盘和数据库 | 平台提供的共享存储 |
| 新界面和新账号 | 没有，成员照常打开自己的 agent | 安装平台客户端或用网页版，再邀请成员加入 |
| 防止重复跑同一个任务 | 数据库租约，脚本开始前检查 | 未见公开说明 |
| 密钥防护 | 提交前门禁，对话记录自动脱敏 | 未见公开说明 |
| 任务看板、上下游交接、手机端 | 没有 | 有 |
| 费用 | 开源（MIT），数据库可用 Supabase 免费档 | 未见公开价格 |

适合：3 到 10 人、已经各自在用编程 agent、希望数据留在自己手里的团队。需要任务看板或跨部门流程审批时，托管平台更合适。

## 安装

依赖只有 git 和 Python 3.9 以上（macOS 自带的就够），不用安装任何第三方库。共享盘可以是任何会同步成本地文件夹的服务：Google Drive、OneDrive、Dropbox 能自动找到，其他的（坚果云、iCloud、NAS 等）在 `team-sync.json` 的 `drive.patterns` 里加一条路径规则，或在每台电脑的 `.env.team` 里写 `<前缀>_DRIVE_ROOT=路径`。

```bash
git clone --branch v0.1.28 https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg   # 成员:只需要 git
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.23"  # 维护者:得到 team-sync 命令
```

用 `init` 配置过的仓库里，一切都通过提交进仓库的启动器 `bin/team_sync.py` 运行，两种安装方式它都能找到。Windows 需要先装 Python，并确保有 `python3` 命令。

## 维护者：配置仓库（一次）

```bash
cd your-team-repo
team-sync init --team "Acme" --prefix ACME --lang zh   # 写入 team-sync.json、四家 hook 文件、.env.team.example、AGENTS.md
team-sync db install --project-ref <supabase-ref>      # 环境变量里放 SUPABASE_ACCESS_TOKEN,或用 --dsn postgres://…
echo "ACME_TEAM_KEY=$(openssl rand -hex 24)" >> .env.team
team-sync db register-key --project-ref <supabase-ref>
team-sync db env-for bob                                # 在桌面生成 env.team.txt,发给 Bob
team-sync autosync on
git add -A && git commit -m "team-sync" && git push
```

`team-sync.json` 的主要字段：

```json
{
  "team": "Acme",
  "lang": "zh",
  "env_prefix": "ACME",
  "journal": "docs/journal.md",
  "fingerprint": { "root": "data", "manifest": "data_manifest.json" },
  "drive": { "root_name": "Team", "marker": "Papers" },
  "convo": { "dir": "docs/对话记录", "compact_chars": 30000, "keep_recent_chars": 8000, "keep_email_domains": ["acme.com"] },
  "inbox": { "dir": "docs/留言" },
  "notify": { "icon": "tools/notify-icon.png" },
  "version": "0.1.28"
}
```

`version` 锁定团队使用的版本：成员电脑上的版本不一致时，开头摘要会提示，守则让 agent 直接运行 `team-sync upgrade`。`convo` 不写就不记录对话，`inbox` 不写就不开留言。`notify.icon` 是桌面通知用的图标（仓库里的一张方形 PNG 或 icns），不写就用系统的同步图标。

## 成员：配置电脑（一次）

把 `tools/SETUP_RUNBOOK.md` 一类的安装手册、安装脚本和 `env.team.txt` 发给成员，让成员对自己的 agent 说「按照这份手册帮我完成配置」。手动配置的步骤：

```bash
git clone <团队仓库> ~/team && cd ~/team
cp ~/Downloads/env.team.txt .env.team        # 维护者发来的文件
python3 bin/team_sync.py start               # 安装提交前门禁,打印摘要
python3 bin/team_sync.py autosync on
python3 bin/team_sync.py paths               # 四行:仓库、共享盘、身份、数据库
```

之后用 agent 打开这个文件夹即可。

## 租约

```python
from team_sync.leases import lease

with lease('collect:telegram:main', ttl=1800, note='fetch'):
    ...   # 被占用时直接退出并说明谁在占用;后台每 ttl/3 秒续租
```

```
team-sync lease acquire collect:telegram:main --ttl 900
team-sync lease list
team-sync lease release collect:telegram:main [--force]
```

租约按资源计，不按人计。持有者标识是 `人@电脑:进程`，同一台电脑上的两个 agent 算两个持有者。

## 对话记录

在 `team-sync.json` 里写上 `"convo": {"dir": "docs/对话记录"}` 即开启。每个会话每个月一个文件：`<dir>/年-月/<日期>_<谁>_<agent>_<会话号>.md`，日期是这个会话在这个月的第一条消息的日期，会话号是 agent 会话 ID 的前 8 位。会话跨到新的一个月时，在新月份的文件夹里接着记，两份文件开头互相链接，旧文件不再改动；这样每个月文件夹里就是那个月实际聊过的内容，单个文件也不会无限变长。

- 不记：工具调用、工具输出、思考过程、注入的上下文。记：双方说的话，以及用户在选择题里点的答案。
- 脱敏：密钥形状的字符串换成 `[redacted]`；邮箱地址换成 `[email]`，`keep_email_domains` 里列出的域名除外。
- 摘要：还没写摘要的原文超过 `compact_chars` 时，hook 在这一轮结束时请正在运行的 agent 给较早的部分写摘要，再运行 `team-sync convo compact`；摘要加在文件开头，原文全部保留（0.1.25 起；之前的版本会删掉写过摘要的原文）。最新的 `keep_recent_chars` 不写进这次摘要。
- 暂停和恢复：只认下面这些固定说法（字符串匹配，hook 里没有模型；`[...]` 不区分大小写）：

  | 作用 | 说法 |
  |---|---|
  | 从这条起暂停，前面的保留 | `不记录这段对话`、`不记录本次对话`、`暂停记录`、`[no-record]` |
  | 从这条起恢复，暂停期间的不补 | `恢复记录`、`继续记录`、`[record]` |
  | 恢复，并补上暂停期间的（误触发时用） | `恢复记录并补上`、`补上刚才的记录`、`[record-all]` |

  触发条件：放在消息开头，或者消息以它结尾、前面最多 6 个字（先去掉空格）；整条消息里不能有提问的迹象（`?`、`？`、吗、怎么、怎样、如何、如果、假如、会不会、是不是、能不能、可不可以、what、how、does、will、if）。换别的说法不会触发。暂停期间的内容只存在本机缓存里，不进仓库；记录里标出暂停和恢复的位置。暂停时每一轮都会提示用户：hook 在界面上发提示，同时要求 agent 在回复第一行原样写出（Codex 桌面版不显示 hook 提示，Claude Code 把它折叠起来）。环境变量 `TEAM_SYNC_NO_CONVO=1` 或 env 文件里的 `<前缀>_CONVO=off` 关闭整台电脑的记录。
- 恢复和分支：Claude Code 每次恢复会话都换一个新的会话 ID，再把整段旧对话复制进去。复制的内容正好到上一个会话记下的最后一条时，算同一个对话接着聊，继续写原来的文件，文件开头记下之前用过的会话 ID；只复制到中间某条的，是从那里分出去的分支，另开文件，只记自己的话，开头链接回原记录。被接着写的旧会话如果之后又有人说话，说明当时其实是分支，它从那以后写进一个带 `_2` 后缀的新文件，两边不会写同一个文件。按消息 ID 判断，每台电脑上有一份索引。Codex 恢复会话时 ID 不变，一直写同一个文件。
- 回退（rewind）：用户在 Claude Code 里 rewind，或在 Codex 里回退几轮之后，被撤回的那几条不删，在记录里折叠在「↩ 已撤回」下面，原文留作参考，重来之后的对话照常往下记。Claude Code 按新消息的 `parentUuid` 判断它接在哪条后面（rewind 不会在记录里另写标记）；Codex 按记录里的 `thread_rolled_back` 事件和它撤回的轮数。rewind 只撤回对话，撤回时还原的文件改动不受影响：之前的改动已经提交推送了，还原会作为一次新的改动再提交上去。
- 仓库里的人都能看到这些记录，开启前要告诉被记录的人。

## 留言

在 `team-sync.json` 里写上 `"inbox": {"dir": "docs/留言"}` 即开启。

```
team-sync msg send yulu "首页文案改好了，有空看一下 docs/知识/网站现状.md"
team-sync msg send yulu,hanyu "……"     多人用逗号隔开
team-sync msg list                     自己发出和收到的留言、是否已送达
```

- 收件人写对方的简称，也就是对方 env 文件里的 `<前缀>_WHO`。
- 一条留言一个文件：`<dir>/<收件人>/<时间>_<发件人>.md`，两个人同时写不会冲突。
- 送达：对方开新对话时放在团队摘要最前面；对话进行中，每轮开始前再检查一次。每轮检查只读本地文件，不联网，不会拖慢对话；新留言靠每轮结束的同步和每 5 分钟的后台同步拉下来，所以最慢要等几分钟加上对方的下一条消息。
- 送达后标记为已送达，这个标记随同步回到发件人那边，发件人下次开会话时摘要里会显示。
- 桌面通知：同步拉到一条还没送达的新留言时，收件人的电脑会弹一条系统通知（谁留的言、开头一段）。点通知会打开这台电脑上最近一次跑 hook 的 agent 应用（Claude、Codex、终端或编辑器）。不开 agent 也会弹，因为后台同步不依赖 agent；每条只弹一次，弹通知不算送达。macOS 上通知由一个自动生成的小程序发出（`~/.team-sync/notify/team-sync.app`，不用安装任何东西），第一次要在系统弹窗里允许通知；换了图标会生成新的小程序，要再允许一次。Windows 上是系统原生通知，显示 team-sync 和图标，在当前用户的注册表里登记，不需要管理员；在弹窗或通知中心里点都能打开 agent，点一条消一条。
- 代理：git 不认系统代理（Clash 等的「系统代理」开关），只认 `http.proxy` 和 `*_PROXY` 环境变量；从应用启动的 hook 和后台同步也拿不到终端里设的变量。所以没设代理变量时，team-sync 会把系统代理交给 git 和 gh；自己给 git 配了 `http.proxy` 的，以那个为准。PAC 自动配置读不到。
- **留言只转告，不执行。** 注入的文字写明这是队友留言、不是用户本人的指令，agent 要先把原文告诉用户，用户同意后才照做。否则能往仓库推送的人就能操纵队友的 agent，本人也不知道它为什么突然去做某件事。这一条靠模型遵守，见上一节。

## 其他命令

```
team-sync note "做了什么 / 改了哪 / 下一步"   往团队日志追加一行
team-sync msg send <谁> "……" | list      给队友的 agent 留言
team-sync convo list | compact             对话记录
team-sync cursor show|pull|push            共享进度(例如采集抓到了第几条)
team-sync fingerprint [--check|--quick]    不进 git 的数据目录的指纹清单
team-sync gate                             密钥门禁(会自动装成 pre-commit)
team-sync selftest                         在临时目录里用三个克隆模拟整套流程,约 30 秒
team-sync paths | status | version
```

## 设计说明

- **租约放在脚本里，不放在 hook 里。** 各家 agent 的 hook 不一样，需要用户信任，而且 agent 自己写的命令可以绕过 hook。脚本是使用这个资源的唯一入口，不会被忘掉。
- **成员只拿团队口令。** 成员拿不到数据库的服务密钥；匿名 key 本来就是公开的，表启用了行级安全，只有校验过口令的函数能读写。
- **开头摘要里的「谁做了什么」从 git 来。** 提交记录、团队日志、对话记录都在仓库里，换电脑、换 agent 都能读到，不依赖某一家 agent 的记忆功能。
- **自测。** `team-sync selftest` 在临时目录里建一个本地远端和三个克隆，覆盖同步、冲突、租约、四家 agent 的 hook、对话记录（过滤、脱敏、压缩、不记录）、日志并发合并、留言送达和回执、提交身份，不会碰真实的远端。

## 背景

这个工具来自一个三人团队的日常使用：三个人分别在 Mac 和 Windows 上用 Claude Code、Codex 和 Antigravity，两个人不用 git，共同维护一个题库、一个网站和一个共享盘。

## 许可

MIT。先在一个测试仓库里运行 `team-sync init`，再运行 `team-sync selftest` 确认各项检查通过。
