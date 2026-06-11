#!/usr/bin/env python3
"""
BOSS直聘职位抓取 + 分析 — 纯 CDP raw protocol

功能:
  1. 搜索特定职位 (关键词 + 城市)
  2. 筛选公司规模、融资阶段、薪资范围、经验、学历、行业
  3. 抓取详情页 JD 并分析薪资范围和技能要求
  4. 输出结构化 JSON + CSV + 终端分析报告
  5. 环境检查、Chrome CDP 自动启动、登录状态检测

用法:
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --city 101020100 --pages 5
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --scale 305 --salary 406
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --analysis
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --detail
  uv run python3 scripts/boss_cdp_raw.py --check
  uv run python3 scripts/boss_cdp_raw.py --setup-chrome
  uv run python3 scripts/boss_cdp_raw.py --version
"""

__version__ = "2.0.0"

import json
import time
import random
import sys
import argparse
import os
import re
import hashlib
import csv
import platform
import subprocess
import signal
import logging
from datetime import datetime
from collections import Counter
from urllib.parse import quote, urlencode

try:
    import websocket
    import requests
except ImportError:
    print("需要: uv add websocket-client requests")
    sys.exit(1)

# ============================================================
# 全局常量
# ============================================================

# CDP 默认端口（可通过 --cdp-port 覆盖）
DEFAULT_CDP_PORT = 9222

# API 基础路径（便于统一修改）
API_JOB_LIST_PATH = "/wapi/zpgeek/search/joblist.json"

# 请求频率保护
MAX_PAGES = 10          # 单次最大页数
MAX_API_REQUESTS = 500  # 单次最大 API 请求数

# 平台检测
if platform.system() == "Darwin":
    DEFAULT_CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    DEFAULT_PROFILE_DIR = os.path.expanduser("~/Library/Application Support/Google/Chrome")
else:
    DEFAULT_CHROME_PATH = "/usr/bin/google-chrome"
    DEFAULT_PROFILE_DIR = os.path.expanduser("~/.config/google-chrome")

# 全局请求计数器
_request_counter = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("boss_cdp")


# ============================================================
# 筛选参数映射
# ============================================================
CITY_MAP = {
    "北京": "101010100", "上海": "101020100", "广州": "101280100",
    "深圳": "101280600", "杭州": "101210100", "成都": "101250100",
    "西安": "101110100", "重庆": "101040100", "南京": "101200100",
    "长沙": "101190100", "福州": "101300100", "武汉": "101170100",
    "合肥": "101230100", "济南": "101240100", "大连": "101150100",
    "青岛": "101160100", "宁波": "101180100", "厦门": "101190200",
    "天津": "101060100", "苏州": "101190400", "郑州": "101140100",
    "东莞": "101281600", "佛山": "101280800", "沈阳": "101060100",
}

SCALE_MAP = {
    "0-20人": "301", "20-99人": "302", "100-499人": "303",
    "500-999人": "304", "1000-9999人": "305", "10000人以上": "306",
}

STAGE_MAP = {
    "未融资": "801", "天使轮": "802", "A轮": "803", "B轮": "804",
    "C轮": "805", "D轮及以上": "806", "已上市": "807", "不需要融资": "808",
}

SALARY_MAP = {
    "不限": "0", "2K以下": "401", "2-5K": "402", "5-10K": "403",
    "10-15K": "404", "15-20K": "405", "20-50K": "406", "50K以上": "407",
}

EXPERIENCE_MAP = {
    "在校生": "101", "应届": "102", "1年以内": "103", "1-3年": "104",
    "3-5年": "105", "5-10年": "106", "10年以上": "107",
}

DEGREE_MAP = {
    "初中及以下": "201", "中专/中技": "202", "高中": "203",
    "大专": "204", "本科": "205", "硕士": "206", "博士": "207",
}

INDUSTRY_MAP = {
    "互联网": "1001", "电子商务": "1002", "金融": "1003", "游戏": "1004",
    "企业服务": "1005", "教育培训": "1006", "社交网络": "1007",
    "医疗健康": "1008", "生活服务": "1009", "广告营销": "1010",
}

# 反向映射（code -> 中文名）
CITY_R = {v: k for k, v in CITY_MAP.items()}


# ============================================================
# 全局请求计数器辅助
# ============================================================
def incr_request():
    """递增全局请求计数，达到上限时抛出异常"""
    global _request_counter
    _request_counter += 1
    if _request_counter > MAX_API_REQUESTS:
        raise RuntimeError(f"已达到单次最大请求数 {MAX_API_REQUESTS}，停止抓取")
    if _request_counter >= MAX_API_REQUESTS * 0.8:
        log.warning(f"⚠️ 请求次数接近上限: {_request_counter}/{MAX_API_REQUESTS}")


# ============================================================
# CDP 连接
# ============================================================
class CDPSession:
    def __init__(self, cdp_port=DEFAULT_CDP_PORT):
        self.cdp_port = cdp_port
        resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=10)
        ws_url = resp.json()["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.mid = 0

    def send(self, method, params=None, sid=None, timeout=30):
        """发送 CDP 命令并等待匹配的响应。

        Args:
            method: CDP 方法名
            params: 参数字典
            sid: Target session ID
            timeout: 等待响应的超时秒数，默认 30s

        Returns:
            CDP 响应字典

        Raises:
            TimeoutError: 超过 max_retries 仍未收到匹配响应
        """
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))

        start_time = time.time()
        max_retries = 1000

        for attempt in range(max_retries):
            # 检查超时
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"CDP send({method}) 超时 ({timeout}s), "
                    f"已跳过 {attempt} 条不匹配消息"
                )

            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                raise TimeoutError(f"CDP WebSocket recv 超时, method={method}")

            try:
                r = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                log.debug(f"跳过非 JSON 消息: {raw[:100]}")
                continue

            if r.get("id") == self.mid:
                return r

            # 不匹配的消息：可能是事件通知，记录并跳过
            event_name = r.get("method", "unknown")
            log.debug(f"跳过不匹配消息 (id={r.get('id')}, event={event_name})")

        raise TimeoutError(
            f"CDP send({method}) 在 {max_retries} 条消息内未找到匹配响应"
        )

    def eval_js(self, js, sid):
        r = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True}, sid)
        return r.get("result", {}).get("result", {}).get("value", None)

    def close(self):
        self.ws.close()


# ============================================================
# 通过页面内 XHR 调 API 获取列表数据（明文薪资）
# ============================================================
FETCH_API_JS_TEMPLATE = """
(function(){
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '__API_URL__', false);
    xhr.send();
    if (xhr.status !== 200) return JSON.stringify([{error: xhr.status}]);
    var data = JSON.parse(xhr.responseText);
    var jobs = (data.zpData || {}).jobList || [];
    var results = jobs.map(function(j) {
        return {
            title: j.jobName || '',
            salary: j.salaryDesc || '',
            location: (j.cityName || '') + '\\u00b7' + (j.areaDistrict || '') + '\\u00b7' + (j.businessDistrict || ''),
            tags: [j.jobExperience || '', j.jobDegree || ''].filter(function(t){return t && t !== '\\u4e0d\\u9650';}).join(' | '),
            boss_name: j.brandName || '',
            boss_title: j.bossTitle || '',
            company_scale: j.brandScaleName || '',
            company_stage: j.brandStageName || '',
            company_industry: j.brandIndustry || '',
            job_labels: (j.jobLabels || []).join(' | '),
            skills: (j.skills || []).join(' | '),
            job_link: j.encryptJobId ? 'https://www.zhipin.com/job_detail/' + j.encryptJobId + '.html' : '',
            company_link: j.encryptBrandId ? 'https://www.zhipin.com/gongsi/' + j.encryptBrandId + '.html' : '',
            welfare: (j.welfareList || []).join(' | ')
        };
    });
    return JSON.stringify(results);
})()
"""

# ============================================================
# DEPRECATED: DOM 提取作为 fallback（薪资可能是加密字体）
# 此方法已弃用，仅作为 API 方式失败时的最后降级手段。
# 新代码应优先使用 FETCH_API_JS_TEMPLATE 通过 API 获取数据。
# ============================================================
EXTRACT_LIST_JS = """
(function(){
    var results = [];
    var cards = document.querySelectorAll('li.job-card-box');
    for (var i = 0; i < cards.length; i++) {
        var card = cards[i];
        var nameEl = card.querySelector('.job-name');
        var salaryEl = card.querySelector('.job-salary');
        var locEl = card.querySelector('.company-location');
        var tagEls = card.querySelectorAll('.tag-list li');
        var bossEl = card.querySelector('.boss-name');
        var bossLink = card.querySelector('.boss-info');
        var tags = [];
        for (var j = 0; j < tagEls.length; j++) tags.push(tagEls[j].innerText.trim());
        var jobLink = nameEl ? (nameEl.getAttribute('href') || '') : '';
        if (jobLink && jobLink.charAt(0) === '/') jobLink = 'https://www.zhipin.com' + jobLink;
        var cLink = bossLink ? (bossLink.getAttribute('href') || '') : '';
        if (cLink && cLink.charAt(0) === '/') cLink = 'https://www.zhipin.com' + cLink;
        var t = nameEl ? nameEl.innerText.trim() : '';
        if (t) results.push({
            title: t,
            salary: salaryEl ? salaryEl.innerText.trim() : '',
            location: locEl ? locEl.innerText.trim() : '',
            tags: tags.join(' | '),
            boss_name: bossEl ? bossEl.innerText.trim() : '',
            job_link: jobLink,
            company_link: cLink
        });
    }
    return JSON.stringify(results);
})()
"""

# ============================================================
# 详情页提取 JS（过滤福利标签）
# ============================================================
EXTRACT_DETAIL_JS = """
(function(){
    var body = document.body.innerText;
    var tags = [];
    var benefitWords = ['五险','补充医疗','定期体检','带薪年假','年终奖','零食','餐补',
        '节日福利','加班补助','股票期权','员工旅游','交通补助','通讯补贴','团建',
        '生日福利','免费班车','全勤奖','包吃','弹性工作','下午茶','租房补贴',
        '体检','健身','文化','充电假','司龄假','红包','能量补贴','社团','三薪',
        '绩效','底薪','保底','活动基金','学习基金','节日礼品','无障碍'];
    var noiseWords = ['BOSS直聘','boss','BOSS','来自BOSS直聘','金','金币'];
    function isBenefit(t) {
        if (t === '...' || t.length > 15 || t.length < 2) return true;
        for (var i = 0; i < benefitWords.length; i++) {
            if (t.includes(benefitWords[i])) return true;
        }
        for (var i = 0; i < noiseWords.length; i++) {
            if (t === noiseWords[i] || t.includes(noiseWords[i])) return true;
        }
        return false;
    }
    document.querySelectorAll('.job-tags .tag-all span, .job-keyword-list span').forEach(function(s){
        var t = s.innerText.trim();
        if(t && !isBenefit(t)) tags.push(t);
    });
    var sections = document.querySelectorAll('.job-sec, .job-detail-section');
    var jd = '';
    sections.forEach(function(s){ jd += s.innerText + '\\n'; });
    if(!jd) jd = body.substring(0, 3000);
    return JSON.stringify({jd: jd, tags: tags, url: location.href});
})()
"""


# ============================================================
# 解析城市参数（支持中文和代码）
# ============================================================
def resolve_city(city_input):
    if city_input in CITY_MAP:
        return city_input, CITY_MAP[city_input]
    if city_input in CITY_R:
        return CITY_R[city_input], city_input
    return city_input, city_input


# ============================================================
# 登录状态检测
# ============================================================
def check_login_state(cdp_port=DEFAULT_CDP_PORT):
    """通过 CDP 检测 BOSS直聘登录状态。

    Returns:
        True 已登录, False 未登录
    """
    try:
        cdp = CDPSession(cdp_port)
        r = cdp.send("Target.createTarget", {"url": "about:blank"})
        tid = r["result"]["targetId"]
        r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        # 先导航到 BOSS直聘，确保 cookie 域名正确
        cdp.send("Page.navigate", {"url": "https://www.zhipin.com/"}, sid)
        time.sleep(4)

        # 用搜索 API 探测登录态（已验证可用的接口）
        js = """
        (function(){
            var xhr = new XMLHttpRequest();
            xhr.open('GET', '/wapi/zpgeek/search/joblist.json?scene=1&query=test&city=101020100&page=1&pageSize=1', false);
            xhr.send();
            return xhr.responseText;
        })()
        """
        val = cdp.eval_js(js, sid)

        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

        if not val:
            return False

        try:
            data = json.loads(val) if isinstance(val, str) else val
        except (json.JSONDecodeError, ValueError):
            return False

        # 搜索接口 code=0 且有 jobList 说明已登录
        if data.get("code") == 0:
            zp_data = data.get("zpData", {})
            if isinstance(zp_data, dict):
                job_list = zp_data.get("jobList")
                if job_list is not None:
                    return True
            # code=0 但没有 jobList，可能是登录了但搜索无结果，也算登录
            return True

        # code=7 通常表示未登录
        log.debug(f"登录检测 API code={data.get('code')}, message={data.get('message', '')}")
        return False
    except (requests.ConnectionError, requests.Timeout, KeyError,
            json.JSONDecodeError, websocket.WebSocketException) as e:
        log.error(f"登录状态检测失败: {e}")
        return False


# ============================================================
# CSV 导出
# ============================================================
CSV_COLUMNS = [
    "job_id", "title", "salary", "location", "tags", "boss_name",
    "company_scale", "company_stage", "company_industry", "skills",
    "job_link", "welfare",
]


def write_csv(csv_path, jobs):
    """将 jobs 列表写入 CSV 文件"""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for j in jobs:
            # 确保每列都有值
            row = {col: j.get(col, "") for col in CSV_COLUMNS}
            writer.writerow(row)
    print(f"CSV 已保存: {csv_path}")


# ============================================================
# 增量写入 JSON
# ============================================================
def append_json(path, new_jobs):
    """追加 jobs 到 JSON 文件，每条按 job_id 去重"""
    existing = []
    seen_ids = set()
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            existing = data.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing}
        except (json.JSONDecodeError, OSError, ValueError):
            data = {}
    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            existing.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1
    data["jobs"] = existing
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return added


def flush_jobs(path, meta, jobs):
    """每次有新数据就全量刷写（jobs 去重后），保证异常退出也能保留"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 合并已有文件
    existing_jobs = []
    seen_ids = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old = json.load(f)
            existing_jobs = old.get("jobs", [])
            seen_ids = {j.get("job_id", "") for j in existing_jobs}
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    for j in jobs:
        if j.get("job_id") not in seen_ids:
            existing_jobs.append(j)
            seen_ids.add(j.get("job_id", ""))
    meta["total"] = len(existing_jobs)
    meta["jobs"] = existing_jobs
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


# ============================================================
# 合并外部 JSON 文件
# ============================================================
def merge_jobs(external_path, new_jobs):
    """从外部 JSON 加载 jobs，与 new_jobs 按 job_id 合并去重。

    Args:
        external_path: 已有 JSON 文件路径
        new_jobs: 新抓取的 jobs 列表

    Returns:
        合并后的 jobs 列表
    """
    try:
        with open(external_path, "r", encoding="utf-8") as f:
            old_data = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as e:
        log.warning(f"无法加载合并文件 {external_path}: {e}")
        return new_jobs

    old_jobs = old_data.get("jobs", [])
    merged = list(old_jobs)
    seen_ids = {j.get("job_id", "") for j in merged}

    added = 0
    for j in new_jobs:
        if j.get("job_id") not in seen_ids:
            merged.append(j)
            seen_ids.add(j.get("job_id", ""))
            added += 1

    print(f"合并: 旧文件 {len(old_jobs)} 条 + 新抓取 {len(new_jobs)} 条 = {len(merged)} 条 (新增 {added})")
    return merged


# ============================================================
# 构建搜索 URL
# ============================================================
def build_search_url(keyword, city_code, page, filters):
    params = {"query": keyword, "city": city_code, "page": page}
    for key, code in filters.items():
        if code:
            params[key] = code
    return f"https://www.zhipin.com/web/geek/job?{urlencode(params)}"


# ============================================================
# 抓取列表
# ============================================================
def scrape_list(keyword, city_input, max_pages, filters, output_path,
                cdp_port=DEFAULT_CDP_PORT, fmt="json"):
    city_name, city_code = resolve_city(city_input)
    cdp = CDPSession(cdp_port)
    all_jobs = []
    seen = set()

    # 显示筛选条件
    filter_desc = []
    if filters.get("scale"):
        for k, v in SCALE_MAP.items():
            if v == filters["scale"]:
                filter_desc.append(f"规模={k}")
    if filters.get("stage"):
        for k, v in STAGE_MAP.items():
            if v == filters["stage"]:
                filter_desc.append(f"融资={k}")
    if filters.get("salary"):
        for k, v in SALARY_MAP.items():
            if v == filters["salary"]:
                filter_desc.append(f"薪资={k}")
    if filters.get("experience"):
        for k, v in EXPERIENCE_MAP.items():
            if v == filters["experience"]:
                filter_desc.append(f"经验={k}")
    if filters.get("degree"):
        for k, v in DEGREE_MAP.items():
            if v == filters["degree"]:
                filter_desc.append(f"学历={k}")
    if filters.get("industry"):
        for k, v in INDUSTRY_MAP.items():
            if v == filters["industry"]:
                filter_desc.append(f"行业={k}")

    print(f"=== BOSS直聘抓取 ===")
    print(f"关键词: {keyword} | 城市: {city_name} | 页数: {max_pages}")
    if filter_desc:
        print(f"筛选: {' | '.join(filter_desc)}")
    print()

    r = cdp.send("Target.createTarget", {"url": "about:blank"})
    tid = r["result"]["targetId"]
    r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    def human_scroll(cdp, sid):
        """模拟人类滚动: 随机次数、随机距离、随机停顿，偶尔回滚一点"""
        total_scrolls = random.randint(3, 6)
        for i in range(total_scrolls):
            # 大部分往下滚，偶尔往上回滚一点（模拟阅读回看）
            if random.random() < 0.15:
                delta = -random.randint(50, 150)
            else:
                delta = random.randint(150, 500)
            cdp.eval_js(f"window.scrollBy(0,{delta})", sid)
            # 滚动间隔随机：有时快速连续滚，有时停下来"看"
            if random.random() < 0.3:
                time.sleep(random.uniform(2.0, 4.0))
            else:
                time.sleep(random.uniform(0.5, 1.5))

    def human_mouse_jitter(cdp, sid):
        """偶尔移动鼠标位置，模拟人在页面上活动"""
        if random.random() < 0.4:
            x = random.randint(100, 800)
            y = random.randint(100, 600)
            cdp.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved", "x": x, "y": y
            }, sid)

    try:
        for pg in range(1, max_pages + 1):
            print(f"--- [{pg}/{max_pages} 页, {len(all_jobs)} 条已抓] ---")
            incr_request()

            # 第一页：导航到搜索页建立 cookie/session
            if pg == 1:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send("Page.navigate", {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                human_scroll(cdp, sid)
                human_mouse_jitter(cdp, sid)

            # 优先用 API 获取明文数据
            api_url = f"{API_JOB_LIST_PATH}?scene=1&query={quote(keyword)}&city={city_code}&page={pg}&pageSize=30"
            for k, v in filters.items():
                if v:
                    api_url += f"&{k}={v}"
            api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
            val = cdp.eval_js(api_js, sid)

            jobs = None
            if val:
                try:
                    parsed = json.loads(val) if isinstance(val, str) else val
                    if isinstance(parsed, list):
                        jobs = parsed
                    elif isinstance(parsed, dict) and parsed.get("error"):
                        print(f"  ⚠️ API 返回错误: {parsed['error']}")
                except (json.JSONDecodeError, ValueError):
                    pass

            # API 失败时 fallback 到 DOM 提取（已弃用）
            if not jobs:
                log.warning("⚠️ API 获取失败，回退到 DOM 提取（此方式已弃用，数据可能不完整）")
                if pg > 1:
                    url = build_search_url(keyword, city_code, pg, filters)
                    cdp.send("Page.navigate", {"url": url}, sid)
                    time.sleep(random.uniform(4, 8))
                    human_scroll(cdp, sid)
                val = cdp.eval_js(EXTRACT_LIST_JS, sid)
                if val:
                    try:
                        jobs = json.loads(val) if isinstance(val, str) else val
                    except (json.JSONDecodeError, ValueError):
                        print(f"  ⚠️ JSON 解析失败")
                        jobs = []

            if not jobs:
                print("  ⚠️ 无数据")
                continue

            new = 0
            for j in jobs:
                key = j.get('job_link') or j['title']
                j['job_id'] = hashlib.md5(key.encode()).hexdigest()[:16]
                if key in seen:
                    continue
                seen.add(key)
                all_jobs.append(j)
                new += 1
                salary = j.get('salary','?')
                scale = j.get('company_scale', '')
                extra = f" | {scale}" if scale else ""
                print(f"  ✓ {j['title']} | {salary} | {j.get('location','')} | {j.get('boss_name','')}{extra}")

            print(f"  本页 {len(jobs)} 条, 新增 {new}, 累计 {len(all_jobs)}")

            # 每页抓完就写入文件，异常退出也能保留
            if output_path:
                flush_jobs(output_path, {
                    "keyword": keyword,
                    "city": city_name,
                    "filters": filters,
                    "filter_desc": filter_desc,
                    "scraped_at": datetime.now().isoformat(),
                }, all_jobs)

            if pg < max_pages:
                d = random.uniform(12, 22)
                print(f"  翻页等待 {d:.0f}s...\n")
                time.sleep(d)

    except KeyboardInterrupt:
        print("\n中断")
    except RuntimeError as e:
        print(f"\n⚠️ {e}")
    finally:
        cdp.send("Target.closeTarget", {"targetId": tid})
        cdp.close()

    print(f"\n{'='*60}")
    print(f"完成: {len(all_jobs)} 条")

    if all_jobs:
        if not output_path:
            output_path = f"/tmp/boss/boss_jobs_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        # 最终写入（含时间戳更新）
        flush_jobs(output_path, {
            "keyword": keyword,
            "city": city_name,
            "filters": filters,
            "filter_desc": filter_desc,
            "scraped_at": datetime.now().isoformat(),
        }, all_jobs)
        print(f"已保存: {output_path}")

        # CSV 导出
        if fmt == "csv":
            csv_path = output_path.rsplit(".", 1)[0] + ".csv"
            write_csv(csv_path, all_jobs)
    else:
        print("无数据")

    return {"keyword": keyword, "city": city_name, "total": len(all_jobs), "jobs": all_jobs}


# ============================================================
# 抓取详情
# ============================================================
def scrape_details(list_data, max_details=None, output_path=None,
                   cdp_port=DEFAULT_CDP_PORT):
    jobs = list_data.get("jobs", [])
    if max_details:
        jobs = jobs[:max_details]

    print(f"\n=== 抓取岗位详情 ({len(jobs)} 个) ===\n")
    results = []
    seen_links = set()

    for idx, job in enumerate(jobs):
        link = job.get("job_link", "")
        title = job.get("title", "")
        company = job.get("boss_name", "")
        if not link:
            continue

        # 按 link 去重
        if link in seen_links:
            print(f"[{idx+1}/{len(jobs)}] 跳过重复: {company} - {title}")
            continue
        seen_links.add(link)

        print(f"[{idx+1}/{len(jobs)}] {company} - {title}")

        incr_request()

        # 每个详情页用新 session 避免检测
        ws = CDPSession(cdp_port)
        r = ws.send("Target.createTarget", {"url": "about:blank"})
        tid = r["result"]["targetId"]
        r = ws.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
        sid = r["result"]["sessionId"]

        ws.send("Page.navigate", {"url": link}, sid)
        time.sleep(random.uniform(5, 10))

        # 模拟人类阅读详情页的滚动行为
        scroll_count = random.randint(3, 7)
        for i in range(scroll_count):
            if random.random() < 0.12:
                # 偶尔往上回滚（回看内容）
                delta = -random.randint(80, 200)
            else:
                delta = random.randint(200, 600)
            ws.eval_js(f"window.scrollBy(0,{delta})", sid)
            # 有时快滚，有时停下来"阅读"
            if random.random() < 0.35:
                time.sleep(random.uniform(2.0, 5.0))
            else:
                time.sleep(random.uniform(0.8, 1.8))

        # 偶尔模拟鼠标移动
        if random.random() < 0.5:
            ws.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": random.randint(200, 800),
                "y": random.randint(200, 600)
            }, sid)
            time.sleep(random.uniform(0.5, 1.5))

        val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
        try:
            d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
        except (json.JSONDecodeError, ValueError, TypeError):
            d = {"jd": "", "tags": []}

        detail = {
            "title": title,
            "company": company,
            "salary": job.get("salary", ""),
            "location": job.get("location", ""),
            "tags_list": job.get("tags", ""),
            "link": link,
            "skill_tags": d.get("tags", []),
            "jd": d.get("jd", ""),
        }
        results.append(detail)

        if d.get("tags"):
            print(f"  技能: {', '.join(d['tags'])}")
        print(f"  JD: {len(d.get('jd',''))} 字")

        # 每抓完一个详情就写入，异常退出也能保留
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)

        ws.send("Target.closeTarget", {"targetId": tid})
        ws.close()
        # 详情页间隔加大，随机 10-25 秒
        gap = random.uniform(10, 25)
        print(f"  等待 {gap:.0f}s 后抓下一个...\n")
        time.sleep(gap)

    # 最终保存
    if not output_path:
        output_path = f"/tmp/boss/boss_details_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详情已保存: {output_path}")
    return results


# ============================================================
# 动态技术术语提取
# ============================================================
def extract_tech_terms_from_jds(details, search_keyword=""):
    """从 JD 文本中动态提取高频技术术语。

    策略：
    1. 保留一个小的基础术语列表用于匹配
    2. 对 JD 正文做分词频率分析，提取高频词
    3. 将搜索关键词拆分后加入

    Args:
        details: 详情列表，每个含 "jd" 字段
        search_keyword: 搜索关键词

    Returns:
        去重后的术语列表
    """
    # 基础技术术语（小列表，用于精确匹配）
    base_tech_terms = [
        "Java", "Spring", "Redis", "MySQL", "Kafka", "Flink", "Spark",
        "Go", "Python", "微服务", "分布式", "高并发",
        "AI", "LLM", "RAG", "Agent", "SQL", "Linux",
    ]

    # 从搜索关键词中提取词
    keyword_terms = []
    for word in re.split(r'[\s,，、]+', search_keyword):
        word = word.strip()
        if len(word) >= 2:
            keyword_terms.append(word)

    # 从 JD 文本中提取高频词
    word_freq = Counter()
    for d in details:
        jd_text = d.get("jd", "")
        if not jd_text:
            continue
        # 提取英文技术词（连续 2+ 字母的词）
        en_words = re.findall(r'\b[A-Za-z][A-Za-z0-9._-]+\b', jd_text)
        for w in en_words:
            if len(w) >= 2 and len(w) <= 30:
                word_freq[w] += 1
        # 提取中文技术词（简单：连续中文字符 2-6 个）
        cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', jd_text)
        # 过滤常见非技术中文词
        stop_words = {
            "任职", "要求", "岗位", "职责", "描述", "优先", "具有",
            "负责", "相关", "经验", "能力", "以上", "及其", "工作",
            "开发", "团队", "项目", "公司", "业务", "熟悉", "熟练",
            "了解", "掌握", "参与", "完成", "进行", "能够", "学历",
            "专业", "提供", "福利", "加入", "我们", "我们只", "是通过",
            "就是", "已经", "可以", "这个", "那个", "什么", "怎么",
            "欢迎", "期待", "为你", "为你提供",
        }
        for w in cn_words:
            if w not in stop_words:
                word_freq[w] += 1

    # 取频率最高的动态词（至少出现 2 次，取 top 60）
    dynamic_terms = [
        word for word, count in word_freq.most_common(60)
        if count >= 2
    ]

    # 合并去重：基础 + 关键词 + 动态提取
    all_terms = list(dict.fromkeys(
        base_tech_terms + keyword_terms + dynamic_terms
    ))
    return all_terms


# ============================================================
# 分析报告
# ============================================================
def analyze(list_data, details=None, search_keyword=""):
    jobs = list_data.get("jobs", [])
    print(f"\n{'='*60}")
    print(f"  分析报告: {list_data.get('keyword','')} @ {list_data.get('city','')}")
    print(f"  共 {len(jobs)} 条职位")
    print(f"{'='*60}")

    # 1. 薪资分析
    print(f"\n--- 薪资分布 ---")
    salary_ranges = Counter()
    for j in jobs:
        s = j.get("salary", "")
        if "K" in s:
            salary_ranges[s] += 1
        elif "元/天" in s:
            salary_ranges[s] += 1
        else:
            salary_ranges["未标注"] += 1
    for s, c in salary_ranges.most_common(15):
        bar = "█" * c
        print(f"  {s:<20} {c:>3}  {bar}")

    # 2. 经验要求
    print(f"\n--- 经验要求 ---")
    exp_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if "年" in t or "应届" in t or "在校" in t or "经验不限" in t:
                exp_count[t] += 1
    for e, c in exp_count.most_common():
        print(f"  {e:<15} {c}")

    # 3. 学历要求
    print(f"\n--- 学历要求 ---")
    edu_count = Counter()
    for j in jobs:
        tags = j.get("tags", "")
        for t in tags.split(" | "):
            if t in ["大专", "本科", "硕士", "博士", "学历不限"]:
                edu_count[t] += 1
    for e, c in edu_count.most_common():
        print(f"  {e:<10} {c}")

    # 4. 地区分布
    print(f"\n--- 地区分布 ---")
    loc_count = Counter()
    for j in jobs:
        loc = j.get("location", "")
        # Extract district
        parts = loc.split("·")
        if len(parts) >= 2:
            loc_count[parts[1]] += 1
        elif loc:
            loc_count[loc] += 1
    for l, c in loc_count.most_common(10):
        print(f"  {l:<15} {c}")

    # 5. 公司分布
    print(f"\n--- 高频公司 ---")
    company_count = Counter()
    for j in jobs:
        c = j.get("boss_name", "")
        if c:
            company_count[c] += 1
    for c, n in company_count.most_common(10):
        print(f"  {c:<25} {n} 个岗位")

    # 6. 详情页的技能标签（如有）
    body_freq = Counter()
    if details:
        print(f"\n--- 技能要求频次（来自 JD 标签）---")
        skill_freq = Counter()
        for d in details:
            for tag in d.get("skill_tags", []):
                skill_freq[tag] += 1
        for s, c in skill_freq.most_common(25):
            bar = "█" * c
            print(f"  {s:<20} {c:>3}/{len(details)}  {bar}")

        # 7. JD 正文关键词（动态提取）
        print(f"\n--- JD 正文高频技术词 ---")
        tech_terms = extract_tech_terms_from_jds(details, search_keyword)
        for d in details:
            jd_lower = d.get("jd", "").lower()
            for term in tech_terms:
                if term.lower() in jd_lower:
                    body_freq[term] += 1
        for t, c in body_freq.most_common(25):
            pct = c / len(details) * 100
            bar = "█" * c
            print(f"  {t:<20} {c:>3}/{len(details)} ({pct:.0f}%)  {bar}")

    # 8. 简历建议
    print(f"\n--- 简历建议 ---")
    if details and body_freq:
        noise_list = {'BOSS直聘', 'boss', 'BOSS', '来自BOSS直聘', '金', '金币'}
        top_skills = [s for s, _ in Counter(
            tag for d in details for tag in d.get("skill_tags", [])
        ).most_common(10)]
        # 如果有效标签太少或都是噪音，用 JD 正文关键词代替
        valid_skills = [s for s in top_skills if len(s) >= 2 and s not in noise_list]
        if len(valid_skills) < 3:
            top_skills = [t for t, _ in body_freq.most_common(10)]
        top_body = [t for t, _ in body_freq.most_common(8)] if body_freq else []
        print(f"  技能关键词: {', '.join(top_skills)}")
        print(f"  正文高频词: {', '.join(top_body)}")
        # Experience requirement
        if exp_count:
            top_exp = exp_count.most_common(1)[0][0]
            print(f"  经验要求主流: {top_exp}")
        if edu_count:
            top_edu = edu_count.most_common(1)[0][0]
            print(f"  学历要求主流: {top_edu}")
    else:
        print("  提示: 用 --detail 抓取 JD 详情后可获得更精准的简历建议")


# ============================================================
# --check 环境检查
# ============================================================
def run_check(cdp_port=DEFAULT_CDP_PORT):
    """运行环境诊断检查"""
    print("=" * 50)
    print("  BOSS直聘 CDP 环境检查")
    print("=" * 50)
    print()

    all_pass = True

    # 检查 1: CDP 端口连通性
    print("[1/3] CDP 端口连通性...")
    try:
        resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5)
        data = resp.json()
        browser = data.get("Browser", "未知")
        print(f"  ✅ 通过 — Chrome {browser}")
    except (requests.ConnectionError, requests.Timeout):
        print(f"  ❌ 失败 — 无法连接 127.0.0.1:{cdp_port}")
        print(f"     请先启动 Chrome CDP: python3 {__file__} --setup-chrome")
        all_pass = False
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  ❌ 失败 — CDP 响应异常: {e}")
        all_pass = False

    # 检查 2: Python 依赖
    print("[2/3] Python 依赖...")
    deps_ok = True
    for mod_name in ["websocket", "requests"]:
        try:
            __import__(mod_name)
            print(f"  ✅ {mod_name} 可导入")
        except ImportError:
            print(f"  ❌ {mod_name} 未安装 — 运行: uv add {mod_name}-client" if mod_name == "websocket" else f"  ❌ {mod_name} 未安装 — 运行: uv add {mod_name}")
            deps_ok = False
            all_pass = False
    if deps_ok:
        print(f"  ✅ 依赖完整")

    # 检查 3: BOSS直聘登录状态
    print("[3/3] BOSS直聘登录状态...")
    try:
        logged_in = check_login_state(cdp_port)
        if logged_in:
            print(f"  ✅ 已登录")
        else:
            print(f"  ❌ 未登录 — 请先在 Chrome 中登录 zhipin.com")
            all_pass = False
    except Exception as e:
        print(f"  ❌ 检测失败: {e}")
        all_pass = False

    print()
    if all_pass:
        print("✅ 所有检查通过，可以开始抓取")
    else:
        print("❌ 部分检查未通过，请修复后重试")
    print()

    return 0 if all_pass else 1


# ============================================================
# --setup-chrome 自动启动
# ============================================================
def run_setup_chrome(cdp_port=DEFAULT_CDP_PORT):
    """自动配置并启动 Chrome CDP 模式"""
    print("=" * 50)
    print("  设置 Chrome CDP 调试模式")
    print("=" * 50)
    print()

    symlink_path = "/tmp/chrome-cdp-profile"

    # 1. 创建符号链接
    if os.path.exists(symlink_path):
        print(f"✅ 符号链接已存在: {symlink_path}")
    else:
        os.symlink(DEFAULT_PROFILE_DIR, symlink_path)
        print(f"✅ 已创建符号链接: {symlink_path} -> {DEFAULT_PROFILE_DIR}")

    # 2. 关闭已有 Chrome
    print("\n关闭已有 Chrome 进程...")
    if platform.system() == "Darwin":
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to quit'],
            capture_output=True, timeout=10,
        )
    else:
        subprocess.run(["pkill", "-f", "google-chrome"], capture_output=True, timeout=5)
    time.sleep(2)
    print("✅ 已关闭")

    # 3. 启动 Chrome
    print(f"\n启动 Chrome (CDP 端口: {cdp_port})...")
    cmd = [
        DEFAULT_CHROME_PATH,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={symlink_path}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # 启动 Chrome（不阻塞）
    if platform.system() == "Darwin":
        # macOS: 用 open 命令
        subprocess.Popen(["open", "-a", "Google Chrome", "--args"] + cmd[1:],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    # 4. 等待 CDP 可用
    print("等待 CDP 可用", end="")
    for i in range(30):
        time.sleep(1)
        print(".", end="", flush=True)
        try:
            resp = requests.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
            if resp.status_code == 200:
                print(f"\n✅ CDP 已就绪 (端口 {cdp_port})")
                break
        except (requests.ConnectionError, requests.Timeout):
            continue
    else:
        print(f"\n❌ 等待超时 (30s)，CDP 未就绪")
        print(f"   请手动检查 Chrome 是否启动，端口 {cdp_port} 是否开放")
        return 1

    print()
    print("Chrome 已启动。请在浏览器中登录 zhipin.com，然后运行抓取命令。")
    print()
    print(f"示例:")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --keyword \"AI Agent\" --city 上海 --pages 3")
    print(f"  uv run python3 scripts/boss_cdp_raw.py --check")
    print()
    return 0


# ============================================================
# main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description=f"BOSS直聘抓取 + 分析 (CDP Raw) v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
筛选参数示例:
  --scale 305          公司规模 (301=0-20人 302=20-99 303=100-499 304=500-999 305=1000-9999 306=10000+)
  --stage 807          融资阶段 (801=未融资 ... 807=已上市 808=不需要融资)
  --salary 406         薪资范围 (401=2K以下 402=2-5K 403=5-10K 404=10-15K 405=15-20K 406=20-50K 407=50K+)
  --experience 105     经验要求 (101=在校 102=应届 103=1年以内 104=1-3年 105=3-5年 106=5-10年 107=10年+)
  --degree 205         学历要求 (204=大专 205=本科 206=硕士 207=博士)
  --industry 1001      行业 (1001=互联网 1002=电商 1003=金融 ...)

城市支持中文: --city 上海  或代码: --city 101020100

示例:
  # 基础搜索
  %(prog)s --keyword "Java 风控" --city 上海 --pages 5

  # 筛选大公司 + 高薪
  %(prog)s --keyword "Java 风控" --scale 305 --salary 406

  # 抓列表 + 详情 + 分析报告
  %(prog)s --keyword "Java 风控" --pages 3 --detail --analysis

  # 只分析已有数据
  %(prog)s --input data/boss/boss_jobs_20260609.json --analysis

  # 导出 CSV
  %(prog)s --keyword "Java 风控" --pages 3 --format csv

  # 合并旧数据
  %(prog)s --keyword "Java 风控" --pages 3 --merge old_data.json

  # 环境检查
  %(prog)s --check

  # 启动 Chrome CDP
  %(prog)s --setup-chrome
        """)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--keyword", default="AI Agent", help="搜索关键词")
    p.add_argument("--city", default="101020100", help="城市 (中文名或代码)")
    p.add_argument("--pages", type=int, default=3, help=f"抓取页数 (最大 {MAX_PAGES})")
    p.add_argument("--output", default=None, help="列表数据输出路径")
    p.add_argument("--detail-output", default=None, help="详情数据输出路径")
    p.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT,
                   help=f"CDP 调试端口 (默认 {DEFAULT_CDP_PORT})")
    p.add_argument("--format", default="json", choices=["json", "csv"],
                   help="输出格式 (默认 json)")
    p.add_argument("--merge", default=None,
                   help="合并已有 JSON 文件 (按 job_id 去重)")

    # 筛选参数
    p.add_argument("--scale", default=None, help="公司规模代码")
    p.add_argument("--stage", default=None, help="融资阶段代码")
    p.add_argument("--salary", default=None, help="薪资范围代码")
    p.add_argument("--experience", default=None, help="经验要求代码")
    p.add_argument("--degree", default=None, help="学历要求代码")
    p.add_argument("--industry", default=None, help="行业代码")

    # 功能开关
    p.add_argument("--detail", action="store_true", help="抓取详情页 JD")
    p.add_argument("--max-details", type=int, default=None, help="最多抓几个详情")
    p.add_argument("--analysis", action="store_true", help="输出分析报告")
    p.add_argument("--input", default=None, help="从已有 JSON 文件读取（跳过抓取）")

    # 工具命令
    p.add_argument("--check", action="store_true", help="运行环境诊断检查")
    p.add_argument("--setup-chrome", action="store_true",
                   help="自动启动 Chrome CDP 调试模式")

    args = p.parse_args()

    # --check 模式
    if args.check:
        sys.exit(run_check(args.cdp_port))

    # --setup-chrome 模式
    if args.setup_chrome:
        sys.exit(run_setup_chrome(args.cdp_port))

    # 页数限制
    if args.pages > MAX_PAGES:
        print(f"⚠️ 页数 {args.pages} 超过上限 {MAX_PAGES}，已自动调整为 {MAX_PAGES}")
        args.pages = MAX_PAGES

    # 收集筛选条件
    filters = {}
    for key in ["scale", "stage", "salary", "experience", "degree", "industry"]:
        val = getattr(args, key)
        if val:
            filters[key] = val

    # 加载或抓取列表
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            list_data = json.load(f)
        print(f"从文件加载 {len(list_data.get('jobs',[]))} 条: {args.input}")
    else:
        # 登录状态检测
        print("检测登录状态...")
        if not check_login_state(args.cdp_port):
            print("❌ 未检测到 BOSS直聘登录状态。请先在 Chrome 中登录 zhipin.com。")
            print(f"   可运行 --check 检查环境，或 --setup-chrome 启动 Chrome。")
            sys.exit(1)
        print("✅ 已登录\n")

        list_data = scrape_list(
            args.keyword, args.city, args.pages, filters, args.output,
            cdp_port=args.cdp_port, fmt=args.format,
        )

    # 合并外部文件
    if args.merge:
        merged_jobs = merge_jobs(args.merge, list_data.get("jobs", []))
        list_data["jobs"] = merged_jobs
        list_data["total"] = len(merged_jobs)
        # 重新保存合并结果
        if args.output:
            flush_jobs(args.output, {
                "keyword": list_data.get("keyword", ""),
                "city": list_data.get("city", ""),
                "filters": list_data.get("filters", {}),
                "filter_desc": list_data.get("filter_desc", []),
                "scraped_at": datetime.now().isoformat(),
                "merged_from": args.merge,
            }, merged_jobs)
            print(f"合并结果已保存: {args.output}")
            if args.format == "csv":
                csv_path = args.output.rsplit(".", 1)[0] + ".csv"
                write_csv(csv_path, merged_jobs)

    # 抓详情
    details = None
    if args.detail and list_data.get("jobs"):
        details = scrape_details(
            list_data, args.max_details, args.detail_output,
            cdp_port=args.cdp_port,
        )

    # 分析
    if args.analysis:
        # 如果有详情文件也加载
        if not details:
            detail_path = f"data/boss/boss_details_{datetime.now().strftime('%Y%m%d')}.json"
            if os.path.exists(detail_path):
                with open(detail_path, encoding="utf-8") as f:
                    details = json.load(f)
        analyze(list_data, details, search_keyword=args.keyword)


if __name__ == "__main__":
    main()
