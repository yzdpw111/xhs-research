---
name: xhs-research
description: 小红书调研 — 搜索笔记、抓取详情/评论/图片 OCR，CDP Chrome 自动化（反爬）
---

# 小红书 Research

小红书笔记搜索、详情、评论、图片 OCR，基于 CDP 裸 Chrome + 人类行为模拟规避反爬。输出 stdout JSON。

## 安装

1. 确认 Chrome 已安装（`C:\Program Files\Google\Chrome\Application\chrome.exe`）
2. 安装依赖：`pip install -r requirements.txt`（含 `rapidocr-onnxruntime`，首次 OCR 自动下载模型）
3. 启动 Chrome 会话：`python chrome_session.py --start`，在弹出的 Chrome 中登录小红书（扫码/手机号）；登录态持久保存，`--status` 查看、`--stop` 关闭

profile 位于 `%USERPROFILE%\.yzdpw_state\chrome-cdp`，CDP 端口记录于同目录 `.cdp_port`。Chrome 用 `--remote-debugging-port` 裸启动（不带 `--enable-automation`），`navigator.webdriver=false`。

**并行与反爬**：`--parallel N`（默认 2）并行时会移除串行限速、请求频率更高，风控风险上升；`--parallel 1` 时保留人类行为模拟（随机滚动/停顿/限速）。

## 输出落盘

两个脚本 stdout 输出完整 JSON 的同时，自动把**同一份完整结果**写入 `<XHS_LOGS_DIR>/<脚本名>-<时间戳>.json`（默认 `logs/`，即**脚本启动时的工作目录**下，与 ieee/wanfang 一致），并在 stderr 与 stdout 的 `logPath` 字段给出绝对路径。

用途：宿主对 stdout 有大小上限，结果条数一多会被截断；需要全文时直接读该文件。文件内容不含 `logPath`，是纯结果；落盘发生在写 stdout **之前**，stdout 即使失败也不丢结果。

## 命令

### xhs_search.py

搜索笔记，支持多关键词筛选（排序/类型/时间/范围/距离）。

| 参数 | 必填 | 默认 | 说明 |
|------|:--:|------|------|
| `--q` | ✅ | — | 搜索关键词（可重复） |
| `--rows` | ❌ | 25 | 最大结果数（1-100） |
| `--sort` | ❌ | 综合 | 综合 / 最新 / 最多点赞 / 最多评论 / 最多收藏 |
| `--type` | ❌ | 不限 | 不限 / 视频 / 图文 |
| `--time` | ❌ | 不限 | 不限 / 一天内 / 一周内 / 半年内 |
| `--scope` | ❌ | 不限 | 不限 / 已看过 / 未看过 / 已关注 |
| `--distance` | ❌ | 不限 | 不限 / 同城 / 附近 |
| `--parallel` | ❌ | 2 | 并行关键词数（1-8） |

**输出：** `{ filters, count, results, logPath }`，每条 result 含 `keyword`, `total`, `items[{ noteId, title, link, author, time, likes, type }]`，`link` 含 `xsec_token`（详情页用）。

- 无结果 `notice: "无搜索结果"`；登录失效 `error: "需要登录"`；风控 `error: "触发风控/验证，请稍后再试"`
- 参数校验：`--q` 必填；筛选值非法直接退出

### xhs_detail.py

抓笔记详情，含正文、评论（递归回复）、图片下载 + OCR。

| 参数 | 必填 | 默认 | 说明 |
|------|:--:|------|------|
| `--url` | ⚠️ | — | 笔记 URL（可重复）；与 stdin 管道二选一，至少一个 |
| `--max-comments` | ❌ | 30 | 每篇最大顶级评论数（1-80） |
| `--reply-limit` | ❌ | 2 | 每条评论最多回复数（>1 时点「展开」抓取） |
| `--no-ocr` | ❌ | — | 跳过 OCR（仅下载图片） |
| `--media-dir` | ❌ | — | 媒体保存目录；不指定则图片临时 OCR 后删、视频只记 URL |
| `--parallel` | ❌ | 2 | 并行 URL 数（1-8） |

**输出：** `{ count, succeeded, failed, notes, logPath }`，每条 note 含 `noteId, type, title, author, date, desc, likes, collects, commentCount, comments[{ author, content, date, location, likes, replies }]`，图文另含 `ocrText`，视频另含 `videoUrl`。

**用法：**
```powershell
# 直接抓单个笔记
python xhs_detail.py --url "https://www.xiaohongshu.com/explore/xxx?xsec_token=..." --max-comments 10

# 搜索 → 详情（管道）
python xhs_search.py --q "机器学习" --rows 5 | python xhs_detail.py --max-comments 10

# 持久保存图片/视频
python xhs_detail.py --url "..." --media-dir "D:\xhs-media"
```

**错误：** 404/笔记不存在 → `"笔记不存在或 token 失效（重定向到首页）"`；登录失效 → `"需要登录"`；风控 → `"触发风控/验证"`

## 配置（环境变量，前缀 `XHS_`）

脚本内置 `config.py` 集中配置，可用 `XHS_*` 环境变量覆盖：

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `XHS_RATE_LIMIT` | `8,18` | 请求限速范围(s)，逗号分隔；调低加快、调高更安全 |
| `XHS_PARALLEL_LIMIT` | `2` | 并行上限 |
| `XHS_TABS_MAX` | `30` | tab 数上限，达到即**只告警**（不自动关 tab；0=不启用） |
| `XHS_HUMAN_SCROLL_DELTA` | `150,500` | 滚动步长范围(px) |
| `XHS_HUMAN_PAUSE` | `0.5,4` | 滚动停顿范围(s) |
| `XHS_HUMAN_MOUSE_PROB` | `0.4` | 鼠标移动概率 |
| `XHS_TIMEOUT_CDP` | `30` | CDP WebSocket 超时(s) |
| `XHS_STATE_DIR` | `~/.yzdpw_state` | 状态目录（profile + 端口文件） |
| `XHS_LOGS_DIR` | `<运行目录>/logs` | 完整结果落盘目录（默认 = 脚本启动时的工作目录，可覆盖为固定路径） |

示例：`set XHS_RATE_LIMIT=3,5` 后运行脚本，限速从 8-18s 调为 3-5s。

## 已知限制

- 评论与回复默认限量：`--max-comments 30`、`--reply-limit 2`；`--reply-limit > 1` 会逐条点击「展开 x 条回复」，抓取耗时会明显增加。需要更多评论时显式调大，上限 `--max-comments 80`。落盘日志与 stdout 是同一次抓取的结果，不会额外多抓
- 每个 URL/关键词用一个 tab，用完即关（`close_page`）；tab 总数达 `XHS_TABS_MAX` 时**只告警不自动关** —— 四个 skill 共用一个 Chrome，自动关"空 tab"会误伤其他任务刚建好、还没 navigate 的 tab
- 若只剩最后一个 tab：`close_page` 会先建一个空白页（about:blank）占位再关它 —— 直接关会让整个共享 Chrome 退出，不关又会在浏览器里留下上次的搜索结果/详情页；占位建不出来时保留该 tab
- `xsec_token` 过期会导致详情页 404，需重新搜索获取新链接
- OCR 用 onnxruntime CPU 推理，多图较慢（单张约 15-25 秒）
- 视频笔记只记录 `videoUrl` 直链，默认不下载（`--media-dir` 时下载 mp4）

## 脚本清单

```
scripts/
  xhs_search.py       搜索（关键词 + 5 维筛选）
  xhs_detail.py       详情 + 评论/回复 + 图片 OCR + 视频直链
  xhs_parser.py       结果后处理纯函数
  cdp_base.py         CDP 客户端 + Chrome 裸启动 + 人类行为模拟
  ocr_images.py       RapidOCR 图片识别
  chrome_session.py   登录会话管理（--start/--status/--stop）
```
