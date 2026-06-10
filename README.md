# BOSS直聘职位抓取工具 (Hermes Skill)

通过 Chrome CDP 协议抓取 BOSS直聘职位数据的 Hermes Agent Skill。

## 安装

```bash
# 从本地路径安装
hermes skills install /path/to/boss-zhipin-scraper

# 或从 GitHub 安装
hermes skills install https://github.com/<your-username>/boss-zhipin-scraper/SKILL.md
```

## 使用

在 Hermes 会话中直接说：

> "帮我搜一下上海 AI Agent 相关的岗位"
> "抓一下 BOSS直聘 上北京 Java 风控 50K 以上的岗位并分析"

Agent 会自动检测 Chrome CDP、安装依赖、运行抓取。

## 手动使用脚本

```bash
# 前置：开启 Chrome CDP
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp-profile

# 安装依赖
pip install websocket-client requests fonttools

# 运行
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3
```

## 文件结构

```
boss-zhipin-scraper/
├── SKILL.md                 # Hermes Skill 定义
├── README.md                # 本文件
├── scripts/
│   └── boss_cdp_raw.py      # 主力抓取脚本
└── LICENSE
```

## License

MIT
