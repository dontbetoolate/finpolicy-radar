# FinPolicy Radar 开发规则

开始任何任务前，必须先阅读：

- `docs/CONTEXT.md`
- `docs/DECISIONS.md` 中与任务相关的决策

## 项目目标

FinPolicy Radar（金策雷达）用于自动发现、整理和展示中国官方金融科技相关政策。准确性、官方来源可追溯性和自动运行稳定性，高于收录数量。

## 不可违反的规则

- 每条有效政策必须保留真实、可访问的官方原文 URL。
- 不得编造标题、发布日期、发布机构、正文、摘要或影响分析。
- 不得将 `{{...}}`、`{%...%}`、JavaScript、Angular、Vue 或其他未解析模板表达式当作政策内容。
- 无法可靠解析时应跳过该记录并保留失败状态，不得生成虚假字段。
- 单个政策来源失败不得阻断其他来源。
- 不得提交 API Key、Token、密码、Cookie、账号信息或个人数据。
- 不得擅自改变 `policies.json` 的数据结构。
- 不得删除历史政策数据，除非确认其为错误、重复或污染记录。
- Bug 修复不得夹带无关重构。
- 默认只在当前本地分支修改，不得直接向 `main` 强制推送。
- 未经明确要求，不得执行 `git commit`、`git push`、合并分支或发布操作。

## 开始任务前

1. 阅读 `docs/CONTEXT.md`。
2. 阅读 `docs/DECISIONS.md` 中与任务相关的决策。
3. 检查当前分支和 `git status`。
4. 阅读相关实现和测试后再修改，不得先猜测根因。
5. 如果工作区存在不属于当前任务的修改，停止并报告。

## 完成任务前

原则上运行：

```bash
python -m pytest -q
python -m src.finpolicy.radar --build-only
```

并根据任务检查：

```bash
rg -n '\{\{|\{%|x\.docSubtitle|data\.publishDate|data\.docSource' data public
```

如果本机没有 `rg`，可使用：

```bash
grep -RInE '\{\{|\{%|x\.docSubtitle|data\.publishDate|data\.docSource' data public
```

若由于环境或网络原因无法完成某项测试，必须明确说明，不能声称测试通过。运行构建可能修改 `data/` 或 `public/`，完成前必须检查并说明这些变化。

## Git 操作规则

默认：

- 可以在当前任务分支修改代码和文档；
- 可以运行必要测试；
- 不自动 commit；
- 不自动 push；
- 不自动 merge 或发布。

完成任务后必须告诉维护者：

1. 是否建议 commit；
2. 推荐 commit message；
3. 是否建议 push；
4. 是否会影响线上部署。

只有得到明确指令后，才执行 commit、push、merge 或发布。

## 小白维护者模式

- 每次任务完成报告末尾必须增加“维护者下一步”。
- “维护者下一步”必须明确显示：
  1. 当前分支；
  2. `git status` 是否干净；
  3. 测试是否通过；
  4. 是否建议 commit；
  5. 推荐的 commit message；
  6. 是否建议 push；
  7. 是否需要 Pull Request；
  8. 是否会触发线上部署；
  9. 建议维护者下一步只做哪一件事。
- 默认不得自动 commit、push、merge 或删除分支。
- 只有维护者明确授权后，才能执行 Git 写操作。
- 如果维护者只说“继续”，一次只执行一个安全步骤，不连续完成多个不可逆操作。
- 如果发现工作区不干净、分支不正确、测试失败或远端存在冲突，立即停止并解释，不得自行强制处理。
- 使用适合初学者的语言解释 Git 操作，不假设维护者了解 rebase、force push 或 detached HEAD；必须说明命令会改变什么以及能否撤销。

## 完成报告

必须说明：

1. 根因或实现方案；
2. 修改文件；
3. 测试结果；
4. 是否生成或修改了 `data/`、`public/`；
5. 残余风险；
6. 尚未执行的操作。

报告末尾必须按“小白维护者模式”给出“维护者下一步”。
