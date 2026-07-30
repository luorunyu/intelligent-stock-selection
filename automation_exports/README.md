# Codex 自动化任务迁移包

本目录根据当前机器上的自动化配置恢复，供在另一台机器的 Codex App 中重新创建任务时使用。

## 已恢复任务

1. `A股收盘后观察池复盘.md`
   - 原任务 ID：`a`
   - 原状态：`ACTIVE`
   - 原计划：周一至周五 18:00（本地时区）
   - 原模型：`gpt-5.5`
   - 原工作目录：`D:\Projects\intelligent-stock-selection-system`

2. `A股早盘观察池复盘.md`
   - 原任务 ID：`a-2`
   - 原状态：`ACTIVE`
   - 原计划：每天 08:00（本地时区；任务正文会自行判断 A 股是否开市）
   - 原模型：`gpt-5.5`
   - 原工作目录：`D:\Projects\intelligent-stock-selection-system`

## 迁移方法

1. 将整个 `intelligent-stock-selection-system` 项目复制或通过代码仓库检出到目标机器。
2. 确认项目内 `.codex/skills`、`scripts`、`stock_selection` 和 `analysis_records` 均存在。
3. 在目标机器的 Codex App 中新建自动化任务，分别粘贴两个 Markdown 文件中“可直接粘贴的任务描述”部分。
4. 将任务工作目录设置为目标机器上的项目绝对路径。
5. 按上述时间重新设置计划，并确认目标机器时区正确。
6. 原任务使用 `gpt-5.5`；若目标机器没有该模型，选择其账号当前可用的模型，并先手动运行一次验证。
7. 邮件步骤只能使用经批准的公司内部邮箱。不要复制旧机器的 `.env`、授权码、token 或密码到聊天工具、公网邮箱或公网网盘；应在目标机器上重新安全配置。若没有合规的内部邮箱，删除或跳过邮件步骤。

## 原始文件仍在

- `C:\Users\runyu.luo\.codex\automations\a\automation.toml`
- `C:\Users\runyu.luo\.codex\automations\a-2\automation.toml`
- 每个目录中的 `memory.md` 是历史运行摘要，不是创建任务所必需，但可在迁移时作为历史上下文参考。

