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

## 一键使用（推荐）

```bash
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis
```

如果 Chrome CDP 还没启动：

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome
# 等浏览器打开后登录 zhipin.com，然后再运行抓取命令
```

检查环境是否就绪：

```bash
python3 scripts/boss_cdp_raw.py --check
```

## 自动化步骤

当用户要求搜索/抓取 BOSS直聘 职位时，按以下顺序执行：

### 1. 检查环境

```bash
python3 scripts/boss_cdp_raw.py --check
```

检查项：CDP 连通性 → Python 依赖 → BOSS直聘登录态。全部通过再继续。

如果检查失败，运行 `--setup-chrome` 或手动解决。

### 2. 启动 Chrome CDP（如果 --check 失败）

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome
```

这会自动完成：
- 创建软链接（避免 Chrome profile 锁冲突）
- 关闭现有 Chrome
- 以 CDP 模式启动 Chrome
- 等待 CDP 端口就绪

**用户需要手动在浏览器中登录 zhipin.com。**

### 3. 安装依赖

```bash
pip install websocket-client>=1.6.0 requests>=2.28.0
# 或者
uv add websocket-client requests
```

### 4. 运行抓取

```bash
# 基础搜索
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3

# 带详情 + 分析 + CSV 输出
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 \
  --detail --max-details 8 --analysis --format csv \
  --output /tmp/boss/jobs.json

# 合并多次抓取
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 北京 --pages 3 \
  --merge /tmp/boss/jobs.json --output /tmp/boss/jobs_merged.json
```

## 参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | AI Agent | 搜索关键词 |
| `--city` | 上海 | 城市名（中文）或代码 |
| `--pages` | 3 | 抓取页数（上限 10） |
| `--output` | /tmp/boss/... | 列表输出路径 |
| `--detail-output` | /tmp/boss/... | 详情输出路径 |
| `--format` | json | 输出格式: json / csv |
| `--detail` | 关闭 | 同时抓取详情页 JD |
| `--max-details` | 全部 | 详情页数量上限 |
| `--analysis` | 关闭 | 输出分析报告 |
| `--merge` | - | 合并已有 JSON（去重） |
| `--cdp-port` | 9222 | CDP 端口 |
| `--setup-chrome` | 关闭 | 一键启动 Chrome CDP |
| `--check` | 关闭 | 环境检查 |
| `--version` | - | 查看版本号 |

### 筛选参数

| 参数 | 值 |
|------|-----|
| `--scale` | 301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+ |
| `--salary` | 401=2K以下 402=2-5K 403=5-10K 404=10-15K 405=15-20K 406=20-50K 407=50K+ |
| `--experience` | 101=在校 102=应届 103=1年内 104=1-3年 105=3-5年 106=5-10年 107=10年+ |
| `--degree` | 204=大专 205=本科 206=硕士 207=博士 |

## 输出格式

### JSON

```json
{
  "keyword": "AI Agent",
  "city": "上海",
  "total": 90,
  "jobs": [
    {
      "job_id": "c4420e8bce3a6e25",
      "title": "AI Agent工程师",
      "salary": "30-60K·15薪",
      "location": "上海·闵行区·虹桥",
      "tags": "5-10年 | 本科",
      "boss_name": "SHEIN",
      "company_scale": "10000人以上",
      "company_stage": "D轮及以上",
      "company_industry": "电子商务",
      "skills": "Java | Spring | AI",
      "job_link": "https://www.zhipin.com/job_detail/xxx.html",
      "welfare": "节日福利 | 零食下午茶"
    }
  ]
}
```

### CSV

`--format csv` 时自动在同目录生成 `.csv` 文件，12 列，UTF-8 with BOM 编码，Excel 直接打开无乱码。

## 安全警告

`--setup-chrome` 会创建指向用户 Chrome profile 的软链接。这意味着 CDP 连接可以访问 Chrome 中的所有数据（cookie、密码、历史记录）。请勿在不受信任的环境中使用，使用完毕后建议：

```bash
# 清理软链接
rm /tmp/chrome-cdp-profile
```

## 常见问题

1. **`--check` 显示 CDP 不可用** — 运行 `--setup-chrome` 启动 Chrome
2. **`--check` 显示未登录** — 在 Chrome 浏览器中访问 zhipin.com 登录
3. **薪资显示空白/方块** — 已通过 API 模式解决，如仍出现请提 issue
4. **抓取中断** — 数据已增量写入，重新运行自动去重合并
5. **详情页太慢** — 每个详情 10-25 秒是正常反爬间隔，10 个约 3-5 分钟
6. **端口被占用** — 用 `--cdp-port 9223` 换个端口

## 注意事项

- 仅用于个人求职研究
- 单次最多 10 页（300 条），防止触发封号
- BOSS直聘可能更新 API，失效时需更新 `API_JOB_LIST_PATH` 常量
