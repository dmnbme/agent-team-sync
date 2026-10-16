# Agent Team Sync

[English](README.en.md)

让一个小团队里每个人各自用的 AI 编程 agent,共用同一个仓库、同一份团队上下文。

适用的情况:几个人合作一个项目,每人用自己习惯的 agent(Claude Code、Codex、Antigravity、WorkBuddy),其中有人不会用 git。没有这个工具时,每个 agent 只知道自己这台电脑上的情况:不知道别人刚改了什么、为什么改,两个人同时跑同一个脚本会互相覆盖,密钥也可能被提交进仓库。

## 它做什么

装好以后,成员照常打开自己的 agent 干活,下面这些由 hook、git 和系统定时任务自动完成:

- **开会话时给 agent 一份团队摘要**:先拉取最新内容,再把最近的提交、团队日志的最后几条、谁正在跑哪个共享任务、共享盘的位置和一级文件夹、最近的对话记录注入 agent 的上下文。
- **自动提交和推送**:会话结束时提交本地改动、变基到远端、推送。另有每 5 分钟一次的后台同步,不依赖任何 agent。
- **记录对话**:每轮结束后,把用户和 agent 说的话写进仓库,一个会话一个文件;不记工具调用和工具输出。原文超过 3 万字时,由正在运行的 agent 把较早的部分写成摘要,不需要 API key,也不额外调用模型。
- **租约**:采集、上线这类会动共享资源的脚本,开始前先在数据库里占用对应资源;别人正在跑时直接拒绝,并显示是谁在占用。租约会过期,某个 agent 中途崩溃也不会让全队卡住。
- **密钥门禁**:git 提交前检查新增内容,发现密钥形状的字符串或 `.env` 这类文件就拒绝提交;对话记录里的密钥和邮箱地址也会被替换掉。
- **冲突处理**:拉取冲突时放弃这次合并、保留本地改动,弹出桌面通知,下次开会话时再提示,不会留下改了一半的文件。多人同时往团队日志末尾追加内容,会自动合并。
- **给不懂技术的成员装机**:安装脚本可以交给成员自己的 agent 来跑,遇到需要本人操作的步骤(登录 GitHub、安装 Google Drive)会停下来,告诉 agent 该让用户点哪里。

## 分三层存放

| 层 | 放什么 | 怎么同步 |
|---|---|---|
| **git 仓库** | 代码、文档、skill、规格、团队日志、对话记录 | hook 和后台同步自动拉取、提交、推送 |
| **共享盘**(Google Drive、OneDrive、Dropbox) | 大文件、给人看的文档 | 由共享盘客户端同步;本工具只负责在每台电脑上找到它的路径 |
| **数据库**(Supabase;可选) | 租约、共享进度、团队口令 | 通过 HTTP 调用数据库里的 6 个函数,每个函数先校验口令;表启用了行级安全。不配数据库时,其他功能照常工作,只是没有租约 |

这样分的原因:三类内容的大小差四个数量级(文件以 GB 计、代码以 MB 计、状态以字节计),对一致性的要求也不同。共享盘没有锁,遇到冲突只会复制出副本;git 只能在改完以后才发现冲突;只有数据库能在一个耗时的任务开始之前就拒绝它。

规则的执行不依赖模型记住规则:租约在脚本里拿,密钥门禁在 git 里跑,后台同步由操作系统的定时任务运行。hook 只负责开头的摘要和结束时的推送。

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
| Claude Code | `.claude/settings.json` | 会话开始:摘要;每轮结束:对话记录;会话结束:提交推送并记一行日志 |
| Codex | `.codex/hooks.json` | 会话开始:摘要;每轮结束:对话记录,再在后台提交推送。脚本调用的 `codex exec` 自动跳过 |
| Antigravity | `.agents/hooks.json` | 每个对话注入一次摘要;空闲时提交推送 |
| WorkBuddy / CodeBuddy | `.codebuddy/settings.json` | 会话开始:摘要;会话结束:提交推送 |
| 其他 agent | 不需要 | 后台同步照常运行;守则里写明开工前先运行 `start` |

团队守则只有一份正本 `AGENTS.md`;`CLAUDE.md` 和 `CODEBUDDY.md` 各只有一行 `@AGENTS.md`。

Codex 第一次打开项目时,要信任这个文件夹,再在「设置 → Hooks」里给每个 hook 点一次 Trust;hook 文件改动后要重新点。对话记录目前支持 Claude Code 和 Codex,Antigravity 和 WorkBuddy 还不支持。

## 和托管的协作平台相比

市面上也有让多人和多个 agent 协作的托管平台,它们提供自己的客户端、项目和任务看板。两类工具的差别:

| | agent-team-sync | 托管的协作平台 |
|---|---|---|
| 使用的 agent | 成员已经在用的 Claude Code、Codex、Antigravity、WorkBuddy,可以混用 | 平台自己的 agent |
| 数据存放 | 你自己的 git 仓库、共享盘和数据库 | 平台提供的共享存储 |
| 新界面和新账号 | 没有,成员照常打开自己的 agent | 安装平台客户端或用网页版,再邀请成员加入 |
| 防止重复跑同一个任务 | 数据库租约,脚本开始前检查 | 未见公开说明 |
| 密钥防护 | 提交前门禁,对话记录自动脱敏 | 未见公开说明 |
| 任务看板、上下游交接、手机端 | 没有 | 有 |
| 费用 | 开源(MIT),数据库可用 Supabase 免费档 | 未见公开价格 |

适合:3 到 10 人、已经各自在用编程 agent、希望数据留在自己手里的团队。需要任务看板或跨部门流程审批时,托管平台更合适。

## 安装

依赖只有 git 和 Python 3.9 以上(macOS 自带的就够),不用安装任何第三方库。共享盘可以是任何会同步成本地文件夹的服务:Google Drive、OneDrive、Dropbox 能自动找到,其他的(坚果云、iCloud、NAS 等)在 `team-sync.json` 的 `drive.patterns` 里加一条路径规则,或在每台电脑的 `.env.team` 里写 `<前缀>_DRIVE_ROOT=路径`。

```bash
git clone --branch v0.1.13 https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg   # 成员:只需要 git
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.13"  # 维护者:得到 team-sync 命令
```

用 `init` 配置过的仓库里,一切都通过提交进仓库的启动器 `bin/team_sync.py` 运行,两种安装方式它都能找到。Windows 需要先装 Python,并确保有 `python3` 命令。

## 维护者:配置仓库(一次)

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

`team-sync.json` 的主要字段:

```json
{
  "team": "Acme",
  "lang": "zh",
  "env_prefix": "ACME",
  "journal": "docs/journal.md",
  "fingerprint": { "root": "data", "manifest": "data_manifest.json" },
  "drive": { "root_name": "Team", "marker": "Papers" },
  "convo": { "dir": "docs/对话记录", "compact_chars": 30000, "keep_recent_chars": 8000, "keep_email_domains": ["acme.com"] },
  "version": "0.1.13"
}
```

`version` 锁定团队使用的版本:成员电脑上的版本不一致时,开头摘要会提示,守则让 agent 直接运行 `team-sync upgrade`。`convo` 不写就不记录对话。

## 成员:配置电脑(一次)

把 `tools/SETUP_RUNBOOK.md` 一类的安装手册、安装脚本和 `env.team.txt` 发给成员,让成员对自己的 agent 说「按照这份手册帮我完成配置」。手动配置的步骤:

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

租约按资源计,不按人计。持有者标识是 `人@电脑:进程`,同一台电脑上的两个 agent 算两个持有者。

## 对话记录

在 `team-sync.json` 里写上 `"convo": {"dir": "docs/对话记录"}` 即开启。每个会话一个文件:`<dir>/年-月/<日期>_<谁>_<agent>_<会话号>.md`。

- 不记:工具调用、工具输出、思考过程、注入的上下文。记:双方说的话,以及用户在选择题里点的答案。
- 脱敏:密钥形状的字符串换成 `[redacted]`;邮箱地址换成 `[email]`,`keep_email_domains` 里列出的域名除外。
- 压缩:原文超过 `compact_chars` 时,hook 在这一轮结束时请正在运行的 agent 写摘要,再运行 `team-sync convo compact`;最新的 `keep_recent_chars` 保持原文。
- 不记录:用户消息里出现 `[no-record]` 或「不记录这段对话」,这个会话的记录会被删掉(已经推送的部分仍在 git 历史里);环境变量 `TEAM_SYNC_NO_CONVO=1` 或 env 文件里的 `<前缀>_CONVO=off` 关闭整台电脑的记录。
- 仓库里的人都能看到这些记录,开启前要告诉被记录的人。

## 其他命令

```
team-sync note "做了什么 / 改了哪 / 下一步"   往团队日志追加一行
team-sync convo list | compact             对话记录
team-sync cursor show|pull|push            共享进度(例如采集抓到了第几条)
team-sync fingerprint [--check|--quick]    不进 git 的数据目录的指纹清单
team-sync gate                             密钥门禁(会自动装成 pre-commit)
team-sync selftest                         在临时目录里用三个克隆模拟整套流程,约 30 秒
team-sync paths | status | version
```

## 设计说明

- **租约放在脚本里,不放在 hook 里。** 各家 agent 的 hook 不一样,需要用户信任,而且 agent 自己写的命令可以绕过 hook。脚本是使用这个资源的唯一入口,不会被忘掉。
- **成员只拿团队口令。** 成员拿不到数据库的服务密钥;匿名 key 本来就是公开的,表启用了行级安全,只有校验过口令的函数能读写。
- **开头摘要里的「谁做了什么」从 git 来。** 提交记录、团队日志、对话记录都在仓库里,换电脑、换 agent 都能读到,不依赖某一家 agent 的记忆功能。
- **自测。** `team-sync selftest` 在临时目录里建一个本地远端和三个克隆,覆盖同步、冲突、租约、四家 agent 的 hook、对话记录(过滤、脱敏、压缩、不记录)和日志并发合并,不会碰真实的远端。

## 背景

这个工具来自一个三人团队的日常使用:三个人分别在 Mac 和 Windows 上用 Claude Code、Codex 和 Antigravity,两个人不用 git,共同维护一个题库、一个网站和一个共享盘。

## 许可

MIT。先在一个测试仓库里运行 `team-sync init`,再运行 `team-sync selftest` 确认各项检查通过。
