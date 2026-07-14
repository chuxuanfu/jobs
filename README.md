# Local Job Monitor

这是一个长期运行在 macOS 本机的确定性职位监控系统。它只采集公司官方招聘来源，保存原始响应和结构化字段，按 employment type 与湾区地点过滤，并追踪新增、更新、下架和重新开放。项目不调用任何本地或云端 AI，也不分析职位是否适合个人。

## 当前公司状态

| 公司 | 官方来源 | 状态与已知限制 |
|---|---|---|
| Apple | `jobs.apple.com` 搜索页及详情页内公开 hydration JSON | 已实现。搜索索引完整；详情采用首次完整抓取、近期职位刷新和 7 日轮换复查，以控制长期请求量。 |
| OpenAI | OpenAI 正式使用的 Ashby public job-board API | 已实现。单个公开 JSON feed 提供完整职位与 compensation。 |
| Meta | `metacareers.com/jobs/sitemap.xml` 官方职位 sitemap + 详情页 JobPosting JSON-LD | 已实现。JSON-LD 没有提供的薪资保持 NULL；不会从正文猜测。 |
| Broadcom | Broadcom 官方 Workday public CXS | 已实现。公开列表加公开详情 JSON。 |
| NVIDIA | NVIDIA 官方 Workday public CXS | 已实现。使用官方 United States facet，再读取公开详情 JSON。 |
| Google | `google.com/about/careers` | `blocked_by_source_policy`。官方 robots 明确禁止带 `page=` 的招聘结果分页，首页只给 20 条；没有找到完整、稳定且允许使用的公开 feed，因此没有绕过或实现不完整 scraper。 |

任何站点都可能改变公开接口或页面结构。系统通过限速、有限重试、解析测试和健康检查降低风险，但不能承诺官方来源永远不变。401/403 会立即停止，不会尝试绕过；429/5xx 只做有限退避重试。

## 数据范围

- `original/<company>/current_open_us_jobs.json`：该公司本次成功抓取到的全部美国公开职位，文件每次原子覆盖，不会逐次追加。
- `results/<company>_open_eligible_jobs.json`：当前开放、在滚动 90 天窗口内、且满足 full-time 与湾区/Remote US 基础过滤的职位。
- `source/<company>/<date>/`：每次官方原始 JSON/HTML 的 gzip 文件和 manifest，作为长期审计档案，不受 90 天清理影响。
- `data/databases/<company>_jobs.sqlite`：每家公司独立 SQLite，保存当前结构化职位、版本、事件和运行记录；不创建合并职位数据库。
- `logs/YYYY-MM-DD.md` 和 `.jsonl`：仅本地运行摘要和错误；不发送系统通知。

`results` 为减少重复，完整 JD 只在 `description.full_text` 出现一次。数据库仍保存 responsibilities、qualifications、benefits 等官方结构化分节，原始 HTML/JSON 仍在数据库、`original` 与 `source`，但不会在面向用户的 JSON 中再次复制整段内容。

Google 当前没有职位数据库；导出时仍会生成 `google_open_eligible_jobs.json`，其中 `status=blocked_by_source_policy`、`job_count=0` 和明确原因，避免把“尚未能合规采集”误解为“Google 当前没有职位”。

## 过滤规则

Employment type 优先使用官方结构化字段。`Full-time`、`Full time`、`Regular`、`Regular Employee`、`Standard` 等官方常规全职值可以通过；标题包含 intern、part-time、contract、temporary、seasonal 或 vendor 等排除词时直接排除。官方字段和标题都无法确定时不会猜测，而是 `employment_review_required`。

地点范围是旧金山向南沿半岛到 San Jose，并包含 Fremont/Newark。允许城市在 `config/settings.toml` 的 `eligible_cities` 中维护；官方明确写出的 `San Francisco Bay Area`、`Bay Area`、`Silicon Valley` 或 `South Bay` 也可通过。明确 Remote US 的职位保留。Hybrid/onsite 必须至少有一个允许的湾区地点；无法可靠判断的地点标记 `location_review_required`。

90 天是每次健康状态更新都重新计算的滚动窗口，不只用于首次 baseline。官方 `posted_at` 明确早于窗口的职位，会从该公司数据库、版本历史、事件历史和 `results` 删除，然后 compact SQLite。官方没有发布时间的职位无法证明已经超过 90 天，因此时间字段保持 NULL 并继续保留；系统不会把估算日期冒充官方日期。

## 目录

```text
config/companies/       公司开关、官方端点和 adapter 版本
config/settings.toml    时区、09:00 调度、90 天、湾区城市、备份目录
migrations/             按编号执行的 SQLite schema migration
src/job_monitor/adapters/ 六家公司独立 adapter 与统一基类
src/job_monitor/        过滤、归档、存储、健康检查、导出、报告、备份
data/databases/         每家公司独立 SQLite
source/                 按公司和日期归档的原始官方响应
original/               当前全部美国职位 JSON
results/                用户与 ChatGPT Work 读取的精简 JSON
logs/                   每日 Markdown、JSONL 和 launchd stdout/stderr
tests/                  离线公开 fixture/构造样本与自动测试
scripts/                运行、setup、launchd 安装/卸载脚本
```

## 新 Mac 安装

要求 macOS 和 Python 3.12 或更高版本：

```bash
cd ~/jobs
./scripts/setup.sh
```

`setup.sh` 会创建隔离的 `.venv`，询问备份目录，并询问是否安装每天 09:00 的 LaunchAgent。备份目录也可随时编辑：

```toml
backup_directory = "/Volumes/YourBackup/jobs"
```

launchd 使用 Mac 的系统时区；本项目要求系统时区保持 `America/Los_Angeles`。终端不需要一直打开。安装和卸载调度也可单独执行：

```bash
./scripts/install-launchd.sh
./scripts/uninstall-launchd.sh
```

第一次完整抓取会读取大量官方详情页，可能需要较长时间；2026-07-13 的真实 Apple baseline（约 4,747 个索引职位）约耗时 93 分钟。后续使用近期刷新和确定性的 7 日轮换，仍会按请求间隔礼貌访问。不要同时启动多个状态写入任务；锁文件会拒绝第二个任务。

## 常用命令

```bash
# 联网解析但不写数据库、source、original、results 或日志
./scripts/jobs-monitor run --company apple --dry-run

# 手动运行单家公司或所有启用公司
./scripts/jobs-monitor run --company openai
./scripts/jobs-monitor run --company all

# 保存原始响应和 original，但不更新职位状态
./scripts/jobs-monitor run --company meta --fetch-only

# 导出当前 eligible；all 会额外生成跨公司只读汇总 JSON
./scripts/jobs-monitor export --company apple --mode current
./scripts/jobs-monitor export --company all --mode current

# 其他导出模式
./scripts/jobs-monitor export --company openai --mode all_open
./scripts/jobs-monitor export --company openai --mode new_since --since 2026-07-01
./scripts/jobs-monitor export --company openai --mode updated --since 2026-07-01
./scripts/jobs-monitor export --company openai --mode closed
./scripts/jobs-monitor export --company openai --mode review

# 状态、健康和手动备份
./scripts/jobs-monitor status --company all
./scripts/jobs-monitor health --company all
./scripts/jobs-monitor backup

# 从单响应 gzip 或多响应 metadata manifest 离线重解析
./scripts/jobs-monitor reparse --company nvidia --archive source/nvidia/YYYY-MM-DD/HHMMSS.metadata.json
./scripts/jobs-monitor reparse --company nvidia --archive source/nvidia/YYYY-MM-DD/HHMMSS.metadata.json --apply

# 自动测试
./scripts/test.sh
```

## 状态与健康保护

同一官方 ID 第一次出现为 `new`；内容 hash 改变为 `updated`；未变化为 `unchanged`；关闭后再次出现为 `reopened`。健康抓取中第一次缺失为 `possibly_closed`，连续 3 次健康抓取缺失才变成 `closed`。

空响应、HTTP 非 2xx、adapter 明确报错、解析结果为空、相对最近健康抓取异常下降超过 60%，都会使该公司本次运行变为 unhealthy。Unhealthy 运行保存能取得的原始响应和错误日志，但不覆盖 `original`、不更新职位、不累计关闭次数。单家公司失败不会阻止其他公司运行。所有未完成的运行记录会在异常边界中明确终止，避免数据库长期留下“运行中”假状态。

## SQLite 主要内容

每个数据库使用相同的基础 schema，公司特有的完整官方 payload 存在 JSON 字段中：

- `jobs`：当前结构化字段、地点 JSON、compensation JSON、完整官方 payload、hash、eligibility 与状态。
- `job_versions`：new/updated/reopened 时的结构化快照。
- `job_events`：new、updated、possibly_closed、closed、reopened 历史事件。
- `runs`：抓取总数、美国职位数、eligible 数、变化统计、缺失字段数、健康与归档路径。
- `schema_migrations`：已应用 migration 版本。

数据库迁移按 `migrations/NNN_*.sql` 顺序且每个版本只执行一次。SQLite 备份使用 SQLite backup API，不直接复制可能仍有 WAL 的数据库文件。

## 维护指南

1. 每天先看 `logs/YYYY-MM-DD.md` 的 unhealthy、count drop 和 parser warning。
2. 页面变化时先保存失败的 `source` manifest，更新该公司 adapter 与离线测试，再用 `reparse` 验证；不要直接放宽健康检查。
3. 调整湾区范围只编辑 `eligible_cities` / `eligible_region_labels`，随后重新运行；不使用地理编码收费服务。
4. 改变 adapter 解析逻辑时提升该公司 `adapter_version`。
5. 添加公司时实现 `SourceAdapter`、独立配置、独立 SQLite、真实公开 fixture 和健康测试。
6. `source` 按需求长期保存，因而会自然增长；数据库、`original` 和 `results` 不采用追加文件。需要缩减 raw archive 时必须单独制定保留政策，当前程序不会擅自删除审计数据。
