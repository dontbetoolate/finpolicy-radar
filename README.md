# 金策雷达 MVP 1.0

一个零数据库、可自动运行的中国金融科技政策监测网页。

## MVP能力

- 定时检查中国人民银行、国家金融监督管理总局、中国政府网、国家网信办；
- 自动发现候选政策页面；
- 关键词相关性过滤；
- URL与标题去重；
- 规则分类、政策类型识别、1—5星重要性；
- 从官方正文生成基础摘要和可能影响提示；
- 生成响应式静态网页和RSS；
- 每条记录保留官方原文链接；
- GitHub Actions每小时自动执行并部署GitHub Pages。

> 摘要与影响提示均为规则自动生成，不代表发布机构观点，必须以官方原文为准。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m src.finpolicy.radar
python -m http.server 8000 -d public
```

浏览器打开 `http://localhost:8000`。

仅使用已有数据重新生成网页：

```bash
python -m src.finpolicy.radar --build-only
```

## 部署到GitHub Pages

1. 新建一个公开GitHub仓库。
2. 将本项目全部文件推送到仓库的 `main` 分支。
3. 打开 `Settings → Pages`。
4. 在 `Build and deployment → Source` 中选择 `GitHub Actions`。
5. 打开 `Actions`，手动运行一次 `Update FinPolicy Radar`。
6. 成功后，网页地址通常为：`https://你的用户名.github.io/仓库名/`。

工作流将在每小时第17分钟执行。GitHub定时任务不是严格实时，可能出现延迟；可在Actions页面随时手动执行。

## 调整监控来源

编辑 `config/sources.yaml`。每个来源使用：

- `start_urls`：官方列表页；
- `allowed_domains`：允许域名；
- `include_url_patterns`：只保留包含这些路径片段的链接；
- `exclude_url_patterns`：排除路径；
- `source_weight`：权威性权重。

政府网站改版后，通常只需要修改该配置；若页面完全改为接口或JavaScript渲染，再新增专用采集器。

## 调整分类与相关性

编辑 `config/rules.yaml`：

- `relevance_keywords`：关键词及分数；
- `categories`：分类词典；
- `policy_type_keywords`：文件类型；
- `impact_templates`：规则影响提示。

## 已知边界

- 部分政府网站可能限制GitHub海外IP或临时改版；采集状态会显示失败来源，不会阻断其他来源。
- 当前为通用HTML解析器，不处理验证码、登录页面和强制JavaScript接口。
- PDF附件仅保留原链接，不对PDF做全文分析。
- 第一版不接大模型API，不产生调用费用。

## 后续最值得增加

1. 为经常失败的官网编写专用采集器；
2. 增加国家数据局、工信部、证监会；
3. 增加政策修订和失效检测；
4. 增加飞书或企业微信Webhook；
5. 可选大模型深度分析。

This project is currently under development. Source code is publicly available for transparency, but no license is granted for reuse.
本项目处于开发阶段，代码公开用于展示和交流，未经授权不得复制、商业使用或二次发布。
