---
name: boss-zhipin-scraper
description: "Scrape BOSS直聘 (job listing site) via Chrome CDP. Searches jobs by keyword/city/filters, fetches JD details, and outputs structured JSON/CSV with plaintext salary. Use when user wants to search/analyze jobs on BOSS直聘 or zhipin.com."
version: 2.0.0
author: eatmoreduck
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [scraper, jobs, career, cdp, chrome, zhipin, boss直聘]
---

# BOSS直聘职位抓取工具 v2.0

通过 Chrome CDP 协议抓取 BOSS直聘 (zhipin.com) 职位数据，输出结构化 JSON/CSV（含明文薪资）。

## 前置条件

- Chrome 浏览器已安装
- Python 3.10+
- 用户已登录 zhipin.com（或愿意手动登录）

## 脚本位置

本 skill 的脚本在 skill 目录下的 `scripts/boss_cdp_raw.py`。

**运行任何命令前，必须先确定脚本的绝对路径。**

用以下方式找到脚本：

```bash
# 方法 1：已知 skill 安装目录（推荐）
SCRIPT_PATH="$(dirname "$(readlink -f "$0")")/scripts/boss_cdp_raw.py"

# 方法 2：搜索 hermes skills 目录
SCRIPT_PATH=$(find ~/.hermes/skills -name "boss_cdp_raw.py" -type f 2>/dev/null | head -1)
```

如果找不到脚本，说明 skill 未正确安装，需要重新安装。

## 依赖安装（首次使用必须执行）

脚本依赖 `websocket-client` 和 `requests`。在用户项目的 venv 中安装：

```bash
uv add websocket-client requests
# 或
pip install websocket-client requests
```

## 自动化流程

当用户要求搜索/抓取 BOSS直聘 职位时，**严格按以下顺序执行**：

### 第 1 步：检查环境

```bash
python3 "$SCRIPT_PATH" --check --cdp-port 9222
```

检查三项：CDP 连通性 → Python 依赖 → 登录态。

- **全部通过** → 跳到第 3 步
- **CDP 不通** → 继续第 2 步
- **依赖缺失** → 先装依赖（见上方依赖安装），再重新 --check
- **未登录** → 告诉用户打开 Chrome 登录 zhipin.com，然后重新 --check

### 第 2 步：启动 Chrome CDP（仅在 --check CDP 不通时）

```bash
python3 "$SCRIPT_PATH" --setup-chrome --cdp-port 9222
```

这会自动完成：
1. 创建 Chrome profile 软链接到 `/tmp/chrome-cdp-profile`（避免 profile 锁冲突，Chrome 不允许多进程同时访问同一 profile）
2. 关闭现有 Chrome 实例
3. 以 CDP 模式启动 Chrome（`--remote-debugging-port=9222`）
4. 等待 CDP 端口就绪（最多 30 秒）

**启动后告诉用户：请在 Chrome 浏览器中访问 zhipin.com 并登录，登录后告诉我。**

等用户确认后，重新运行 `--check` 验证。

### 第 3 步：运行抓取

```bash
# 基础搜索
python3 "$SCRIPT_PATH" --keyword "关键词" --city 城市 --pages 3 --output /tmp/boss/jobs.json

# 带 CSV 输出
python3 "$SCRIPT_PATH" --keyword "关键词" --city 城市 --pages 3 --format csv --output /tmp/boss/jobs.json

# 带详情 + 分析报告
python3 "$SCRIPT_PATH" --keyword "关键词" --city 城市 --pages 3 --detail --max-details 8 --analysis --format csv --output /tmp/boss/jobs.json

# 合并多次抓取（去重）
python3 "$SCRIPT_PATH" --keyword "关键词" --city 北京 --pages 3 --merge /tmp/boss/jobs.json --output /tmp/boss/jobs_merged.json
```

默认输出到 `/tmp/boss/` 目录，`--format csv` 会额外生成 `.csv` 文件。

## 参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | AI Agent | 搜索关键词 |
| `--city` | 上海 | 城市名（中文）或代码 |
| `--pages` | 3 | 抓取页数（上限 10，每页 30 条） |
| `--output` | /tmp/boss/... | 列表输出路径 |
| `--detail-output` | /tmp/boss/... | 详情输出路径 |
| `--format` | json | 输出格式: json / csv |
| `--detail` | 关闭 | 同时抓取详情页 JD |
| `--max-details` | 全部 | 详情页数量上限 |
| `--analysis` | 关闭 | 输出分析报告 |
| `--merge FILE` | - | 合并已有 JSON（按 job_id 去重） |
| `--cdp-port` | 9222 | CDP 端口 |
| `--setup-chrome` | 关闭 | 一键启动 Chrome CDP（含软链接） |
| `--check` | 关闭 | 环境检查 |
| `--version` | - | 查看版本号 |

### 筛选参数

| 参数 | 值 |
|------|-----|
| `--scale` | 301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+ |
| `--salary` | 401=2K以下 402=2-5K 403=5-10K 404=10-15K 405=15-20K 406=20-50K 407=50K+ |
| `--experience` | 101=在校 102=应届 103=1年内 104=1-3年 105=3-5年 106=5-10年 107=10年+ |
| `--degree` | 204=大专 205=本科 206=硕士 207=博士 |

### 城市代码

北京 101010100 | 上海 101020100 | 广州 101280100 | 深圳 101280600 | 杭州 101210100 | 成都 101250100

## 输出格式

### JSON

```json
{
  "keyword": "AI Agent",
  "city": "上海",
  "total": 60,
  "jobs": [
    {
      "job_id": "c4420e8bce3a6e25",
      "title": "AI Agent工程师",
      "salary": "30-60K·15薪",
      "location": "上海·闵行区·虹桥",
      "tags": "5-10年 | 本科",
      "boss_name": "SHEIN",
      "boss_title": "招聘者",
      "company_scale": "10000人以上",
      "company_stage": "D轮及以上",
      "company_industry": "电子商务",
      "skills": "Java | Spring | AI",
      "job_link": "https://www.zhipin.com/job_detail/xxx.html",
      "company_link": "https://www.zhipin.com/gongsi/xxx.html",
      "welfare": "节日福利 | 零食下午茶 | 定期体检"
    }
  ]
}
```

### CSV

`--format csv` 时自动在同目录生成 `.csv` 文件，13 列，UTF-8 BOM 编码，Excel 直接打开无乱码。

## 工作原理

1. 通过 Chrome DevTools Protocol (CDP) 连接到已打开的 Chrome 浏览器
2. 在 BOSS直聘页面内注入 JS，用同步 XHR 调用 `/wapi/zpgeek/search/joblist.json` API
3. API 返回明文 `salaryDesc`（如 `30-60K·15薪`），绕过前端字体反爬
4. 每页 30 条，每页抓完立即写入文件，异常退出不丢数据
5. 按 `job_id`（job_link 的 MD5 哈希前 16 位）去重

## 数据安全警告

`--setup-chrome` 创建的软链接指向用户的完整 Chrome profile（含 cookie、密码、历史记录）。CDP 连接可以访问所有这些数据。使用完毕后建议清理：

```bash
rm /tmp/chrome-cdp-profile
```

## 常见问题

1. **--check CDP 不通** → 运行 `--setup-chrome`
2. **--check 未登录** → 在 Chrome 中访问 zhipin.com 登录
3. **薪资空白** → 不应该出现，API 返回明文，如仍有问题请提 issue
4. **抓取中断** → 重新运行即可，增量写入 + 自动去重
5. **端口占用** → `--cdp-port 9223` 换端口
6. **Chrome 启动失败** → 先完全关闭 Chrome，再 `--setup-chrome`

## 注意事项

- 仅用于个人求职研究
- 单次最多 10 页（300 条），防封号
- 翻页间隔 12-22 秒随机延迟，3 页约 1 分钟
- 详情页每条 10-25 秒，10 条约 3-5 分钟
- BOSS直聘可能更新 API 路径，失效时需更新脚本中 `API_JOB_LIST_PATH` 常量
