# FinPolicy Radar 项目上下文

本文只描述当前仓库能够证明或维护者已经明确确认的状态。计划项不代表已经实现；仍无法确认的信息会明确标注为“需要验证”。

## 1. 产品名称与目标

- 产品名称：FinPolicy Radar（金策雷达）MVP 1.0。
- 形态：零数据库的中国金融科技政策监测静态网站。
- 目标：从官方站点发现候选政策，经规则过滤、分类和摘要后，生成可公开浏览的网页、JSON 与 RSS。
- 核心优先级：内容准确、官方原文可追溯、定时任务稳定，高于收录数量。

## 2. 当前已实现的能力

- 配置四个官方来源的列表入口、域名白名单和 URL 路径规则。
- 使用通用 HTML 解析发现链接并抓取详情页。
- 规范化 URL，去除部分跟踪参数，并按候选键和 URL 合并去重。
- 从详情页 `<h1>` 或列表标题提取标题，从页面文本或 URL 提取日期，从候选内容块中选择最长正文。
- 检测并拒绝 `{{...}}`、`{%...%}`、编码后的模板表达式及部分 `x.*`、`data.*` 绑定。
- 使用关键词相关性分数过滤政策。
- 使用规则生成分类、文件类型、重要性、摘要和影响提示。
- 将政策与采集状态保存在 JSON 文件中。
- 使用 Jinja2 生成静态首页和 RSS；浏览器端 JavaScript 提供搜索和筛选。
- 使用 GitHub Actions 每小时抓取、保存生成数据并部署 GitHub Pages。
- 支持 `--build-only`，在不联网的情况下使用已有数据重新生成站点。

## 3. 当前不支持的能力

- 不执行浏览器 JavaScript，不渲染 Angular、Vue 等前端应用。
- 没有 NFRA 或其他来源的专用 API 采集器。
- 不处理验证码、登录或必须携带身份凭据的页面。
- 不解析 PDF 正文，只能保留发现到的链接。
- 不使用数据库；没有面向历史版本的迁移机制。
- 不检测已收录 URL 的正文变更、修订、废止或失效状态。
- 不使用大模型 API；摘要和影响提示均为规则生成。
- 没有飞书、企业微信或其他主动通知集成。
- 没有后台管理界面或人工审核队列。
- GitHub Actions 当前不运行测试。

## 4. 目录结构与职责

| 路径 | 当前职责 |
| --- | --- |
| `src/finpolicy/radar.py` | 抓取、解析、过滤、合并、规则分析、静态构建和 CLI 入口 |
| `src/finpolicy/__init__.py` | 包版本号 |
| `config/sources.yaml` | 官方来源、入口 URL、允许域名、URL 包含/排除规则及来源权重 |
| `config/rules.yaml` | 抓取限制、相关性关键词、分类、文件类型和影响提示规则 |
| `data/policies.json` | 持久化政策记录，作为下一次运行的历史输入 |
| `data/status.json` | 最近一次采集的来源级运行状态 |
| `templates/index.html.j2` | 静态首页 Jinja2 模板 |
| `public/` | GitHub Pages 发布产物：HTML、RSS、JSON 和前端资源 |
| `public/assets/app.js` | 浏览器端搜索、分类、来源和重要性筛选 |
| `public/assets/style.css` | 网站样式 |
| `tests/test_rules.py` | 当前规则、模板污染和合并行为测试 |
| `.github/workflows/radar.yml` | 定时抓取、生成数据、提交产物和 Pages 部署 |
| `requirements.txt` | 应用运行依赖；当前不包含 `pytest` |
| `README.md` | 面向使用者的项目介绍和基本运行说明 |

## 5. 端到端处理流程

### 5.1 配置加载

`run()` 从 `config/sources.yaml` 读取来源，从 `config/rules.yaml` 读取抓取和分析规则，并读取已有 `data/policies.json`。

### 5.2 候选发现

对每个来源按顺序处理其 `start_urls`：

1. 使用带重试的 `requests.Session` 获取列表页；
2. 用 BeautifulSoup 遍历带 `href` 的 `<a>`；
3. 清理标题，拒绝过短导航文案和含模板表达式的链接；
4. 将相对 URL 转为绝对 URL 并规范化；
5. 应用允许域名、包含路径和排除路径规则；
6. 从父节点文本或 URL 尝试提取列表日期；
7. 使用标题与 URL 的 SHA-256 截断值形成候选键；
8. 每个来源最多保留 `max_candidates_per_source` 个候选。

### 5.3 详情解析

1. 抓取候选详情页，并保留重定向后的官方 URL；
2. 移除脚本、样式、导航、页脚、表单和 iframe；
3. 优先使用不含模板表达式的 `<h1>`，否则回退到列表标题；
4. 在页面前 2500 个字符或 URL 中匹配日期；
5. 在预设内容选择器中选择最长且达到长度阈值的文本块；找不到时回退到合适长度的 `div` 或 `section`；
6. 将正文截断到 `max_article_chars`。

### 5.4 有效性与相关性过滤

- 候选 URL 含未解析模板表达式时跳过。
- 详情标题、URL 或正文仍含模板表达式时跳过。
- 标题和正文前 4000 字按关键词加权；低于 `minimum_relevance_score` 时跳过。
- 当前模板检测明确覆盖花括号模板及 `x.*`、`data.*`，并不等于完整的任意模板语言解析器。

### 5.5 规则分析与记录生成

- 分类：任一分类关键词命中即可进入相应分类；无命中时使用“其他金融科技政策”。
- 文件类型：按 `policy_type_keywords` 的配置顺序返回第一个命中类型。
- 重要性：来源权重、相关性、文件类型和标题关键词共同决定 1—5 星。
- 摘要：从正文中选择最多两句、约 150 字以上的有效句子，最终截断为 220 字；无可用句子时使用固定提示。
- 影响：使用首个匹配分类的固定模板，不是机构观点。

### 5.6 合并与持久化

- 合并前过滤标题、摘要或 URL 含模板表达式的历史和新增记录。
- 首先以 `id` 建索引，再以规范化 URL 识别同一政策。
- URL 已存在时保留原 `id` 和首次 `discovered_at`。
- 按发布日期或发现时间倒序保存到 `data/policies.json`。
- 已知 URL 在抓取阶段直接跳过，因此当前不会重新解析已收录页面，也不会发现同一 URL 的正文更新。

### 5.7 静态网站生成

- Jinja2 渲染 `templates/index.html.j2` 到 `public/index.html`。
- 同步生成 `public/policies.json`、`public/status.json` 和 `public/feed.xml`。
- 首页显示总数、今日数量、高重要性数量、来源状态和政策卡片。
- `public/assets/app.js` 只在浏览器内过滤已渲染卡片，不请求后端服务。

## 6. `policies.json` 字段与约束

当前记录结构包含以下字段，不得在普通 Bug 修复中擅自改变：

| 字段 | 类型/允许值 | 说明 |
| --- | --- | --- |
| `id` | 字符串 | 标题与规范化 URL 生成的 18 位哈希键；URL 合并时可沿用旧 ID |
| `title` | 字符串，必需 | 真实政策标题，不得含未解析模板 |
| `source_id` | 字符串 | `sources.yaml` 中的来源 ID |
| `source_name` | 字符串 | 展示用来源名称 |
| `published_at` | `YYYY-MM-DD` 或 `null` | 识别到的发布日期；无法可靠识别时允许为空 |
| `discovered_at` | ISO 8601 字符串 | 首次发现时间 |
| `updated_at` | ISO 8601 字符串 | 本次生成记录的时间；当前不会主动重抓已知 URL |
| `url` | 字符串，必需 | 真实官方原文 URL |
| `document_type` | 字符串 | 规则识别的文件类型或“政策信息” |
| `categories` | 字符串列表 | 一个或多个规则分类 |
| `keywords` | 字符串列表 | 最多保存前 10 个相关性命中词 |
| `relevance_score` | 整数 | 关键词权重之和 |
| `importance` | 1—5 整数 | 规则计算的重要性 |
| `summary` | 字符串 | 规则摘要，不得含未解析模板 |
| `impact` | 字符串 | 分类对应的规则影响提示 |
| `analysis_notice` | 字符串 | 非官方、以原文为准的声明 |

`content` 只在处理过程中使用，不写入最终政策记录。

## 7. 当前官方来源

全部配置位于 `config/sources.yaml`：

| `source_id` | 来源 |
| --- | --- |
| `pbc_news` | 中国人民银行 |
| `nfra_policy` | 国家金融监督管理总局 |
| `gov_policy` | 中国政府网 |
| `cac_policy` | 国家互联网信息办公室 |

来源网页可能改版或限制 GitHub 托管运行器 IP。配置存在不代表每次都能发现可解析记录。

## 8. GitHub Actions 自动运行方式

工作流名称为 `Update FinPolicy Radar`，位于 `.github/workflows/radar.yml`。

- 触发：手动运行、每小时第 17 分钟的 cron、`main` 上非纯 `data/**`/`public/**` 的 push。
- 环境：Ubuntu、Python 3.12、安装 `requirements.txt`。
- 主步骤：抓取并构建 → 暂存 `data` 和 `public` → 有变化时由机器人提交 → `git pull --rebase origin main` → push `main`。
- 并发：同一并发组不取消正在运行的任务。
- 当前工作流没有执行 `pytest`。

## 9. GitHub Pages 部署方式

同一工作流将 `public/` 上传为 Pages artifact；独立的 `deploy` job 等待构建 job 成功后，通过 `actions/deploy-pages` 发布。

- 当前 GitHub Pages 地址：<https://dontbetoolate.github.io/finpolicy-radar/>
- 当前没有自定义域名。

## 10. 本地开发与测试

初始化：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
```

测试：

```bash
python -m pytest -q
```

联网抓取并构建：

```bash
python -m src.finpolicy.radar
```

仅用现有数据构建：

```bash
python -m src.finpolicy.radar --build-only
```

本地预览：

```bash
python -m http.server 8000 -d public
```

模板污染检查：

```bash
rg -n '\{\{|\{%|x\.docSubtitle|data\.publishDate|data\.docSource' data public
```

注意：构建命令会重写部分 `data/` 和 `public/` 文件及生成时间；测试前后应检查 `git status`。

## 11. Git 与上线流程

推荐流程：

1. 从最新 `main` 创建任务分支；
2. 在任务分支完成范围内修改；
3. 运行测试和必要构建，检查生成文件与 `git diff`；
4. 得到明确授权后 commit；
5. 得到明确授权后 push；
6. 创建 `任务分支 → main` 的 Pull Request；
7. 复核文件和测试后合并；
8. 等待 `Update FinPolicy Radar` 和 Pages 部署成功；
9. 验证线上结果，再同步本地 `main` 并删除已合并分支。

任何自动 commit、push、合并或发布动作都需要维护者明确授权。

## 12. 当前已知限制与风险

- 政府网页结构变化、反爬措施、网络波动，以及官方站点对 GitHub Actions 运行器的访问限制，可能导致来源失败或零候选。这是持续运行风险，不是已经解决的问题；当前通过 `status.json` 和来源失败隔离降低影响，后续仍需增加监控。
- 通用最长正文块和前 2500 字日期提取可能误选导航、页面日期或过宽内容。
- 关键词规则可能产生误收录、漏收录或分类重叠。
- 已知 URL 不重抓，无法识别原文后续修订。
- 模板表达式检测不是完整 JavaScript/模板语言解析器，新增污染形式需要测试覆盖。
- 来源级异常会写入状态；因不相关或模板污染而主动跳过的单条记录没有独立失败历史。为每条跳过记录保存详细失败审计不属于 MVP 必做项，作为后续改进保留。
- `pytest` 未列入 `requirements.txt`，且 GitHub Actions 不运行测试。
- `public/feed.xml` 当前包含意外的字面量 `f"` 片段；XML 可解析，但 RSS 内容质量需要单独修复和客户端验证。
- 当前阶段暂不正式开源，但 `README.md` 的限制性声明与根目录 MIT `LICENSE` 互相冲突。该问题需要建立独立任务处理，本次文档工作不删除或修改 `LICENSE`。
- 仓库分支保护和人工审核要求无法仅从仓库文件确认。

## 13. 后续最合理的开发顺序

以下是建议，不代表已实现：

1. 修复并测试 RSS 输出中的字面量字符串问题。
2. 将测试依赖和 CI 测试步骤规范化，确保 PR 自动运行测试。
3. 建立独立任务，明确开源策略并统一 README 与 `LICENSE` 的授权声明。
4. 增强采集状态；详细的逐条跳过失败审计作为 MVP 之后的改进评估。
5. 为 JavaScript 驱动且经常失败的来源增加经过验证的专用采集器。
6. 增加正文质量、发布日期和官方 URL 的来源级回归样本。
7. 设计同 URL 政策修订与失效检测。
8. 在上述基础稳定后，再评估新增来源、通知集成或可选大模型分析。

## 14. 需要验证的信息

- 仓库是否配置分支保护、必需审查或其他合并规则。
- 何种数据规模或运行成本应触发存储方案迁移。

## 15. 当前阶段已确认的维护策略

### 授权状态

当前阶段暂不正式开源。仓库现有 MIT `LICENSE` 与 README 的限制性声明存在冲突；该冲突留给独立任务处理，本次不删除 `LICENSE`，也不将任一表述解释为已经完成最终授权决策。

### 生成数据与发布产物

MVP 阶段继续将 `data/` 和 `public/` 提交到仓库：

- `data/` 用于保存政策历史和最近运行状态；
- `public/` 支持当前 GitHub Pages 自动化发布流程；
- 现有 GitHub Actions 会在抓取后自动提交这两个目录的变化。

未来数据量扩大后，再评估独立数据分支、GitHub Release、对象存储或数据库。当前尚未选定迁移方案或触发阈值。

### 失败审计与持续运行

- 逐条保存所有跳过记录的详细失败审计暂不属于 MVP 必做项，列入后续改进。
- 官方网站对 GitHub Actions 运行器的长期可访问性是持续风险。当前已有来源级 `status.json` 和失败隔离；后续监控用于降低发现延迟和影响范围，不能视为彻底解决外部可访问性问题。
