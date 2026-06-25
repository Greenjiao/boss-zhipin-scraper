#!/usr/bin/env python3
"""
BOSS直聘 AI 筛选器 — 照搬 JobCopilot 的提示词与评分体系

功能:
  1. AI 多维度评分筛选岗位（个人匹配度 + 岗位质量）
  2. AI 生成个性化招呼语（适合 BOSS 直聘 HR）
  3. 支持 DeepSeek / Claude 两种 LLM
  4. 批量筛选 + 招呼语生成（一次 API 调用同时完成评分和招呼语）

用法:
  from scripts.ai_screener import AIScreener
  
  screener = AIScreener(provider="deepseek", api_key="sk-xxx", model="deepseek-chat")
  result = screener.screen_job(job_info, resume_text, keywords="AI Agent")
  # result = {"score": 78, "match": True, "reason": "...", "greeting": "..."}
"""

import json
import re
import os
import logging

log = logging.getLogger("ai_screener")

# ============================================================
# 提示词 — 原样照搬 JobCopilot background.js
# ============================================================

SCREEN_PROMPT_PRE = """你是资深求职助手。请完全依据下面提供的【求职者简历】与【岗位信息】，完成两项任务：
1. 对岗位进行多维度评分，判断是否值得投递
2. 如果值得投递（match=true），同时生成一段BOSS直聘打招呼语

【评分标准·满分100分】分两大类：个人匹配度（60分）+ 岗位本身质量（40分），逐项评估后给出综合总分。

===== 第一类：个人匹配度（满分60分）=====

1. 岗位方向匹配度（0-20分）
   - 完全对口（同一职能方向）：15-20分
   - 弱相关（有交叉技能但方向不同）：8-14分
   - 基本无关：0-7分

2. 经验年限匹配度（0-10分）
   - 求职者经验满足岗位要求：8-10分
   - 求职者经验略低于要求（差距1-2年）：4-7分
   - 求职者经验明显不足（差距3年以上）：0-3分

3. 技能匹配度（0-10分）
   - 核心技能大部分匹配：8-10分
   - 约半数核心技能匹配：4-7分
   - 核心技能基本不匹配：0-3分

4. 薪资匹配度（0-10分）
   - 岗位薪资在期望范围内或高于期望：8-10分
   - 岗位薪资略低于期望（差距20%以内）：4-7分
   - 岗位薪资明显低于期望（差距超过20%）：0-3分

5. 学历/级别匹配度（0-6分）
   - 岗位级别与求职者当前水平匹配或可够得着：5-6分
   - 岗位级别略高于求职者但可挑战：2-4分
   - 岗位级别明显高于求职者（如要求总监而你只有初级经验）：0-1分

6. 城市/地点匹配度（0-4分）
   - 求职者意向城市包含该岗位所在城市或接受远程：3-4分
   - 求职者意向城市不包含该城市且未表明接受异地：0-2分

===== 第二类：岗位本身质量（满分40分）=====

7. JD完整度与专业性（0-15分）
   - JD描述详尽、职责清晰、要求明确、结构规范：11-15分
   - JD描述一般、有部分关键信息缺失或表述笼统：5-10分
   - JD极其简略、只有一句话、明显敷衍、或者照抄模板毫无针对性：0-4分
   - 注意：JD越敷衍的岗位，越有可能是钓鱼/刷KPI/虚假招聘，应大力扣分

8. 公司/岗位可信度（0-15分）
   - 公司名称明确、信息正规可查、岗位描述与薪资匹配合理：11-15分
   - 公司名称模糊（如某知名互联网公司）、岗位描述存在矛盾或夸大：5-10分
   - 出现以下任一红旗信号则大幅扣分至0-4分：
     * 公司名称缺失或明显虚假
     * 薪资范围异常（过高或过宽，如5-50K）
     * JD中出现"无需经验""躺着赚钱""日结""兼职刷单"等可疑措辞
     * 岗位名称与JD内容严重不符（如标题是开发实际是销售）
     * 岗位信息严重不全（连岗位职责和任职要求都缺失）

9. 岗位成长性与发展空间（0-10分）
   - 对你而言该岗位有明显成长空间（技术栈新、业务有前景、职责有挑战）：7-10分
   - 岗位对你的成长性一般（能稳定工作但提升有限）：3-6分
   - 岗位对你几乎没有成长价值（技能倒退、外包岗无上升空间、重复劳动）：0-2分
   - 注意：无需经验的流水线式岗位也要扣分
"""

DEFAULT_GREETING_FORMAT = """【招呼语格式要求】（仅match=true时需要填写greeting字段）
- 你是求职者本人，招呼语会原样发给HR，严禁任何注释、说明、括号备注、字数统计或引导语
- 生成一段简短真诚、突出我匹配点的打招呼语(80-120字，口语化，不要过度套话堆砌，字数不是强制限定范围，可根据实际情况调整)
- 要求结合【个人背景】、【意向关键词】等相关信息，生成的打招呼语要突出我匹配点，不能只描述我自己的背景
- 结合相关信息时需要评估你要写入招呼语的信息是否适合发送给HR，不能包含很可能让HR不喜欢的信息或者和HR对于招聘求职者的录用判断无关的信息，不相干的或者与岗位要求没什么大关联的信息不要强行制造关联
- 如果你无法判断，建议你返回"为什么无法判断"+无法判断的原因，而不是随机生成"""

SCREEN_PROMPT_POST = """
【output字段说明】
- score：整数，0-100，上述9项得分之和
- reason：字符串，100字以内的评估摘要，简要说明匹配点和扣分点（若因岗位质量问题扣分应明确指出）
- match：布尔值，score >= 设定的命中阈值则为true，否则false（请在user消息中确认阈值）
- greeting：字符串，match=true时按招呼语格式要求填写，match=false时填空串""

【输出格式·严格要求】你必须严格按以下格式输出，不得有任何偏离：
1. 只输出一行纯JSON，不要换行，尽量少空格，不要任何额外文字
2. 不要使用markdown代码块（禁止```json或```）
3. 不要加任何解释、备注、前缀、后缀
4. JSON必须包含且仅包含四个字段：score（整数）、reason（字符串）、match（布尔值）、greeting（字符串）
5. 示例正确输出（匹配）：{"score":78,"reason":"Java方向对口，3年经验满足要求，薪资在期望范围","match":true,"greeting":"熟悉Java、Spring Boot，做过微服务架构项目，期待与贵司共同成长，我相信我的分布式系统经验能为团队带来价值"}
6. 示例正确输出（不匹配）：{"score":32,"reason":"岗位要求5年算法经验，你仅有1年Python开发，方向与技能均不匹配","match":false,"greeting":""}
7. 如果无法判断，默认score=0，match=false，reason写明原因，greeting=""

请现在就输出一个JSON对象（仅JSON，无其他任何内容）："""


def build_screen_prompt(custom_prompt: str = "") -> str:
    """组装完整的 system 提示词：自定义招呼语格式段替换默认"""
    greeting_section = custom_prompt.strip() or DEFAULT_GREETING_FORMAT
    return SCREEN_PROMPT_PRE + greeting_section + SCREEN_PROMPT_POST


def build_user_prompt(resume_text: str, keywords: str, salary_range: str) -> str:
    """构建用户画像提示词（背景 + 意向关键词 + 期望薪资）"""
    bg = resume_text.strip() or "（未填写）"
    kw = keywords or "（未填写）"
    sal = salary_range.strip() or "（未填写）"
    return (
        "【个人背景】\n" + bg
        + "\n\n【意向关键词】\n" + kw
        + "\n\n【期望薪资范围】（仅作判断参考，岗位薪资明显低于此区间可适当降低评分，但不必硬性排除）\n" + sal
    )


def build_job_info(job: dict, jd: str = "") -> str:
    """构建结构化岗位信息"""
    salary = (job.get("salary", "") or "").replace("\ue000", "").replace("\uf8ff", "").strip() or "未显示"
    desc = (jd or "").strip()
    if not desc:
        desc = "（列表页无详情）"
    tags = job.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split("|") if t.strip()]
    return (
        "职位：" + (job.get("name", job.get("title", "")) or "")
        + "\n薪资：" + salary
        + "\n公司：" + (job.get("company", job.get("boss_name", "")) or "")
        + "\n城市：" + (job.get("city", job.get("location", "")) or "")
        + "\n技能标签/要求：" + "、".join(tags)
        + "\n岗位描述：" + desc
    )


def build_messages(cfg: dict, job: dict, jd: str = "") -> list:
    """构建发送给 LLM 的 messages"""
    threshold = int(cfg.get("score_threshold", 60))
    custom_prompt = cfg.get("custom_prompt", "")
    extra_prompt = cfg.get("extra_prompt", "")
    resume_text = cfg.get("resume_text", "")
    keywords = cfg.get("keywords", "")
    salary_range = cfg.get("salary_range", "")

    sys_prompt = build_screen_prompt(custom_prompt)
    if extra_prompt:
        sys_prompt += "\n\n【额外要求】\n" + extra_prompt

    user_text = (
        build_user_prompt(resume_text, keywords, salary_range)
        + "\n\n【评分阈值】当前命中分数阈值为 " + str(threshold) + " 分，score>=" + str(threshold) + " 则 match=true，否则 match=false"
        + "\n【待判断岗位】\n" + build_job_info(job, jd)
        + "\n请严格按JSON格式输出（score/reason/match/greeting）。"
    )
    if extra_prompt:
        user_text += "\n\n【额外说明】\n" + extra_prompt

    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_text},
    ]


# ============================================================
# JSON 解析 — 兼容 AI 各种输出格式
# ============================================================

def parse_json_from_ai_output(raw: str) -> dict:
    """从 AI 返回中提取 JSON：直接解析 → 正则提取 → 去 markdown → 再尝试"""
    if not raw:
        return None

    # 1. 直接解析
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. 正则提取第一个 {...}
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
        # 3. 移除可能的 markdown 代码块标记后再试
        cleaned = m.group(0).replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ============================================================
# LLM 调用
# ============================================================

def _require_requests():
    """延迟导入 requests"""
    try:
        import requests as _requests
        return _requests
    except ImportError:
        raise ImportError("缺少 requests 依赖，请安装: pip install requests")


def call_deepseek(api_key: str, model: str, messages: list,
                  base_url: str = "https://api.deepseek.com",
                  max_tokens: int = 10000) -> str:
    """调用 DeepSeek Chat API"""
    requests = _require_requests()
    base = base_url.rstrip("/")
    resp = requests.post(
        f"{base}/v1/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.5,
        },
        timeout=120,
    )
    if not resp.ok:
        text = resp.text[:200] if resp.text else ""
        raise RuntimeError(f"DeepSeek {resp.status_code}: {text}")
    data = resp.json()
    return (data.get("choices", [{}])[0].get("message", {}).get("content", "")) or ""


def call_claude(api_key: str, model: str, messages: list,
                base_url: str = "https://api.anthropic.com",
                max_tokens: int = 10000) -> str:
    """调用 Claude Messages API"""
    requests = _require_requests()
    base = base_url.rstrip("/")

    # system 单独提出来
    sys_content = "\n\n".join(
        m["content"] for m in messages if m["role"] == "system"
    )
    user_msgs = [
        {"role": m["role"], "content": m["content"]}
        for m in messages if m["role"] != "system"
    ]

    resp = requests.post(
        f"{base}/v1/messages",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-dangerous-direct-browser-access": "true",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "system": sys_content,
            "messages": user_msgs,
        },
        timeout=120,
    )
    if not resp.ok:
        text = resp.text[:200] if resp.text else ""
        raise RuntimeError(f"Claude {resp.status_code}: {text}")
    data = resp.json()
    return "".join(c.get("text", "") for c in data.get("content", []))


# ============================================================
# AIScreener 主类
# ============================================================

class AIScreener:
    """AI 筛选器 — 评分 + 招呼语生成"""

    def __init__(
        self,
        provider: str = "deepseek",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        score_threshold: int = 60,
    ):
        """
        Args:
            provider: "deepseek" 或 "claude"
            api_key: API Key
            model: 模型名称
            base_url: 自定义 API 地址
            score_threshold: 命中分数阈值
        """
        self.provider = provider
        self.api_key = api_key
        self.model = model or (
            "deepseek-chat" if provider != "claude" else "claude-sonnet-4-20250514"
        )
        self.base_url = base_url or (
            "https://api.deepseek.com" if provider != "claude" else "https://api.anthropic.com"
        )
        self.score_threshold = score_threshold

    def call_llm(self, messages: list) -> str:
        """统一 LLM 调用"""
        if self.provider == "claude":
            return call_claude(
                self.api_key, self.model, messages,
                base_url=self.base_url,
            )
        return call_deepseek(
            self.api_key, self.model, messages,
            base_url=self.base_url,
        )

    def screen_job(
        self,
        job: dict,
        resume_text: str,
        keywords: str = "",
        salary_range: str = "",
        jd: str = "",
        custom_prompt: str = "",
        extra_prompt: str = "",
    ) -> dict:
        """对单个岗位进行 AI 评分 + 招呼语生成。

        Args:
            job: 岗位字典，需含 name/title, salary, company, location, tags 等
            resume_text: 个人简历/背景文本
            keywords: 意向关键词
            salary_range: 期望薪资范围
            jd: 岗位 JD 详情（可选，有则更准）
            custom_prompt: 自定义招呼语提示词
            extra_prompt: 额外说明提示词

        Returns:
            {"score": int, "match": bool, "reason": str, "greeting": str}
        """
        cfg = {
            "score_threshold": self.score_threshold,
            "custom_prompt": custom_prompt,
            "extra_prompt": extra_prompt,
            "resume_text": resume_text,
            "keywords": keywords,
            "salary_range": salary_range,
        }
        messages = build_messages(cfg, job, jd)
        raw = self.call_llm(messages)

        log.debug("岗位：%s\n--- SYSTEM ---\n%s\n--- USER ---\n%s",
                  job.get("name", job.get("title", "")),
                  messages[0]["content"],
                  messages[1]["content"])

        parsed = parse_json_from_ai_output(raw)
        if not parsed:
            log.warning("AI 解析失败，原始返回：%s", (raw or "(空)")[:200])
            return {
                "score": 0,
                "match": False,
                "reason": "AI解析失败",
                "greeting": "",
            }

        score = parsed.get("score", 0)
        if not isinstance(score, (int, float)):
            score = self.score_threshold if parsed.get("match") else 0
        score = int(score)

        match = parsed.get("match", False)
        if not isinstance(match, bool):
            match = score >= self.score_threshold

        greeting = parsed.get("greeting", "")
        if not isinstance(greeting, str):
            greeting = ""

        return {
            "score": score,
            "match": match,
            "reason": parsed.get("reason", ""),
            "greeting": greeting,
        }

    def screen_jobs_batch(
        self,
        jobs: list,
        resume_text: str,
        keywords: str = "",
        salary_range: str = "",
        jd_map: dict = None,
        custom_prompt: str = "",
        extra_prompt: str = "",
        on_progress=None,
    ) -> list:
        """批量筛选多个岗位。

        Args:
            jobs: 岗位列表
            resume_text: 个人背景
            keywords: 意向关键词
            salary_range: 期望薪资
            jd_map: {job_id: jd_text} JD 详情映射
            custom_prompt: 自定义招呼语提示词
            extra_prompt: 额外说明
            on_progress: 进度回调 (index, total, result)

        Returns:
            [{job..., score, match, reason, greeting}, ...]
        """
        jd_map = jd_map or {}
        results = []
        total = len(jobs)
        for i, job in enumerate(jobs):
            job_id = job.get("id") or job.get("job_id", "")
            jd = jd_map.get(job_id, "")
            try:
                result = self.screen_job(
                    job, resume_text,
                    keywords=keywords,
                    salary_range=salary_range,
                    jd=jd,
                    custom_prompt=custom_prompt,
                    extra_prompt=extra_prompt,
                )
            except Exception as e:
                result = {
                    "score": 0,
                    "match": False,
                    "reason": f"筛选异常: {e}",
                    "greeting": "",
                }

            screened = dict(job)
            screened["score"] = result["score"]
            screened["match"] = result["match"]
            screened["reason"] = result["reason"]
            screened["greeting"] = result["greeting"]
            results.append(screened)

            if on_progress:
                on_progress(i + 1, total, screened)

            matched_label = "✓ 匹配" if result["match"] else "✗ 跳过"
            log.info(
                "[%d/%d] %s | %s分 | %s | %s",
                i + 1, total,
                job.get("name", job.get("title", "")),
                result["score"],
                matched_label,
                result["reason"],
            )

        return results


# ============================================================
# CLI 独立测试
# ============================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("请设置环境变量 DEEPSEEK_API_KEY")
        sys.exit(1)

    screener = AIScreener(
        provider="deepseek",
        api_key=api_key,
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    )

    sample_job = {
        "name": "Python 后端开发工程师",
        "salary": "20-35K·14薪",
        "company": "某知名互联网公司",
        "location": "上海·浦东新区",
        "tags": ["3-5年", "本科", "Python", "Django", "MySQL", "Redis"],
    }

    sample_resume = """
    3年Python后端开发经验，熟悉Django/Flask框架，有微服务架构经验。
    掌握MySQL、Redis、Kafka中间件，有高并发系统开发经验。
    本科计算机专业，期望在上海发展，期望薪资22-30K。
    """

    result = screener.screen_job(
        sample_job,
        resume_text=sample_resume,
        keywords="Python 后端开发",
        salary_range="22-30K",
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
