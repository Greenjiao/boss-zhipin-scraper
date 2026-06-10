#!/usr/bin/env python3
"""
BOSS直聘职位抓取 + 分析 — 纯 CDP raw protocol

功能:
  1. 搜索特定职位 (关键词 + 城市)
  2. 筛选公司规模、融资阶段、薪资范围、经验、学历、行业
  3. 抓取详情页 JD 并分析薪资范围和技能要求
  4. 输出结构化 JSON + 终端分析报告

用法:
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --city 101020100 --pages 5
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --scale 305 --salary 406
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --analysis
  uv run python3 scripts/boss_cdp_raw.py --keyword "Java 风控" --detail
"""
import json
import time
import random
import sys
import argparse
import os
import re
import hashlib
from datetime import datetime
from collections import Counter
from urllib.parse import quote, urlencode

try:
    import websocket
    import requests
except ImportError:
    print("需要: uv add websocket-client requests")
    sys.exit(1)

CDP_PORT = 9222

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
# CDP 连接
# ============================================================
class CDPSession:
    def __init__(self):
        resp = requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version")
        ws_url = resp.json()["webSocketDebuggerUrl"]
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.mid = 0

    def send(self, method, params=None, sid=None):
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == self.mid:
                return r

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
            location: (j.cityName || '') + '\u00b7' + (j.areaDistrict || '') + '\u00b7' + (j.businessDistrict || ''),
            tags: [j.jobExperience || '', j.jobDegree || ''].filter(function(t){return t && t !== '\u4e0d\u9650';}).join(' | '),
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

# DOM 提取作为 fallback（薪资可能是加密字体）
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
        except:
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
        except:
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
def scrape_list(keyword, city_input, max_pages, filters, output_path):
    city_name, city_code = resolve_city(city_input)
    cdp = CDPSession()
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
            print(f"--- 第 {pg}/{max_pages} 页 ---")
            
            # 第一页：导航到搜索页建立 cookie/session
            if pg == 1:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send("Page.navigate", {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                human_scroll(cdp, sid)
                human_mouse_jitter(cdp, sid)

            # 优先用 API 获取明文数据
            api_url = f"/wapi/zpgeek/search/joblist.json?scene=1&query={quote(keyword)}&city={city_code}&page={pg}&pageSize=30"
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
                except:
                    pass

            # API 失败时 fallback 到 DOM 提取
            if not jobs:
                if pg > 1:
                    url = build_search_url(keyword, city_code, pg, filters)
                    cdp.send("Page.navigate", {"url": url}, sid)
                    time.sleep(random.uniform(4, 8))
                    human_scroll(cdp, sid)
                val = cdp.eval_js(EXTRACT_LIST_JS, sid)
                if val:
                    try:
                        jobs = json.loads(val) if isinstance(val, str) else val
                    except:
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
    else:
        print("无数据")

    return {"keyword": keyword, "city": city_name, "total": len(all_jobs), "jobs": all_jobs}


# ============================================================
# 抓取详情
# ============================================================
def scrape_details(list_data, max_details=None, output_path=None):
    jobs = list_data.get("jobs", [])
    if max_details:
        jobs = jobs[:max_details]

    print(f"\n=== 抓取岗位详情 ({len(jobs)} 个) ===\n")
    results = []

    for idx, job in enumerate(jobs):
        link = job.get("job_link", "")
        title = job.get("title", "")
        company = job.get("boss_name", "")
        if not link:
            continue

        print(f"[{idx+1}/{len(jobs)}] {company} - {title}")

        # 每个详情页用新 session 避免检测
        ws = CDPSession()
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
        except:
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
# 分析报告
# ============================================================
def analyze(list_data, details=None):
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

        # 7. JD 正文关键词
        print(f"\n--- JD 正文高频技术词 ---")
        tech_terms = [
            "Java", "Spring", "Redis", "MySQL", "Kafka", "Flink", "Spark",
            "Elasticsearch", "ES", "MongoDB", "Go", "Python", "微服务", "分布式",
            "高并发", "规则引擎", "Drools", "风控", "反欺诈", "反洗钱", "AML",
            "合规", "KYC", "实时", "离线", "大数据", "消息队列", "缓存",
            "架构设计", "JVM", "多线程", "RPC", "Dubbo", "SpringCloud",
            "大模型", "LLM", "AI", "Agent", "RAG", "知识图谱", "向量",
            "决策引擎", "特征", "模型", "制裁", "图数据库", "向量检索",
            "特征平台", "中间件", "Canal",
        ]
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
# main
# ============================================================
def main():
    p = argparse.ArgumentParser(
        description="BOSS直聘抓取 + 分析 (CDP Raw)",
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
        """)
    p.add_argument("--keyword", default="AI Agent", help="搜索关键词")
    p.add_argument("--city", default="101020100", help="城市 (中文名或代码)")
    p.add_argument("--pages", type=int, default=3, help="抓取页数")
    p.add_argument("--output", default=None, help="列表数据输出路径")
    p.add_argument("--detail-output", default=None, help="详情数据输出路径")

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

    args = p.parse_args()

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
        list_data = scrape_list(args.keyword, args.city, args.pages, filters, args.output)

    # 抓详情
    details = None
    if args.detail and list_data.get("jobs"):
        details = scrape_details(list_data, args.max_details, args.detail_output)

    # 分析
    if args.analysis:
        # 如果有详情文件也加载
        if not details:
            detail_path = f"data/boss/boss_details_{datetime.now().strftime('%Y%m%d')}.json"
            if os.path.exists(detail_path):
                with open(detail_path, encoding="utf-8") as f:
                    details = json.load(f)
        analyze(list_data, details)


if __name__ == "__main__":
    main()
