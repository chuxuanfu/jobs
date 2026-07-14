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
| Google | `google.com/about/careers` | 已实现受限增量模式。每天只读取官方 `Full-time + San Francisco Bay Area + Sort by date` 搜索的前 20 条，不访问 robots 禁止的 `page=` 分页。第一次仅登记 ID、不建立 baseline；之后只抓取新出现 ID 的官方详情。它不是完整职位清单，也不根据缺失判断关闭。 |

任何站点都可能改变公开接口或页面结构。系统通过限速、有限重试、解析测试和健康检查降低风险，但不能承诺官方来源永远不变。401/403 会立即停止，不会尝试绕过；429/5xx 只做有限退避重试。

## 数据范围

- `original/<company>/current_open_us_jobs.json`：完整快照 adapter 保存本次抓到的全部美国公开职位，文件每次原子覆盖。Google 文件只代表当次新增详情并带 coverage 声明，不能解释为全部美国职位。
- `results/<company>_open_eligible_jobs.json`：当前开放、在滚动 90 天窗口内、且满足 full-time 与湾区/Remote US 基础过滤的职位。
- `source/<company>/<date>/`：每次官方原始 JSON/HTML 的 gzip 文件和 manifest，作为长期审计档案，不受 90 天清理影响。
- `data/databases/<company>_jobs.sqlite`：每家公司独立 SQLite，保存当前结构化职位、版本、事件和运行记录；不创建合并职位数据库。
- `logs/YYYY-MM-DD.md` 和 `.jsonl`：仅本地运行摘要和错误；不发送系统通知。

`results` 为减少重复，完整 JD 只在 `description.full_text` 出现一次。数据库仍保存 responsibilities、qualifications、benefits 等官方结构化分节，原始 HTML/JSON 仍在数据库、`original` 与 `source`，但不会在面向用户的 JSON 中再次复制整段内容。

Google 使用独立 `google_jobs.sqlite`。第一次健康运行只把官方前 20 条 ID 写入 `discovery_ids`，不会把既有职位错误计为新增；从第二次开始，只读取前 20 条中从未见过的 ID 的详情。`google_open_eligible_jobs.json` 顶层 `coverage.mode=incremental_first_page_only`，并明确写明无 baseline、最多 20 条、不能追踪关闭。一天内如果新增超过 20 条，超出部分可能永远无法被该模式发现。

## 过滤规则

Employment type 优先使用官方结构化字段。`Full-time`、`Full time`、`Regular`、`Regular Employee`、`Standard` 等官方常规全职值可以通过；标题包含 intern、part-time、contract、temporary、seasonal 或 vendor 等排除词时直接排除。官方字段和标题都无法确定时不会猜测，而是 `employment_review_required`。

地点范围是旧金山向南沿半岛到 San Jose，并包含 Fremont/Newark。允许城市在 `config/settings.toml` 的 `eligible_cities` 中维护；官方明确写出的 `San Francisco Bay Area`、`Bay Area`、`Silicon Valley` 或 `South Bay` 也可通过。明确 Remote US 的职位保留。Hybrid/onsite 必须至少有一个允许的湾区地点；无法可靠判断的地点标记 `location_review_required`。

90 天是每次健康状态更新都重新计算的滚动窗口，不只用于首次 baseline。官方 `posted_at` 明确早于窗口的职位，会从该公司数据库、版本历史、事件历史和 `results` 删除，然后 compact SQLite。完整快照 adapter 缺少发布时间时保持 NULL，不会把估算日期冒充官方日期。Google 没有发布时间且明确采用“从启用后开始观察”的增量模式，因此用本机 `first_seen_at` 作为 90 天清理基准，但不会把它输出成官方发布时间。

## 目录

```text
config/companies/       公司开关、官方端点和 adapter 版本
config/settings.toml    版本控制中的时区、09:00 调度、90 天和湾区城市
config/settings.local.toml setup 自动生成的本机备份目录；不进入 Git
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

已经能够 `git clone` 的 fresh Mac 只需要执行：

```bash
git clone git@github.com:chuxuanfu/jobs.git ~/jobs
cd ~/jobs
./setup.sh
```

`setup.sh` 完全非交互：如果没有 Python 3.12，会通过 uv 官方 standalone installer 自动安装独立 Python；随后重建 `.venv`、创建或迁移六个 SQLite、创建默认备份目录 `~/JobsMonitorBackup`、安装每天 09:00 的 LaunchAgent，并立即启动第一次后台运行。无需 Homebrew，也不要求终端保持打开。

本机覆盖配置写在不上传 Git 的 `config/settings.local.toml`，可以随时编辑：

```toml
backup_directory = "/Volumes/YourBackup/jobs"
```

launchd 使用 Mac 的系统时区；目标 Mac 应设置为 `America/Los_Angeles`。安装和卸载调度也可单独执行：

```bash
./scripts/install-launchd.sh
./scripts/uninstall-launchd.sh
```

第一次完整抓取会读取大量官方详情页，可能需要较长时间；2026-07-13 的真实 Apple baseline（约 4,747 个索引职位）约耗时 93 分钟。后续使用近期刷新和确定性的 7 日轮换，仍会按请求间隔礼貌访问。不要同时启动多个状态写入任务；锁文件会拒绝第二个任务。

### 从旧 Mac 用 AirDrop 迁移全部本地数据

不要只复制单个 `.sqlite`，因为 SQLite 可能还有 WAL 数据。旧 Mac 停止调度后运行：

```bash
./scripts/create-transfer-bundle.sh
```

脚本会使用 SQLite backup API 生成一致性数据库副本，并把代码、六个数据库、`source`、`original`、`results`、`logs` 打成桌面上的 `jobs-transfer-*.zip`；不会包含 `.venv`、Git 历史或敏感凭据。AirDrop 后解压，在新 Mac 的文件夹里直接运行 `./setup.sh`。setup 会保留并迁移已有数据库，然后安装新机的 launchd。不要同时在两台 Mac 上启用调度。

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

上一段关闭规则只适用于完整快照 adapter。Google 的前 20 条是增量发现窗口，缺失可能只是职位被挤到第二页，因此 Google 永远不会根据前 20 条缺失标记关闭；其捕获记录在 90 天后按 `first_seen_at` 清理。

空响应、HTTP 非 2xx、adapter 明确报错、解析结果为空、相对最近健康抓取异常下降超过 60%，都会使该公司本次运行变为 unhealthy。Unhealthy 运行保存能取得的原始响应和错误日志，但不覆盖 `original`、不更新职位、不累计关闭次数。单家公司失败不会阻止其他公司运行。所有未完成的运行记录会在异常边界中明确终止，避免数据库长期留下“运行中”假状态。

## SQLite 主要内容

每个数据库使用相同的基础 schema，公司特有的完整官方 payload 存在 JSON 字段中：

- `jobs`：当前结构化字段、地点 JSON、compensation JSON、完整官方 payload、hash、eligibility 与状态。
- `job_versions`：new/updated/reopened 时的结构化快照。
- `job_events`：new、updated、possibly_closed、closed、reopened 历史事件。
- `runs`：抓取总数、美国职位数、eligible 数、变化统计、缺失字段数、健康与归档路径。
- `discovery_ids`：Google 已观察到的轻量 job ID tombstone，用于避免第一次建立 baseline 和避免重复把旧职位记为新增；不含 JD 正文。
- `schema_migrations`：已应用 migration 版本。

数据库迁移按 `migrations/NNN_*.sql` 顺序且每个版本只执行一次。SQLite 备份使用 SQLite backup API，不直接复制可能仍有 WAL 的数据库文件。

## 维护指南

1. 每天先看 `logs/YYYY-MM-DD.md` 的 unhealthy、count drop 和 parser warning。
2. 页面变化时先保存失败的 `source` manifest，更新该公司 adapter 与离线测试，再用 `reparse` 验证；不要直接放宽健康检查。
3. 调整湾区范围只编辑 `eligible_cities` / `eligible_region_labels`，随后重新运行；不使用地理编码收费服务。
4. 改变 adapter 解析逻辑时提升该公司 `adapter_version`。
5. 添加公司时实现 `SourceAdapter`、独立配置、独立 SQLite、真实公开 fixture 和健康测试。
6. `source` 按需求长期保存，因而会自然增长；数据库、`original` 和 `results` 不采用追加文件。需要缩减 raw archive 时必须单独制定保留政策，当前程序不会擅自删除审计数据。
