# Local Job Monitor

本项目在本机定期保存六家公司的官方公开职位，不使用任何本地或云端 AI，不分析职位与个人的匹配程度。目前处于实施顺序中的 **OpenAI 端到端样板阶段**；Apple、Meta、Broadcom、NVIDIA adapter 会在样板数据质量确认后加入。Google adapter 配置保留为 `blocked_by_source_policy`，因为官方 `robots.txt` 禁止招聘搜索结果分页，且尚未发现完整公开 feed。

## 当前样板能力

- 从 OpenAI 正式使用的 Ashby 公开 job-board API 一次性读取当前职位；
- 保存官方原始 JSON（gzip）、全部美国职位 JSON、每家公司独立 SQLite；
- 只用官方字段、标题排除关键词和可配置城市 allowlist 做确定性过滤；
- 旧金山—半岛—San Jose 湾区走廊保留；主或次地点明确列出 Remote US 的职位也保留；
- hybrid/onsite 必须至少有一个湾区地点或明确 Remote US 地点；模糊地点进入 review；
- 保存职位当前状态、重要内容版本和状态事件；
- 健康检查失败时不改变职位状态；连续 3 次健康抓取缺失才关闭；
- 只导出 UTF-8 JSON，不包含 AI 生成内容；
- 支持 dry-run、fetch-only、离线重解析、状态和健康查看。

## 目录

```text
config/                 可编辑配置和公司开关
migrations/             SQLite schema migration
src/job_monitor/        adapter、过滤、存储、归档、导出、健康检查
data/databases/         每家公司独立 SQLite
source/<company>/<date> 每次官方原始响应及请求元数据
original/<company>/     当前全部美国职位
results/                当前符合基础过滤条件、供用户和 ChatGPT Work 读取的 JSON
logs/                   每日 Markdown 摘要及 JSONL 机器日志
tests/                  离线 fixture 和自动测试
```

`original` 与 `results` 的含义不同：前者保存全部美国公开职位用于查缺补漏；后者只包含当前开放、发布时间在滚动 90 天窗口内、符合 employment/location 基础过滤的职位。缺少官方发布时间的职位无法可靠判断年龄，因此继续保留并明确留空。

OpenAI 的 user-facing results 使用公司专属的 `openai.v1` 结构。完整 JD 放在 `description.plain_text`，官方标题拆出的分节放在同一个 `description` 对象中。分节本来就是完整 JD 的子集，因此会有必要的文本重叠；原始 HTML 和嵌套官方 payload 只留在 SQLite、`original` 和 `source`，不会重复放进 results。

## 安装（样板阶段）

要求 macOS 和 Python 3.12 或更高版本：

```bash
cd ~/jobs
./scripts/setup.sh
```

安装过程不需要第三方 Python 运行依赖。备份位置保存在 `config/settings.toml` 的 `backup_directory`，完整备份和 launchd 安装会在六家公司 adapter 完成后按实施顺序启用。

## 常用命令

```bash
# 只读联网验证，不写 source/original/results/数据库/日志
./scripts/jobs-monitor run --company openai --dry-run

# 保存官方原始响应和 original，但不更新职位状态
./scripts/jobs-monitor run --company openai --fetch-only

# 正常运行 OpenAI 样板
./scripts/jobs-monitor run --company openai

# 导出当前 eligible、更新、关闭和地点待复核职位
./scripts/jobs-monitor export --company openai --mode current
./scripts/jobs-monitor export --company openai --mode updated --since 2026-07-01
./scripts/jobs-monitor export --company openai --mode closed
./scripts/jobs-monitor export --company openai --mode review

# 查看运行和 adapter 健康状态
./scripts/jobs-monitor status --company openai
./scripts/jobs-monitor health --company all

# 从已归档 gzip JSON 离线验证解析器
./scripts/jobs-monitor reparse --company openai --archive source/openai/YYYY-MM-DD/HHMMSS.json.gz
# 确认该归档是完整 feed 后，显式更新数据库和导出
./scripts/jobs-monitor reparse --company openai --archive source/openai/YYYY-MM-DD/HHMMSS.json.gz --apply

# 自动测试
./scripts/test.sh
```

## 状态安全规则

一次请求失败、空响应、HTTP 非 2xx、职位总数相对最近健康运行异常下降超过 60%，都会令该次运行变为 unhealthy。Unhealthy 运行仍会保存可取得的原始响应和错误日志，但不会覆盖 `original`、不会更新职位，也不会累计缺失次数。

健康运行中某职位第一次缺失时标记 `possibly_closed`。连续 3 次健康运行缺失才标记 `closed`；closed job ID 再次出现则为 `reopened`。同一 ID 的官方内容 hash 改变则为 `updated`。

90 天是每次成功状态更新都会重新计算的滚动窗口，不再只作用于首次 baseline。超过 90 天的职位会连同其数据库版本和事件历史一起删除；删除发生后 SQLite 会 compact，以把空闲页归还磁盘。缺少官方发布时间的职位保留，同时明确将时间字段留空。`original/current_open_us_jobs.json` 仍保存当前全部美国职位，原始抓取仍按长期审计要求保存在 `source`。

## 访问与合规边界

adapter 只使用无需登录的官方 careers 页面、公司正式 ATS 的公开接口或公开结构化内容。程序不保存 cookie/token，不破解验证码，不轮换 IP，不伪装浏览器，不绕过 robots 或访问控制。遇到 401/403 立即熔断；429/5xx 只进行有限退避重试。网站所有者可以随时更改或停止公开访问，因此系统报告可用性，不能承诺永不受限。
