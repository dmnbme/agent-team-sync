# agent-team-sync

让一个小团队和他们各自的 AI 编程 agent 保持同步。

三个人、三种 agent(Claude Code、Antigravity、WorkBuddy)、一个仓库,其中两个人不会 git。文件在网盘、代码在 git,谁改了什么没人知道,重活并发会撞车。`team-sync` 把事情分成三层,各管一件事:

| 层 | 放什么 | 机制 |
|---|---|---|
| **git** | 代码、文档、skill、规格、数据指纹 | hook 在会话开始拉取、结束时提交推送;后台每 5 分钟再做一遍 |
| **共享网盘** | 大文件(Google Drive、OneDrive、Dropbox) | 只做路径解析,同步交给网盘客户端 |
| **Postgres**(Supabase 或任何) | 租约(互斥)、共享游标、团队口令 | RLS 全封的表,只经 security definer 函数访问 |

强制从不依赖模型「记得」某条规则: 租约做在脚本里,密钥门禁做在 git 里,后台同步做在系统调度器里。hook 只负责方便: 会话开头的摘要和结束时的推送。

## 安装

```bash
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.1"
python3 -m team_sync version
```

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
pip install --user "agent-team-sync @ git+https://github.com/dmnbme/agent-team-sync@v0.1.1"
git clone <团队仓库> ~/team && cd ~/team
cp ~/Downloads/env.team.txt .env.team
team-sync start && team-sync autosync on && team-sync paths
```

然后用 agent 打开这个文件夹。`init` 提交进仓库的 hook 文件让每个受支持的 agent 自动跑 `team-sync`。

## 各 agent

| agent | 文件 | 事件 |
|---|---|---|
| Claude Code | `.claude/settings.json` | SessionStart → `start`,SessionEnd → `stop` |
| Antigravity | `.agents/hooks.json` | PreInvocation → 每个对话注入一次摘要(`injectSteps`);Stop → 空闲时 `stop` |
| CodeBuddy / WorkBuddy | `.codebuddy/settings.json` | SessionStart → `start`,SessionEnd → `stop` |
| 其它 | 不需要 | 后台 autosync 照常跑;规则文件里写着「先跑 team-sync start」 |

指令文件只有一份正本 `AGENTS.md`,`CLAUDE.md` 和 `CODEBUDDY.md` 各一行 `@AGENTS.md`。

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
team-sync cursor show|pull|push
team-sync fingerprint [--check|--quick]
team-sync selftest          临时目录双 clone 模拟,约 20 秒
team-sync paths | status | version
```

MIT 许可。
