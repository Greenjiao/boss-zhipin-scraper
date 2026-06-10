---
name: boss-zhipin-scraper
description: "Scrape BOSS直聘 (job listing site) via Chrome CDP. Searches jobs by keyword/city/filters, fetches JD details, and outputs structured JSON with plaintext salary. Use when user wants to search/analyze jobs on BOSS直聘 or zhipin.com."
version: 1.0.0
author: eatmoreduck
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [scraper, jobs, career, cdp, chrome, zhipin, boss直聘]
---

# BOSS直聘职位抓取工具

通过 Chrome CDP 协议抓取 BOSS直聘 (zhipin.com) 职位数据，输出结构化 JSON（含明文薪资）。

## 前置条件

用户必须有一个已登录 BOSS直聘 的 Chrome 浏览器实例，通过 CDP 端口连接。

## 自动化步骤

当用户要求搜索/抓取 BOSS直聘 职位时，按以下顺序执行：

### 1. 检测 CDP 连接

```bash
curl -s http://127.0.0.1:9222/json/version
```

- 如果返回 JSON 且包含 `webSocketDebuggerUrl`：CDP 可用，跳到步骤 3
- 如果连接失败：继续步骤 2

### 2. 启动 Chrome CDP（软链接方案）

直接 `--user-data-dir` 指向原始 Chrome profile 会锁冲突，必须用软链接：

```bash
# macOS
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_DIR="$HOME/Library/Application Support/Google/Chrome"
SYMLINK_DIR="/tmp/chrome-cdp-profile"

# 创建软链接（如果不存在）
if [ ! -L "$SYMLINK_DIR" ]; then
    ln -s "$PROFILE_DIR" "$SYMLINK_DIR"
fi

# 关闭现有 Chrome（必须先关闭，否则锁冲突）
osascript -e 'tell application "Google Chrome" to quit'
sleep 2

# 用软链接路径启动 CDP
"$CHROME" --remote-debugging-port=9222 --user-data-dir="$SYMLINK_DIR" &
sleep 5

# 验证
curl -s http://127.0.0.1:9222/json/version
```

**重要**：
- 启动后用户需要手动在浏览器中登录 BOSS直聘 (zhipin.com)
- 如果用户已经开启了 CDP 模式的 Chrome，不要重复启动
- Linux 上 Chrome 路径通常是 `/usr/bin/google-chrome`，profile 在 `~/.config/google-chrome`

### 3. 安装依赖

```bash
cd <用户项目目录>
uv add websocket-client requests fonttools
```

如果项目没有 uv，也可以：
```bash
pip install websocket-client requests fonttools
```

### 4. 部署脚本

脚本位于本 skill 的 `scripts/boss_cdp_raw.py`。复制到用户项目：

```bash
mkdir -p <项目>/scripts
cp <skill_dir>/scripts/boss_cdp_raw.py <项目>/scripts/boss_cdp_raw.py
```

### 5. 运行抓取

```bash
uv run python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --output /tmp/boss/jobs.json
```

### 6. 带详情 + 分析

```bash
uv run python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 \
  --detail --max-details 8 --analysis \
  --output /tmp/boss/jobs.json --detail-output /tmp/boss/details.json
```

## 参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--keyword` | AI Agent | 搜索关键词 |
| `--city` | 101020100 (上海) | 城市名（上海）或代码 |
| `--pages` | 3 | 抓取页数 |
| `--output` | /tmp/boss/... | 列表输出路径 |
| `--detail-output` | /tmp/boss/... | 详情输出路径 |
| `--detail` | 关闭 | 同时抓取详情页 JD |
| `--max-details` | 全部 | 详情页数量上限 |
| `--analysis` | 关闭 | 输出分析报告 |
| `--scale` | - | 公司规模筛选 |
| `--salary` | - | 薪资范围筛选 |
| `--experience` | - | 经验要求筛选 |
| `--degree` | - | 学历要求筛选 |
| `--input` | - | 分析已有 JSON（不重新抓取） |

### 筛选参数值

```
--scale:    301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+
--salary:   401=2K以下 402=2-5K 403=5-10K 404=10-15K 405=15-20K 406=20-50K 407=50K+
--experience: 101=在校 102=应届 103=1年内 104=1-3年 105=3-5年 106=5-10年 107=10年+
--degree:   204=大专 205=本科 206=硕士 207=博士
```

## 输出格式

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
      "welfare": "节日福利 | 零食下午茶 | ..."
    }
  ]
}
```

## 常见问题

1. **Chrome 启动后无法连接 CDP** — 检查端口是否被占用：`lsof -i :9222`
2. **薪资显示空白/方块** — 旧版 DOM 提取模式遇到字体反爬，API 模式已解决此问题
3. **页面 DOM 被清空** — BOSS直聘检测到自动化工具，不要用 Playwright/Puppeteer
4. **抓取中断** — 数据已增量写入 JSON，重新运行会自动去重合并
5. **详情页太慢** — 每个详情页间隔 10-25 秒是正常的（反爬），10 个约需 3-5 分钟

## 注意事项

- 仅用于个人求职研究，勿用于商业数据采集
- 建议单次不超过 5 页（150 条）
- BOSS直聘可能更新 API，失效时需更新脚本中的接口路径
