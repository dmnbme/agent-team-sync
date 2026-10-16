# agent-team-sync

让一个小团队和他们各自的 AI 编程 agent 保持同步。

三个人、四种 agent(Claude Code、Codex、Antigravity、WorkBuddy)、一个仓库,其中两个人不会 git。文件在网盘、代码在 git,谁改了什么没人知道,重活并发会撞车。`team-sync` 把事情分成三层,各管一件事:

| 层 | 放什么 | 机制 |
|---|---|---|
| **git** | 代码、文档、skill、规格、数据指纹 | hook 在会话开始拉取、结束时提交推送;后台每 5 分钟再做一遍 |
| **共享网盘** | 大文件(Google Drive、OneDrive、Dropbox) | 只做路径解析,同步交给网盘客户端 |
| **Postgres**(Supabase 或任何) | 租约(互斥)、共享游标、团队口令 | RLS 全封的表,只经 security definer 函数访问 |

强制从不依赖模型「记得」某条规则: 租约做在脚本里,密钥门禁做在 git 里,后台同步做在系统调度器里。hook 只负责方便: 会话开头的摘要和结束时的推送。

## 安装

两种方式。checkout 只需要 git,而且不挑 hook 拿到的是哪个 `python3`:

```bash
git clone --branch v0.1.8 https://github.com/dmnbme/agent-team-sync ~/.team-sync/pkg   # 成员
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.8"  # 或:维护者,得到 team-sync 命令
```

用 `init` 配置过的仓库里,所有东西都走提交在仓库里的引导脚本 `bin/team_sync.py`,两种装法它都能找到包。

零依赖,Python 3.9 起(macOS 自带的够用)。Windows 装好 Python 后要有 `python3` 这个命令(把 `python.exe` 复制一份叫 `python3.exe` 即可)。

## 维护者配置仓库(一次)

```bash
cd 团队仓库
team-sync init --team "Acme" --prefix ACME --lang zh
team-sync db install --project-ref <supabase 项目 ref>      # 环境变量 SUPABASE_ACCESS_TOKEN,或 --dsn postgres://…
echo "ACME_TEAM_KEY=$(openssl rand -hex 24)" >> .env.team
team-sync db register-key --project-ref <ref>
team-sync db env-for bob                                    # 生成 ~/Desktop/env.team.txt 发给 Bob
team-sync autosync on
```

`team-sync.json` 里 `version` 是版本钉子: `start` 发现装的版本不一致会提醒,`team-sync upgrade` 装钉住的那个。

## 成员配置机器(一次)

```bash
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.8"
git clone <团队仓库> ~/team && cd ~/team
cp ~/Downloads/env.team.txt .env.team
team-sync start && team-sync autosync on && team-sync paths
```

然后用 agent 打开这个文件夹。`init` 提交进仓库的 hook 文件让每个受支持的 agent 自动跑 `team-sync`。

## 各 agent

| agent | 文件 | 事件 |
|---|---|---|
| Claude Code | `.claude/settings.json` | SessionStart → `start`;Stop → 对话记录(见下);SessionEnd → `stop` |
| Antigravity | `.agents/hooks.json` | PreInvocation → 每个对话注入一次摘要(`injectSteps`);Stop → 空闲时 `stop` |
| CodeBuddy / WorkBuddy | `.codebuddy/settings.json` | SessionStart → `start`,SessionEnd → `stop` |
| Codex | `.codex/hooks.json` | SessionStart → 摘要作为 `additionalContext` 注入;Stop → 每轮结束后写对话记录,再在独立进程里 `stop`。Codex 会先让你信任这个项目,再在 `/hooks` 里逐个信任钩子(各一次,钩子文件改了要再信任);不用 SessionEnd,Codex 只给它 3 秒,不够拉取加推送 |
| 其它 | 不需要 | 后台 autosync 照常跑;规则文件里写着「先跑 team-sync start」 |

指令文件只有一份正本 `AGENTS.md`,`CLAUDE.md` 和 `CODEBUDDY.md` 各一行 `@AGENTS.md`。

## 对话记录

可选。`team-sync.json` 里写上 `"convo": {"dir": "docs/对话记录"}`,Claude Code 和 Codex 每轮结束时的 hook 就会把用户和
agent 说的话写进 `<dir>/年-月/<日期>_<谁>_<agent>_<会话>.md`,一个会话一个文件,随其它改动一起提交。工具调用、工具输出、
思考过程和注入的上下文都不记;密钥形状的字符串按门禁的规则换成 `[redacted]`。新会话开头的摘要会列出最近 5 份记录,
agent 需要时自己打开。

原文超过 `compact_chars`(默认 30000 字)时,hook 在这一轮结束时提出请求:由正在运行的 agent 把较早的部分写成摘要,
运行 `team-sync convo compact --file F --through T`(摘要从标准输入给)。最新的 `keep_recent_chars`(默认 8000 字)保持原文。
不需要 API key,也不额外调用模型:摘要由当前的 agent 来写。

不记录:用户消息里出现 `[no-record]` 或「不记录这段对话」,这个会话的记录会被删掉,已经推送的部分仍在 git 历史里。
`TEAM_SYNC_NO_CONVO=1`,或者 env 文件里写 `<前缀>_CONVO=off`,这台电脑就不记录。Antigravity 和 CodeBuddy 暂不支持。
仓库里的人都能看到这些记录,开启前告诉被记录的人。

## 租约

```python
from team_sync.leases import lease
with lease('collect:telegram:main', ttl=1800, note='fetch'):
    ...   # 拿不到直接 SystemExit 并说明谁在占用;后台每 ttl/3 秒续租
```

租约不是锁: 会过期,崩掉的 agent 不会把全队锁死。按资源不按人。持有者 id 是 `人@机器:进程`,同一台电脑开两个 agent 算两个持有者。

## 其它命令

```
team-sync note "做了什么 / 改了哪 / 下一步"
team-sync convo list | compact      对话记录
team-sync cursor show|pull|push
team-sync fingerprint [--check|--quick]
team-sync selftest          临时目录双 clone 模拟,约 20 秒
team-sync paths | status | version
```

MIT 许可。
