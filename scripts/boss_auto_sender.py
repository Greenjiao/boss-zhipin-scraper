#!/usr/bin/env python3
"""
BOSS直聘自动发送沟通消息 — 纯 CDP raw protocol

功能:
  1. 在搜索页定位岗位卡片 → 点击"立即沟通" → 弹窗点"继续沟通" → 跳转聊天页
  2. 在聊天页填入招呼语文字 → 上传简历图片 → 发送消息
  3. 批量投递多个岗位

技术:
  - 通过 CDPSession.eval_js() 注入 JS 操作 DOM（仿 JobCopilot content scripts）
  - 多选择器兜底策略，适配 BOSS 直聘 DOM 变更
  - 验证发送成功（输入框清空 或 新消息气泡出现）

用法:
  from scripts.boss_auto_sender import send_to_job, batch_send
  
  cdp = CDPSession(9222)
  send_to_job(cdp, job, greeting="你好，我对这个岗位很感兴趣", image_data=None)
"""

import time
import logging
import base64
import os
import re
from urllib.parse import quote

log = logging.getLogger("boss_auto_sender")

# ============================================================
# BOSS 直聘 DOM 选择器（来自 JobCopilot 验证过的）
# ============================================================

JOB_CARD_SEL = 'li.job-card-box'
IMMEDIATE_CHAT_BTN_SEL = 'a.op-btn-chat'
CHAT_INPUT_SELS = [
    'div#chat-input',
    '#chat-input',
    'div.chat-input',
    '.chat-input[contenteditable]',
    '[contenteditable="true"]',
    'textarea.input-area',
    '.chat-editor textarea',
    'textarea[placeholder]',
    'textarea',
]
SEND_BTN_SELS = [
    'button.btn-send',
    '.btn-send',
    'button[class*="send"]',
    '[class*="send-btn"]',
]
IMG_UPLOAD_SELS = [
    '.btn-sendimg input[type=file]',
    '.toolbar input[type=file]',
    'input[type=file]',
]
USER_LIST_SEL = '.user-list-content li'
MESSAGE_SENT_SEL = '.item-myself'

# ============================================================
# 注入 JS 脚本模板（仿 JobCopilot content-chat.js）
# ============================================================

# 在搜索页定位岗位卡片并点击
FIND_AND_CLICK_CARD_JS = """
(function() {
    var jobId = '__JOB_ID__';
    var jobName = '__JOB_NAME__';
    var company = '__COMPANY__';
    var cards = document.querySelectorAll('li.job-card-box');
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var nameEl = c.querySelector('.job-name');
        var compEl = c.querySelector('.company-name, .boss-info .company-name, [class*="company-name"]');
        var nameText = nameEl ? nameEl.textContent.trim() : '';
        var compText = compEl ? compEl.textContent.trim() : '';
        if (nameText === jobName && compText === company) {
            c.scrollIntoView({block: 'center'});
            return 'found_card';
        }
    }
    // 宽松匹配: 只用岗位名
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var nameEl = c.querySelector('.job-name');
        if (nameEl && nameEl.textContent.trim() === jobName) {
            c.scrollIntoView({block: 'center'});
            return 'found_card_name';
        }
    }
    return 'card_not_found';
})()
"""

# 点击卡片打开详情面板
CLICK_CARD_JS = """
(function() {
    var jobName = '__JOB_NAME__';
    var cards = document.querySelectorAll('li.job-card-box');
    for (var i = 0; i < cards.length; i++) {
        var nameEl = cards[i].querySelector('.job-name');
        if (nameEl && nameEl.textContent.trim() === jobName) {
            cards[i].click();
            return 'clicked';
        }
    }
    return 'not_found';
})()
"""

# 在详情面板点"立即沟通"按钮
CLICK_IMMEDIATE_CHAT_JS = """
(function() {
    // 先尝试精确选择器
    var btn = document.querySelector('a.op-btn-chat');
    if (btn) { btn.click(); return 'clicked_op_btn_chat'; }
    // 兜底: 遍历所有可见按钮/链接/span，匹配文字
    var all = document.querySelectorAll('a, button, span, div');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var tx = (el.textContent || '').trim();
        if (tx === '立即沟通' && el.offsetParent !== null) {
            el.click();
            return 'clicked_text';
        }
    }
    return 'not_found';
})()
"""

# 弹窗中点击"继续沟通"
CLICK_CONTINUE_CHAT_JS = """
(function() {
    var all = document.querySelectorAll('a, button, span, div');
    for (var i = 0; i < all.length; i++) {
        var el = all[i];
        var tx = (el.textContent || '').trim();
        if (tx === '继续沟通' && el.offsetParent !== null) {
            el.click();
            return 'clicked';
        }
    }
    return 'not_found';
})()
"""

# 聊天页：填入招呼语文字并发送
FILL_AND_SEND_JS = """
(function() {
    var greeting = '__GREETING__';
    var inputSels = ['div#chat-input', '#chat-input', 'div.chat-input',
        '.chat-input[contenteditable]', '[contenteditable="true"]',
        'textarea.input-area', '.chat-editor textarea',
        'textarea[placeholder]', 'textarea'];
    var sendSels = ['button.btn-send', '.btn-send',
        'button[class*="send"]', '[class*="send-btn"]'];

    // 找输入框
    var input = null;
    for (var i = 0; i < inputSels.length; i++) {
        var els = document.querySelectorAll(inputSels[i]);
        for (var j = 0; j < els.length; j++) {
            if (els[j].offsetParent !== null || getComputedStyle(els[j]).position === 'fixed') {
                input = els[j];
                break;
            }
        }
        if (input) break;
    }
    if (!input) {
        // dump 页面可编辑元素用于调试
        var dump = [];
        document.querySelectorAll('[contenteditable="true"], textarea, div[id*="input"], div[class*="input"]').forEach(function(el) {
            if (dump.length < 8) dump.push(el.tagName + '#' + (el.id||'') + '.' + (typeof el.className==='string'?el.className.slice(0,40):''));
        });
        return JSON.stringify({ok: false, err: '未找到输入框|' + (dump.join(' | ') || '无')});
    }

    input.focus();
    var editable = input.isContentEditable || input.getAttribute('contenteditable') === 'true';
    if (editable) {
        input.textContent = greeting;
        input.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: greeting}));
    } else {
        var setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
        setter.call(input, greeting);
        input.dispatchEvent(new Event('input', {bubbles: true}));
    }

    // 验证文字已填入
    var val = editable ? (input.textContent || '') : (input.value || '');
    if (!val.trim()) return JSON.stringify({ok: false, err: '文字未填入输入框'});

    // 记录发送前消息数
    var before = document.querySelectorAll('.item-myself').length;

    // 回车发送
    var opt = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true};
    input.dispatchEvent(new KeyboardEvent('keydown', opt));
    input.dispatchEvent(new KeyboardEvent('keypress', opt));
    input.dispatchEvent(new KeyboardEvent('keyup', opt));

    // 兜底: 如果有发送按钮也点一下
    var btn = null;
    for (var i = 0; i < sendSels.length; i++) {
        var b = document.querySelector(sendSels[i]);
        if (b && !b.classList.contains('disabled') && !b.disabled) { btn = b; break; }
    }
    if (btn) btn.click();

    return JSON.stringify({ok: true, beforeCount: before});
})()
"""

# 上传图片到聊天输入框
UPLOAD_IMAGE_JS = """
(function() {
    var imgSels = ['.btn-sendimg input[type=file]',
        '.toolbar input[type=file]', 'input[type=file]'];
    var input = null;
    for (var i = 0; i < imgSels.length; i++) {
        var el = document.querySelector(imgSels[i]);
        if (el) { input = el; break; }
    }
    if (!input) input = document.querySelector('input[type=file]');
    if (!input) return 'no_file_input';

    // 通过内联 script 传递 image data（由 Python 端 base64 解码后以 blob 传入）
    // 这里由 Python 端在外层 eval_js 中拼接 data URL
    return 'ready';
})()
"""

# 验证发送成功（输入框清空 或 新消息气泡出现）
VERIFY_SEND_JS = """
(function() {
    var input = null;
    var inputSels = ['div#chat-input', '#chat-input', 'textarea'];
    for (var i = 0; i < inputSels.length; i++) {
        var el = document.querySelector(inputSels[i]);
        if (el) { input = el; break; }
    }
    var cleared = false;
    if (input) {
        var val = (input.textContent || input.value || '');
        cleared = !val.trim();
    }
    var sentCount = document.querySelectorAll('.item-myself').length;
    return JSON.stringify({cleared: cleared, sentCount: sentCount});
})()
"""

# 在聊天页选择一个会话打开
OPEN_CONVERSATION_JS = """
(function() {
    var company = '__COMPANY__';
    var items = document.querySelectorAll('.user-list-content li');
    if (!items.length) return 'no_items';
    var target = null;
    var ck = company.replace(/\\s/g, '');
    for (var i = 0; i < items.length; i++) {
        var tx = (items[i].textContent || '').replace(/\\s/g, '');
        if (ck && tx.indexOf(ck) >= 0) { target = items[i]; break; }
    }
    if (!target) target = items[0];
    target.click();
    return 'opened';
})()
"""


# ============================================================
# 辅助函数
# ============================================================

def find_visible_element(cdp, sid, selectors, timeout=8.0):
    """轮询等待可见元素出现，返回 True/False"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for sel in selectors:
            result = cdp.eval_js(
                f"(function(){{var el=document.querySelector('{sel}');"
                f"if(el&&(el.offsetParent!==null||getComputedStyle(el).position==='fixed'))return true;"
                f"return false;}})()",
                sid,
            )
            if result:
                return True
        time.sleep(0.5)
    return False


def read_image_as_base64(image_path):
    """读取图片文件并返回 base64 data URI"""
    if not image_path or not os.path.exists(image_path):
        return None
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    ext = os.path.splitext(image_path)[1].lower().replace(".", "")
    mime = "image/png" if ext == "png" else "image/jpeg"
    return f"data:{mime};base64,{data}"


def _escape_js_string(s):
    """安全转义字符串用于 JS 模板"""
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "")


# ============================================================
# 核心操作
# ============================================================

def click_immediate_chat(cdp, sid):
    """在已打开的详情面板中点击"立即沟通"按钮
    
    Returns:
        (success: bool, error: str|None)
    """
    time.sleep(1.5)
    result = cdp.eval_js(CLICK_IMMEDIATE_CHAT_JS, sid)
    if result and "clicked" in str(result):
        log.info("  ✓ 已点击「立即沟通」")
        return True, None
    log.warning("  ✗ 未找到「立即沟通」按钮: %s", result)
    return False, f"未找到立即沟通按钮: {result}"


def click_continue_chat(cdp, sid):
    """在弹窗中点击"继续沟通"按钮
    
    Returns:
        (success: bool, error: str|None)
    """
    time.sleep(2.0)
    result = cdp.eval_js(CLICK_CONTINUE_CHAT_JS, sid)
    if result and "clicked" in str(result):
        log.info("  ✓ 已点击「继续沟通」")
        return True, None
    # 有些情况下可能不需要点"继续沟通"（直接跳转聊天页）
    log.info("  - 未找到「继续沟通」按钮（可能已直接跳转）")
    return True, None  # 不是致命错误


def fill_and_send_message(cdp, sid, greeting):
    """在聊天页填入招呼语并发送
    
    Returns:
        (success: bool, error: str|None)
    """
    # 等待输入框出现
    if not find_visible_element(cdp, sid, CHAT_INPUT_SELS[:3], timeout=8.0):
        return False, "聊天页未找到输入框"

    time.sleep(0.8)
    escaped_greeting = _escape_js_string(greeting)
    js = FILL_AND_SEND_JS.replace("__GREETING__", escaped_greeting)
    result = cdp.eval_js(js, sid)
    
    try:
        import json
        parsed = json.loads(result) if isinstance(result, str) else result
    except (json.JSONDecodeError, ValueError, TypeError):
        parsed = {"ok": False, "err": f"无法解析结果: {result}"}

    if parsed.get("ok"):
        log.info("  ✓ 文字已填入并发送")
        # 验证发送成功
        time.sleep(2.0)
        for _ in range(8):
            verify = cdp.eval_js(VERIFY_SEND_JS, sid)
            try:
                v = json.loads(verify) if isinstance(verify, str) else {}
            except (json.JSONDecodeError, ValueError, TypeError):
                v = {}
            if v.get("cleared") or v.get("sentCount", 0) > (parsed.get("beforeCount", 0) if isinstance(parsed, dict) else 0):
                log.info("  ✓ 发送已验证成功")
                return True, None
            time.sleep(0.5)
        log.warning("  ⚠ 发送未确认（输入框未清空、未见新气泡）")
        return True, "发送未确认"
    return False, parsed.get("err", "未知错误")


def upload_resume_image(cdp, sid, image_b64):
    """上传简历图片到聊天输入框
    
    Args:
        image_b64: base64 data URI 字符串
    
    Returns:
        (success: bool, error: str|None)
    """
    if not image_b64:
        return True, None  # 无图片，跳过

    js = f"""
    (function() {{
        var imgInput = null;
        var sels = ['.btn-sendimg input[type=file]',
            '.toolbar input[type=file]', 'input[type=file]'];
        for (var i = 0; i < sels.length; i++) {{
            var el = document.querySelector(sels[i]);
            if (el) {{ imgInput = el; break; }}
        }}
        if (!imgInput) return 'no_input';
        
        // base64 → File
        var dataUrl = '{image_b64}';
        var parts = dataUrl.split(',');
        var mime = parts[0].match(/:(.*?);/);
        if (!mime) return 'bad_dataurl';
        mime = mime[1];
        var bin = atob(parts[1]);
        var arr = new Uint8Array(bin.length);
        for (var i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
        var file = new File([arr], 'resume.png', {{type: mime}});
        
        var dt = new DataTransfer();
        dt.items.add(file);
        var setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'files').set;
        setter.call(imgInput, dt.files);
        imgInput.dispatchEvent(new Event('change', {{bubbles: true}}));
        return 'uploaded';
    }})()
    """
    result = cdp.eval_js(js, sid)
    if result and "uploaded" in str(result):
        log.info("  ✓ 简历图片已上传")
        time.sleep(2.5)
        return True, None
    elif result and "no_input" in str(result):
        log.warning("  ⚠ 未找到图片上传 input")
        return True, None  # 非致命
    log.warning("  ⚠ 图片上传异常: %s", result)
    return True, None


def navigate_to_chat_and_send(cdp, sid, greeting, image_b64=None):
    """在聊天页执行完整发送流程：上传图片 → 填入文字 → 发送
    
    Returns:
        (success: bool, error: str|None)
    """
    # 1. 上传图片
    if image_b64:
        ok, err = upload_resume_image(cdp, sid, image_b64)
        if not ok:
            return False, err
        time.sleep(0.8)

    # 2. 填入文字并发送
    return fill_and_send_message(cdp, sid, greeting)


def send_to_job(cdp, job, greeting, image_path=None):
    """对单个岗位执行完整投递闭环：
    导航到搜索页 → 定位卡片 → 立即沟通 → 继续沟通 → 发消息
    
    Args:
        cdp: CDPSession 实例
        job: 岗位字典，需含 name/title, company/boss_name, link/job_link
        greeting: 招呼语文案
        image_path: 简历图片文件路径（可选）
    
    Returns:
        {"success": bool, "error": str|None}
    """
    job_name = job.get("name") or job.get("title", "") or ""
    company = job.get("company") or job.get("boss_name", "") or ""
    link = job.get("link") or job.get("job_link", "") or ""

    log.info("投递: %s - %s", job_name, company)

    image_b64 = read_image_as_base64(image_path) if image_path else None

    # 创建新 session
    r = cdp.send("Target.createTarget", {"url": "about:blank"})
    tid = r["result"]["targetId"]
    r = cdp.send("Target.attachToTarget", {"targetId": tid, "flatten": True})
    sid = r["result"]["sessionId"]

    try:
        # 1. 导航到搜索页（优先用抓取时的来源关键词+城市，确保找到卡片列表）
        src_kw = job.get("src_keyword", "") or job_name
        src_city_name = job.get("src_city_name", "") or ""
        # 如果有关键词，构造搜索URL；否则回退到岗位详情链接
        if src_kw:
            search_url = f"https://www.zhipin.com/web/geek/job?query={quote(src_kw)}"
            # 使用城市代码（若存储了）
            city_code = job.get("src_city_code", "")
            if not city_code and src_city_name:
                try:
                    from scripts.boss_cdp_raw import resolve_city
                    _, city_code = resolve_city(src_city_name)
                except ImportError:
                    pass
            if city_code and city_code != src_city_name:
                search_url += f"&city={city_code}"
        elif link:
            search_url = link
            log.info("  无来源关键词，直接使用岗位链接")
        else:
            search_url = f"https://www.zhipin.com/web/geek/job?query={quote(job_name)}"

        cdp.send("Page.navigate", {"url": search_url}, sid)
        log.info("  加载搜索页: %s...", src_kw or job_name)
        time.sleep(6)

        # 2. 定位并点击岗位卡片
        escaped_name = _escape_js_string(job_name)
        escaped_company = _escape_js_string(company)
        card_js = (CLICK_CARD_JS
                   .replace("__JOB_NAME__", escaped_name))
        card_result = cdp.eval_js(card_js, sid)
        log.info("  定位卡片: %s", card_result)
        time.sleep(2.0)

        # 3. 点击"立即沟通"
        ok, err = click_immediate_chat(cdp, sid)
        if not ok:
            return {"success": False, "error": err}

        # 4. 点击"继续沟通"（弹窗）
        ok, err = click_continue_chat(cdp, sid)
        if not ok:
            return {"success": False, "error": err}

        # 5. 等待跳转聊天页
        time.sleep(3.0)

        # 6. 发送消息
        ok, err = navigate_to_chat_and_send(cdp, sid, greeting, image_b64)
        if not ok:
            return {"success": False, "error": err}

        return {"success": True, "error": None}
    finally:
        cdp.send("Target.closeTarget", {"targetId": tid})


def batch_send(cdp, jobs, greetings_map, image_path=None, on_progress=None):
    """批量投递多个岗位
    
    Args:
        cdp: CDPSession 实例
        jobs: 岗位列表
        greetings_map: {job_id: greeting_text} 招呼语映射
        image_path: 简历图片文件路径（可选）
        on_progress: 回调 (index, total, job, result)
    
    Returns:
        [{"job_id": ..., "success": bool, "error": str|None}, ...]
    """
    results = []
    total = len(jobs)
    for i, job in enumerate(jobs):
        job_id = job.get("id") or job.get("job_id", "")
        greeting = greetings_map.get(job_id, "").strip()
        if not greeting:
            result = {"job_id": job_id, "success": False, "error": "招呼语为空"}
            results.append(result)
            if on_progress:
                on_progress(i + 1, total, job, result)
            log.warning("[%d/%d] 跳过（招呼语为空）: %s", i + 1, total, job.get("name", ""))
            continue

        log.info("[%d/%d] 投递: %s", i + 1, total, job.get("name", ""))

        try:
            r = send_to_job(cdp, job, greeting, image_path)
            result = {"job_id": job_id, "success": r["success"], "error": r.get("error")}
        except Exception as e:
            result = {"job_id": job_id, "success": False, "error": str(e)}

        results.append(result)
        if on_progress:
            on_progress(i + 1, total, job, result)

        # 岗位间间隔
        if i < total - 1:
            import random
            gap = random.uniform(3, 8)
            log.info("  等待 %.0fs 后投递下一个...", gap)
            time.sleep(gap)

    ok_count = sum(1 for r in results if r["success"])
    log.info("批量投递完成: 成功 %d / %d", ok_count, total)
    return results


# ============================================================
# CLI 独立测试
# ============================================================

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    # 需要 CDP 连接，这里只做模块导入验证
    print("boss_auto_sender 模块加载成功")
    print("功能: send_to_job, batch_send, fill_and_send_message")
    print("")
    print("使用示例:")
    print("  from scripts.boss_auto_sender import send_to_job, batch_send")
    print("  from scripts.boss_cdp_raw import CDPSession")
    print("  cdp = CDPSession(9222)")
    print("  send_to_job(cdp, {'name': 'Python开发', 'company': 'XX公司'}, '你好')")
