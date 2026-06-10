# BOSS直聘职位抓取工具 v2.0

通过 Chrome CDP 协议抓取 BOSS直聘职位数据的命令行工具 + Hermes Agent Skill。

## 特性

- 明文薪资（API 模式，绕过字体反爬）
- JSON / CSV 双格式输出
- 详情页 JD 抓取 + 技能分析
- 增量写入（异常退出不丢数据）
- 一键环境检查 + Chrome CDP 启动
- 多维筛选（规模、融资、薪资、经验、学历、行业）
- macOS + Linux 支持

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 Chrome CDP
python3 scripts/boss_cdp_raw.py --setup-chrome
# 在浏览器中登录 zhipin.com

# 3. 检查环境
python3 scripts/boss_cdp_raw.py --check

# 4. 抓取
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis
```

## 作为 Hermes Skill 安装

```bash
# 从本地
hermes skills install /path/to/boss-zhipin-scraper

# 从 GitHub
hermes skills install https://github.com/<user>/boss-zhipin-scraper/SKILL.md
```

安装后直接在 Hermes 对话中说"帮我搜一下 BOSS直聘 上上海的 AI Agent 岗位"。

## 参数

| 参数 | 说明 |
|------|------|
| `--keyword` | 搜索关键词（默认 "AI Agent"） |
| `--city` | 城市（中文或代码） |
| `--pages` | 页数（上限 10） |
| `--format` | json / csv |
| `--detail` | 抓取详情页 |
| `--analysis` | 分析报告 |
| `--merge FILE` | 合并已有数据 |
| `--check` | 环境检查 |
| `--setup-chrome` | 启动 Chrome CDP |
| `--cdp-port` | CDP 端口（默认 9222） |
| `--scale/--salary/--experience/--degree` | 筛选条件 |

## 文件结构

```
boss-zhipin-scraper/
├── SKILL.md              # Hermes Skill 定义
├── README.md
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── scripts/
│   └── boss_cdp_raw.py   # 主力脚本
└── requirements.txt
```

## License

MIT
