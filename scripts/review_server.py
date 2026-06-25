#!/usr/bin/env python3
"""
BOSS直聘配置中心 + 审核页面 — Flask 本地 Web

功能:
  - ① 配置：AI 提供商、API Key、模型、简历文本、期望薪资、提示词等（自动持久化）
  - ② 审核：展示 AI 筛选结果，二次编辑招呼语，全选批量投递
  - SSE 实时投递进度推送

配置持久化: ~/.boss-zhipin-scraper/config.json

用法:
  # 打开配置+审核页面（无数据时只有配置页）
  python scripts/review_server.py

  # 带已有筛选数据打开
  python scripts/review_server.py --input screened.json

  # 从代码调用
  from scripts.review_server import ReviewServer
  ReviewServer(screened_jobs).start()
"""

import json
import os
import sys
import time
import logging
import threading
import webbrowser
from datetime import datetime

log = logging.getLogger("review_server")

try:
    from flask import Flask, request, jsonify, Response, render_template_string
except ImportError:
    Flask = None

# 配置持久化路径
CONFIG_DIR = os.path.expanduser("~/.boss-zhipin-scraper")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
RESULT_DIR = os.path.join(CONFIG_DIR, "job-result")

DEFAULT_CONFIG = {
    "provider": "deepseek",
    "ds_key": "",
    "deepseek_model": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "claude_key": "",
    "claude_model": "",
    "claude_base_url": "https://api.anthropic.com",
    "resume_text": "",
    "city": "",
    "keywords": "",
    "expect_salary": "",
    "custom_prompt": "",
    "extra_prompt": "",
    "score_threshold": 60,
    "ai_concurrency": 3,
    "detail_gap": "10-25",
    "load_gap": "5-10",
    "scroll_gap": "2-5",
    "page_gap": "12-22",
    "pages": 3,
    "page_size": 30,
    "cdp_port": 9222,
    "resume_image": "",
}


def load_config():
    """加载持久化配置"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            cfg = dict(DEFAULT_CONFIG)
            cfg.update(saved)
            return cfg
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置到文件"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOSS 直聘 · 控制台</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f5f6f8; color: #222; padding: 20px; max-width: 900px; margin: 0 auto;
}
.header {
    background: linear-gradient(135deg, #5cc4bf, #3da8a0);
    color: #fff; padding: 16px 24px; border-radius: 10px 10px 0 0;
    font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 20px;
}
.tabs { display: flex; gap: 4px; }
.tab-btn {
    background: rgba(255,255,255,.2); color: #fff; border: none;
    padding: 6px 16px; border-radius: 6px; font-size: 14px; cursor: pointer;
    transition: .2s;
}
.tab-btn.active { background: #fff; color: #3da8a0; font-weight: 700; }
.tab-content { display: none; }
.tab-content.active { display: block; }
.card {
    background: #fff; border-radius: 0 0 10px 10px;
    box-shadow: 0 2px 12px rgba(0,0,0,.06); margin-bottom: 18px;
}
.card-h {
    font-size: 14px; font-weight: 600; color: #555; padding: 12px 20px;
    border-bottom: 1px solid #f0f0f0; cursor: pointer; display: flex;
    justify-content: space-between; align-items: center;
}
.card-b { padding: 14px 20px; }
.form-group { margin-bottom: 12px; }
.form-group label {
    display: block; font-size: 13px; font-weight: 600; color: #444; margin-bottom: 4px;
}
.form-group .hint {
    font-size: 11px; color: #999; font-weight: 400; margin-left: 4px;
}
.form-group input, .form-group select, .form-group textarea {
    width: 100%; padding: 8px 10px; border: 1px solid #ddd;
    border-radius: 6px; font-size: 13px; font-family: inherit;
    transition: border-color .2s;
}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
    border-color: #5cc4bf; outline: none; box-shadow: 0 0 0 2px rgba(92,196,191,.15);
}
.form-group textarea { resize: vertical; min-height: 80px; font-size: 12px; }
.form-row { display: flex; gap: 10px; }
.form-row .form-group { flex: 1; }
.btn-primary {
    background: #5cc4bf; color: #fff; border: none; padding: 10px 24px;
    border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer;
    transition: .2s;
}
.btn-primary:hover { background: #4ab5af; }
.btn-primary:disabled { background: #bbb; cursor: not-allowed; }
.btn-stop { background: #e74c3c; color: #fff; border: none; padding: 8px 18px;
    border-radius: 6px; font-size: 14px; cursor: pointer; }
.btn-sm {
    background: #e8f0fe; color: #3da8a0; border: 1px solid #c4dbf5;
    padding: 4px 12px; border-radius: 4px; font-size: 12px; cursor: pointer;
}
.saved-msg { color: #2a8a6e; font-size: 13px; margin-left: 10px; display: none; }
.toolbar {
    display: flex; align-items: center; gap: 12px; padding: 12px 20px;
    border-bottom: 1px solid #eee; flex-wrap: wrap;
}
.toolbar label { font-size: 14px; cursor: pointer; display: flex; align-items: center; gap: 4px; }
.stats { font-size: 13px; color: #888; margin-left: auto; }
.job-item {
    display: flex; align-items: flex-start; gap: 10px; padding: 14px 20px;
    border-bottom: 1px solid #f0f0f0; transition: .15s;
}
.job-item:hover { background: #f9fbfa; }
.job-item.analyzing {
    background: #fffbe6; border-left: 3px solid #faad14;
    transition: background .2s, border-left .2s;
}
.job-item input[type=checkbox] { margin-top: 4px; flex-shrink: 0; }
.job-main { flex: 1; min-width: 0; }
.job-title { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.job-sub { font-size: 13px; color: #777; margin-top: 2px; }
.job-reason { font-size: 13px; margin-top: 4px; padding: 4px 8px; border-radius: 4px; }
.job-reason.match { color: #2a8a6e; background: #e8f8f3; }
.job-reason.skip { color: #999; background: #f8f8f8; }
.score-badge { font-size: 12px; padding: 2px 7px; border-radius: 10px; font-weight: 700; }
.score-badge.m { background: #e8f8f3; color: #2a8a6e; }
.score-badge.s { background: #f0f0f0; color: #aaa; }
.greet-box {
    width: 100%; min-height: 48px; margin-top: 8px; padding: 8px 10px;
    border: 1px solid #ddd; border-radius: 6px; font-size: 13px; resize: vertical;
    font-family: inherit; line-height: 1.6;
}
.greet-box:focus { border-color: #5cc4bf; outline: none; box-shadow: 0 0 0 2px rgba(92,196,191,.15); }
.status-bar {
    padding: 12px 20px; font-size: 13px; background: #fafafa;
    border-radius: 8px; margin-top: 12px; display: flex; align-items: center; gap: 10px;
}
.progress-bar {
    flex: 1; height: 6px; background: #e0e0e0; border-radius: 3px; overflow: hidden;
}
.progress-fill {
    height: 100%; background: linear-gradient(90deg, #5cc4bf, #3da8a0);
    border-radius: 3px; transition: width .3s;
}
.result-ok { color: #2a8a6e; }
.result-fail { color: #e74c3c; }
.section-title {
    font-size: 14px; font-weight: 600; color: #555; padding: 12px 20px;
    border-bottom: 1px solid #f0f0f0; display: flex; align-items: center; gap: 8px;
}
.section-title .count { font-size: 12px; color: #999; font-weight: 400; }
.empty { padding: 30px 20px; text-align: center; color: #bbb; font-size: 14px; }
.tip { background: #fff9e6; border: 1px solid #ffe08a; padding: 10px 14px;
    border-radius: 6px; font-size: 13px; color: #8a6d3b; margin: 12px 0; }
.provider-box { display: none; }
.provider-box.active { display: block; }
.btn-view {
    background: #e8f0fe; color: #3da8a0; border: 1px solid #c4dbf5;
    padding: 2px 8px; border-radius: 4px; font-size: 11px; cursor: pointer;
    text-decoration: none; display: inline-block; flex-shrink: 0;
}
.btn-analyze {
    background: linear-gradient(135deg, #5cc4bf, #3da8a0); color: #fff;
    border: none; padding: 4px 12px; border-radius: 4px; font-size: 12px;
    cursor: pointer; min-width: 90px; text-align: center; flex-shrink: 0;
    transition: .2s;
}
.btn-analyze:hover { opacity: 0.9; }
.btn-analyze.analyzing { background: #bbb; cursor: not-allowed; }
.btn-analyze.done { background: #d4edda; color: #2a8a6e; cursor: default; }
.jd-preview {
    font-size: 12px; color: #999; margin-top: 4px; line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
    overflow: hidden; cursor: pointer; white-space: pre-wrap;
}
.jd-preview.expanded {
    display: block; -webkit-line-clamp: unset;
    max-height: 300px; overflow-y: auto; color: #666;
}
.job-actions { display: flex; gap: 6px; align-items: center; flex-shrink: 0; }
.job-body { flex: 1; min-width: 0; }
</style>
</head>
<body>

<div class="header">
    BOSS 直聘 · 控制台
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('config')">① 配置</button>
        <button class="tab-btn" onclick="switchTab('review')" id="reviewTabBtn">② 审核 <span id="reviewBadge"></span></button>
    </div>
</div>

<!-- ===== 配置 Tab ===== -->
<div class="tab-content active" id="tab-config">
<div class="card">
    <div class="card-h"><span>AI 服务</span></div>
    <div class="card-b">
        <div class="form-group">
            <label>使用的 AI</label>
            <select id="cfgProvider" onchange="onProviderChange()">
                <option value="deepseek">DeepSeek（国内稳定）</option>
                <option value="claude">Claude（Anthropic）</option>
            </select>
        </div>
        <div class="provider-box" id="dsBox">
            <div class="form-group">
                <label>DeepSeek API Key</label>
                <input type="password" id="cfgDsKey" placeholder="sk- 开头">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>模型<span class="hint">填完 Key 后点击刷新获取可用模型</span></label>
                    <div style="display:flex;gap:4px">
                        <select id="cfgDeepseekModel" style="flex:1"><option value="">请先填写 API Key 后点击刷新</option></select>
                        <button type="button" class="btn-sm" onclick="fetchModels('deepseek')" style="flex-shrink:0">⟳ 刷新</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>Base URL<span class="hint">默认官方，可填兼容代理</span></label>
                    <input type="text" id="cfgDeepseekBaseUrl" placeholder="https://api.deepseek.com">
                </div>
            </div>
        </div>
        <div class="provider-box" id="claudeBox">
            <div class="form-group">
                <label>Claude API Key</label>
                <input type="password" id="cfgClaudeKey" placeholder="sk-ant- 开头">
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label>模型<span class="hint">填完 Key 后点击刷新获取可用模型</span></label>
                    <div style="display:flex;gap:4px">
                        <select id="cfgClaudeModel" style="flex:1"><option value="">请先填写 API Key 后点击刷新</option></select>
                        <button type="button" class="btn-sm" onclick="fetchModels('claude')" style="flex-shrink:0">⟳ 刷新</button>
                    </div>
                </div>
                <div class="form-group">
                    <label>Base URL<span class="hint">默认官方，可填兼容代理</span></label>
                    <input type="text" id="cfgClaudeBaseUrl" placeholder="https://api.anthropic.com">
                </div>
            </div>
        </div>
    </div>
</div>

<div class="card">
    <div class="card-h"><span>个人简历 & 偏好</span></div>
    <div class="card-b">
        <div class="form-group">
            <label>个人简历 / 背景<span class="hint">AI 筛选 + 招呼语生成的核心依据</span></label>
            <textarea id="cfgResumeText" rows="6" placeholder="粘贴你的简历/背景：技能、项目经历、教育背景、实习经历、期望城市与薪资等，越详细招呼语越精准"></textarea>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>城市<span class="hint">分号/逗号分隔可多个，每个城市×每个关键词都会搜</span></label>
                <input type="text" id="cfgCity" placeholder="如：上海;北京;杭州;深圳">
            </div>
            <div class="form-group">
                <label>意向关键词<span class="hint">分号/逗号分隔，每个关键词×每个城市</span></label>
                <input type="text" id="cfgKeywords" placeholder="如：Python;后端;数据分析">
            </div>
        </div>
            <div class="form-group">
                <label>期望薪资<span class="hint">如 20-35K，AI 判断参考</span></label>
                <input type="text" id="cfgExpectSalary" placeholder="如：20-35K">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>简历图片<span class="hint">投递时发给 HR</span></label>
                <input type="file" id="cfgResumeImage" accept="image/*" onchange="handleImageUpload(event)">
            </div>
            <div class="form-group">
                <label>CDP 端口<span class="hint">Chrome 调试端口</span></label>
                <input type="number" id="cfgCdpPort" value="9222" min="1024" max="65535">
            </div>
        </div>
        <div id="imgPreview" style="margin-bottom:8px"></div>
    </div>
</div>

<div class="card">
    <div class="card-h"><span>提示词设置</span></div>
    <div class="card-b">
        <div class="form-group">
            <label>自定义招呼语提示词<span class="hint">替换默认招呼语格式要求段</span></label>
            <textarea id="cfgCustomPrompt" rows="4" placeholder="留空使用内置默认"></textarea>
        </div>
        <div class="form-group">
            <label>额外说明提示词<span class="hint">补充风格/要求，例：招呼语更活泼 / 强调我能远程</span></label>
            <textarea id="cfgExtraPrompt" rows="3" placeholder="例：打招呼语写得更口语主动"></textarea>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>AI 命中阈值<span class="hint">0-100，综合评分超过此值算匹配</span></label>
                <input type="number" id="cfgScoreThreshold" value="60" min="0" max="100">
            </div>
            <div class="form-group">
                <label>AI 并发数<span class="hint">全选分析时同时分析的线程数，1为单线程</span></label>
                <input type="number" id="cfgAiConcurrency" value="3" min="1" max="10">
            </div>
        </div>
    </div>
</div>

<div class="card">
    <div class="card-h"><span>抓取设置</span></div>
    <div class="card-b">
        <div class="form-row">
            <div class="form-group">
                <label>默认页数<span class="hint">每次搜索抓取多少页</span></label>
                <input type="number" id="cfgPages" value="3" min="1" max="10">
            </div>
            <div class="form-group">
                <label>每页个数<span class="hint">每页最多职位数，默认30</span></label>
                <input type="number" id="cfgPageSize" value="30" min="5" max="100">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>详情页间隔<span class="hint">每个岗位详情之间等待秒数范围，如 5-15</span></label>
                <input type="text" id="cfgDetailGap" placeholder="10-25">
            </div>
            <div class="form-group">
                <label>页面加载等待<span class="hint">打开详情页后等待加载的秒数范围，如 3-8</span></label>
                <input type="text" id="cfgLoadGap" placeholder="5-10">
            </div>
        </div>
        <div class="form-row">
            <div class="form-group">
                <label>滚动间隔<span class="hint">模拟滚动每次停留秒数范围，如 1-3</span></label>
                <input type="text" id="cfgScrollGap" placeholder="2-5">
            </div>
            <div class="form-group">
                <label>翻页间隔<span class="hint">搜索翻页之间等待秒数范围，如 8-20</span></label>
                <input type="text" id="cfgPageGap" placeholder="12-22">
            </div>
        </div>
    </div>
</div>

<div class="card">
    <div class="card-b">
        <button class="btn-primary" onclick="saveConfig()">💾 保存配置</button>
        <span class="saved-msg" id="savedMsg">✓ 已保存</span>
    </div>
</div>
</div>

<!-- ===== 审核 Tab ===== -->
<div class="tab-content" id="tab-review">
<div class="card">
    <div style="display:flex;align-items:center;gap:8px;padding:10px 20px;border-bottom:1px solid #eee;flex-wrap:wrap">
        <span style="font-size:13px;color:#555;font-weight:600;white-space:nowrap">历史记录:</span>
        <select id="historySelect" onchange="onHistoryChange()" style="flex:1;min-width:200px;padding:6px 8px;border:1px solid #ddd;border-radius:6px;font-size:13px">
            <option value="">当前会话</option>
        </select>
        <button class="btn-sm" onclick="refreshHistory()" style="flex-shrink:0">↻ 刷新</button>
    </div>
    <div class="toolbar">
        <label><input type="checkbox" id="selAll" checked onchange="toggleAll(this)"> 全选匹配</label>
        <button class="btn-primary" id="btnDeliver" onclick="startDeliver()">投递选中</button>
        <button class="btn-primary" id="btnAnalyzeAll" onclick="startBatchAnalyze()" style="display:none">🤖 全选分析</button>
        <button class="btn-sm" id="btnSaveScreened" onclick="saveScreened()" style="display:none">💾 保存筛选结果</button>
        <button class="btn-stop" id="btnPause" onclick="pauseAnalysis()" style="display:none">暂停</button>
        <button class="btn-stop" id="btnStop" onclick="stopDeliver()" style="display:none">停止</button>
        <span class="stats" id="statsText"></span>
    </div>

    <div class="section-title">✓ 匹配岗位 <span class="count" id="matchedCount"></span></div>
    <div id="matchedList"></div>

    <div class="section-title" style="margin-top:12px">✗ 未匹配岗位 <span class="count" id="skippedCount"></span></div>
    <div id="skippedList"></div>

    <div class="status-bar" id="statusBar" style="display:none">
        <span id="statusPhase">待命中</span>
        <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0"></div></div>
        <span id="statusProgress">0 / 0</span>
        <span id="statusResult"></span>
    </div>
</div>
</div>

<script>
// ===== Tab 切换 =====
function switchTab(name) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    document.querySelectorAll('.tab-btn').forEach(b => {
        if (b.textContent.includes(name === 'config' ? '配置' : '审核')) b.classList.add('active');
    });
}

// ===== 配置管理 =====
function onProviderChange() {
    const p = document.getElementById('cfgProvider').value;
    document.getElementById('dsBox').classList.toggle('active', p === 'deepseek');
    document.getElementById('claudeBox').classList.toggle('active', p === 'claude');
}

// 设置 select 的值，若不存在该选项则添加
function setSelectValue(selectId, value) {
    if (!value) return;
    const sel = document.getElementById(selectId);
    if (!sel) return;
    // 检查是否存在该值
    for (let i = 0; i < sel.options.length; i++) {
        if (sel.options[i].value === value) { sel.value = value; return; }
    }
    // 不存在则追加
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = value;
    sel.appendChild(opt);
    sel.value = value;
}

// 从官方 API 获取可用模型列表
async function fetchModels(provider) {
    const selectId = provider === 'claude' ? 'cfgClaudeModel' : 'cfgDeepseekModel';
    const keyId = provider === 'claude' ? 'cfgClaudeKey' : 'cfgDsKey';
    const baseUrlId = provider === 'claude' ? 'cfgClaudeBaseUrl' : 'cfgDeepseekBaseUrl';
    const select = document.getElementById(selectId);
    const key = document.getElementById(keyId).value.trim();
    let baseUrl = (document.getElementById(baseUrlId).value || '').trim();
    if (!baseUrl) baseUrl = provider === 'claude' ? 'https://api.anthropic.com' : 'https://api.deepseek.com';
    baseUrl = baseUrl.replace(/\/$/, '');

    if (!key) {
        select.innerHTML = '<option value="">请先填写 API Key</option>';
        return;
    }

    select.innerHTML = '<option value="">获取中…</option>';
    select.disabled = true;

    try {
        const resp = await fetch('/api/fetch-models', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({provider: provider, key: key, base_url: baseUrl})
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const data = await resp.json();
        const models = data.models || [];

        if (!models.length) {
            select.innerHTML = '<option value="">API 未返回模型</option>';
            return;
        }

        // 保留之前选中的值
        const currentVal = select.value;
        select.innerHTML = models.map(m =>
            '<option value="' + m + '"' + (m === currentVal ? ' selected' : '') + '>' + m + '</option>'
        ).join('');
        if (!select.value) select.value = models[0];
    } catch(e) {
        select.innerHTML = '<option value="">获取失败，检查 Key / URL</option>';
        console.error('fetchModels ' + provider + ':', e);
    } finally {
        select.disabled = false;
    }
}

async function loadConfig() {
    const resp = await fetch('/api/config');
    const cfg = await resp.json();
    document.getElementById('cfgProvider').value = cfg.provider || 'deepseek';
    document.getElementById('cfgDsKey').value = cfg.ds_key || '';
    document.getElementById('cfgDeepseekBaseUrl').value = cfg.deepseek_base_url || 'https://api.deepseek.com';
    document.getElementById('cfgClaudeKey').value = cfg.claude_key || '';
    document.getElementById('cfgClaudeBaseUrl').value = cfg.claude_base_url || 'https://api.anthropic.com';
    // 模型下拉：恢复保存值
    setSelectValue('cfgDeepseekModel', cfg.deepseek_model);
    setSelectValue('cfgClaudeModel', cfg.claude_model);
    document.getElementById('cfgResumeText').value = cfg.resume_text || '';
    document.getElementById('cfgCity').value = cfg.city || '';
    document.getElementById('cfgKeywords').value = cfg.keywords || '';
    document.getElementById('cfgExpectSalary').value = cfg.expect_salary || '';
    document.getElementById('cfgCustomPrompt').value = cfg.custom_prompt || '';
    document.getElementById('cfgExtraPrompt').value = cfg.extra_prompt || '';
    document.getElementById('cfgScoreThreshold').value = cfg.score_threshold || 60;
    document.getElementById('cfgAiConcurrency').value = cfg.ai_concurrency || 3;
    document.getElementById('cfgDetailGap').value = cfg.detail_gap || '10-25';
    document.getElementById('cfgLoadGap').value = cfg.load_gap || '5-10';
    document.getElementById('cfgScrollGap').value = cfg.scroll_gap || '2-5';
    document.getElementById('cfgPageGap').value = cfg.page_gap || '12-22';
    document.getElementById('cfgPages').value = cfg.pages || 3;
    document.getElementById('cfgPageSize').value = cfg.page_size || 30;
    document.getElementById('cfgCdpPort').value = cfg.cdp_port || 9222;
    if (cfg.resume_image) {
        document.getElementById('imgPreview').innerHTML = '<img src="' + cfg.resume_image + '" style="max-height:80px;border-radius:4px">';
    }
    onProviderChange();
}

async function saveConfig() {
    const cfg = {
        provider: document.getElementById('cfgProvider').value,
        ds_key: document.getElementById('cfgDsKey').value.trim(),
        deepseek_model: document.getElementById('cfgDeepseekModel').value.trim(),
        deepseek_base_url: document.getElementById('cfgDeepseekBaseUrl').value.trim(),
        claude_key: document.getElementById('cfgClaudeKey').value.trim(),
        claude_model: document.getElementById('cfgClaudeModel').value.trim(),
        claude_base_url: document.getElementById('cfgClaudeBaseUrl').value.trim(),
        resume_text: document.getElementById('cfgResumeText').value,
        city: document.getElementById('cfgCity').value.trim(),
        keywords: document.getElementById('cfgKeywords').value.trim(),
        expect_salary: document.getElementById('cfgExpectSalary').value.trim(),
        custom_prompt: document.getElementById('cfgCustomPrompt').value,
        extra_prompt: document.getElementById('cfgExtraPrompt').value,
        score_threshold: parseInt(document.getElementById('cfgScoreThreshold').value) || 60,
        ai_concurrency: parseInt(document.getElementById('cfgAiConcurrency').value) || 3,
        detail_gap: document.getElementById('cfgDetailGap').value.trim() || '10-25',
        load_gap: document.getElementById('cfgLoadGap').value.trim() || '5-10',
        scroll_gap: document.getElementById('cfgScrollGap').value.trim() || '2-5',
        page_gap: document.getElementById('cfgPageGap').value.trim() || '12-22',
        pages: parseInt(document.getElementById('cfgPages').value) || 3,
        page_size: parseInt(document.getElementById('cfgPageSize').value) || 30,
        cdp_port: parseInt(document.getElementById('cfgCdpPort').value) || 9222,
    };
    await fetch('/api/config', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(cfg)
    });
    const msg = document.getElementById('savedMsg');
    msg.style.display = 'inline';
    setTimeout(() => msg.style.display = 'none', 2000);
}

function handleImageUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(ev) {
        document.getElementById('imgPreview').innerHTML =
            '<img src="' + ev.target.result + '" style="max-height:80px;border-radius:4px">';
        fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({resume_image: ev.target.result})
        });
    };
    reader.readAsDataURL(file);
}

// ===== 审核 =====
let allJobs = [];
let jobMap = {};
let checkedSet = new Set();

async function loadReviewData() {
    try {
        const resp = await fetch('/api/data');
        const data = await resp.json();
        allJobs = data.jobs || [];
        jobMap = {};
        allJobs.forEach(j => { jobMap[j.id] = j; if (j.match && j.greeting) checkedSet.add(j.id); });
        renderReview();
        const badge = document.getElementById('reviewBadge');
        if (allJobs.length) badge.textContent = ' (' + allJobs.filter(j=>j.match).length + ' 匹配)';
    } catch(e) { console.error(e); }
}

function escapeHtml(s) {
    return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderReview() {
    // 判断是否为详情模式
    const isDetailMode = allJobs.length > 0 && allJobs[0].is_detail_mode;

    if (isDetailMode) {
        renderDetailMode();
        return;
    }

    // ===== 筛选模式（原有逻辑 + 查看按钮） =====
    const matched = allJobs.filter(j => j.match);
    const skipped = allJobs.filter(j => !j.match);
    // 恢复可能被详情模式隐藏的元素
    document.getElementById('skippedCount').parentElement.style.display = '';
    document.getElementById('selAll').style.display = '';
    document.getElementById('selAll').closest('label').style.display = '';
    document.getElementById('btnDeliver').style.display = 'inline-block';
    document.getElementById('btnAnalyzeAll').style.display = 'none';
    document.getElementById('btnSaveScreened').style.display = 'none';
    document.getElementById('matchedCount').textContent = matched.length + ' 个';
    document.getElementById('skippedCount').textContent = skipped.length + ' 个';
    document.getElementById('statsText').textContent = '匹配 ' + matched.length + ' / ' + allJobs.length;

    let mHtml = '';
    if (matched.length === 0) {
        mHtml = '<div class="empty">无匹配岗位<br><span style="font-size:12px">先用 AI 筛选得到数据，或加载已有筛选结果</span></div>';
    } else {
        matched.forEach(j => {
            const jobLink = j.job_link || j.link || '';
            mHtml += '<div class="job-item">'
                + '<input type="checkbox" ' + (checkedSet.has(j.id) ? 'checked' : '')
                + ' onchange="toggleJob(\'' + j.id + '\', this.checked)"'
                + (j.greeting ? '' : ' disabled') + '>'
                + '<div class="job-main">'
                + '<div class="job-title">' + escapeHtml(j.name || j.title)
                + '<span class="score-badge m">' + (j.score || 0) + '分</span>'
                + (jobLink ? '<a href="' + jobLink + '" target="_blank" class="btn-view" style="margin-left:auto">👁 查看</a>' : '')
                + '</div>'
                + '<div class="job-sub">' + escapeHtml(j.company || j.boss_name || '')
                + ' · ' + escapeHtml(j.salary || '') + '</div>'
                + (j.jd ? '<div class="jd-preview" onclick="this.classList.toggle(\'expanded\')">' + escapeHtml(j.jd) + '</div>' : '')
                + '<div class="job-reason match">' + (j.reason || '') + '</div>'
                + '<textarea class="greet-box" data-id="' + j.id + '" onchange="updateGreeting(\''
                + j.id + '\', this.value)">' + escapeHtml(j.greeting || '') + '</textarea>'
                + '</div></div>';
        });
    }
    document.getElementById('matchedList').innerHTML = mHtml;

    let sHtml = '';
    if (skipped.length === 0) {
        sHtml = '<div class="empty">无跳过岗位</div>';
    } else {
        skipped.forEach(j => {
            const jobLink = j.job_link || j.link || '';
            sHtml += '<div class="job-item" style="opacity:0.7">'
                + '<input type="checkbox" disabled>'
                + '<div class="job-main">'
                + '<div class="job-title">' + escapeHtml(j.name || j.title)
                + '<span class="score-badge s">' + (j.score || 0) + '分</span>'
                + (jobLink ? '<a href="' + jobLink + '" target="_blank" class="btn-view" style="margin-left:auto">👁 查看</a>' : '')
                + '</div>'
                + '<div class="job-sub">' + escapeHtml(j.company || j.boss_name || '')
                + ' · ' + escapeHtml(j.salary || '') + '</div>'
                + (j.jd ? '<div class="jd-preview" onclick="this.classList.toggle(\'expanded\')">' + escapeHtml(j.jd) + '</div>' : '')
                + '<div class="job-reason skip">' + (j.reason || '') + '</div>'
                + '</div></div>';
        });
    }
    document.getElementById('skippedList').innerHTML = sHtml;

    const selAll = document.getElementById('selAll');
    const matchedCbs = document.querySelectorAll('#matchedList input[type=checkbox]:not([disabled])');
    selAll.checked = matchedCbs.length > 0 && Array.from(matchedCbs).every(cb => cb.checked);
}

// ===== 详情模式渲染 =====
function renderDetailMode() {
    document.getElementById('matchedCount').textContent = allJobs.length + ' 个';
    document.getElementById('skippedCount').textContent = '';
    document.getElementById('statsText').textContent = '详情模式 · ' + allJobs.length + ' 个岗位';

    // Toolbar 控制：隐藏筛选模式按钮，显示详情模式按钮
    document.getElementById('selAll').style.display = 'none';
    document.getElementById('selAll').closest('label').style.display = 'none';
    document.getElementById('btnDeliver').style.display = 'none';

    const analyzed = allJobs.filter(j => j.score > 0);
    const unanalyzed = allJobs.filter(j => j.score === 0);

    // 全选分析按钮：有未分析岗位时显示
    document.getElementById('btnAnalyzeAll').style.display = unanalyzed.length > 0 ? 'inline-block' : 'none';
    // 保存按钮：有分析完成的岗位时显示
    document.getElementById('btnSaveScreened').style.display = analyzed.length > 0 ? 'inline-block' : 'none';
    // 停止按钮：分析中时由 startBatchAnalyze 控制

    let html = '';
    if (allJobs.length === 0) {
        html = '<div class="empty">无岗位数据</div>';
    } else {
        allJobs.forEach((j, idx) => {
            const jobLink = j.job_link || j.link || '';
            const isAnalyzed = j.score > 0;
            const location = j.location || j.city || '';
            const tags = j.tags_list || (j.skill_tags && j.skill_tags.join(', ')) || '';
            const hasJd = !!(j.jd);

            html += '<div class="job-item" id="job-row-' + j.id + '">';

            // 如果已分析且匹配，显示复选框
            if (isAnalyzed && j.match) {
                html += '<input type="checkbox" ' + (checkedSet.has(j.id) ? 'checked' : '')
                    + ' onchange="toggleJob(\'' + j.id + '\', this.checked)">';
            } else if (isAnalyzed) {
                html += '<input type="checkbox" disabled>';
            } else {
                html += '<input type="checkbox" disabled style="visibility:hidden">';
            }

            html += '<div class="job-body">';
            // 标题行
            html += '<div class="job-title">' + escapeHtml(j.name || j.title);
            if (isAnalyzed) {
                html += '<span class="score-badge ' + (j.match ? 'm' : 's') + '">' + j.score + '分</span>';
            }
            html += '</div>';

            // 公司 + 薪资 + 位置
            html += '<div class="job-sub">' + escapeHtml(j.company || j.boss_name || '');
            if (j.salary) html += ' · ' + escapeHtml(j.salary);
            if (location) html += ' · ' + escapeHtml(location);
            html += '</div>';

            // 标签
            if (tags) {
                html += '<div class="job-sub" style="color:#999;font-size:12px">' + escapeHtml(tags) + '</div>';
            }

            // JD
            if (hasJd) {
                html += '<div class="jd-preview" onclick="this.classList.toggle(\'expanded\')">' + escapeHtml(j.jd || '') + '</div>';
            }

            // AI 分析结果（已分析时显示）
            if (isAnalyzed) {
                html += '<div class="job-reason ' + (j.match ? 'match' : 'skip') + '">' + (j.reason || '') + '</div>';
                if (j.greeting) {
                    html += '<textarea class="greet-box" data-id="' + j.id + '" onchange="updateGreeting(\''
                        + j.id + '\', this.value)">' + escapeHtml(j.greeting || '') + '</textarea>';
                }
            }
            html += '</div>'; // job-body

            // 右侧按钮
            html += '<div class="job-actions">';
            if (jobLink) {
                html += '<a href="' + jobLink + '" target="_blank" class="btn-view">👁 查看</a>';
            }
            if (isAnalyzed) {
                html += '<button class="btn-analyze done" disabled>✓ 已分析</button>';
            } else {
                html += '<button class="btn-analyze" id="btn-analyze-' + j.id + '" onclick="analyzeJob(\'' + j.id + '\')">🤖 AI 分析</button>';
            }
            html += '</div>';

            html += '</div>'; // job-item
        });
    }
    document.getElementById('matchedList').innerHTML = html;
    document.getElementById('skippedList').innerHTML = '';
    document.getElementById('skippedCount').parentElement.style.display = 'none';
}

// ===== 单条 AI 分析 =====
let activeAnalysis = null;

async function analyzeJob(jobId) {
    const btn = document.getElementById('btn-analyze-' + jobId);
    if (!btn) return;
    btn.textContent = '分析中…';
    btn.classList.add('analyzing');
    btn.disabled = true;

    // 显示状态栏 + 高亮当前行
    document.getElementById('statusBar').style.display = 'flex';
    document.getElementById('statusPhase').textContent = '正在分析...';
    document.getElementById('statusResult').textContent = '';
    document.getElementById('progressFill').style.width = '20%';
    document.getElementById('statusProgress').textContent = '';
    _highlightJobRow(jobId);

    const es = new EventSource('/api/analyze-job-stream?job_id=' + encodeURIComponent(jobId));
    activeAnalysis = es;

    es.onmessage = function(e) {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
            document.getElementById('statusPhase').textContent = data.label || '分析中...';
        } else if (data.type === 'result') {
            const job = jobMap[data.job_id];
            if (job) {
                job.score = data.score;
                job.match = data.match;
                job.reason = data.reason;
                job.greeting = data.greeting;
                if (data.match && data.greeting) checkedSet.add(data.job_id);
            }
        } else if (data.type === 'done') {
            es.close();
            activeAnalysis = null;
            _clearHighlight();
            document.getElementById('statusBar').style.display = 'none';
            renderReview();
        } else if (data.type === 'error') {
            alert('AI 分析失败: ' + (data.message || '未知错误'));
            btn.textContent = '🤖 AI 分析';
            btn.classList.remove('analyzing');
            btn.disabled = false;
            _clearHighlight();
            document.getElementById('statusBar').style.display = 'none';
            es.close();
            activeAnalysis = null;
        }
    };
    es.onerror = function() {
        if (activeAnalysis === es) {
            btn.textContent = '🤖 AI 分析';
            btn.classList.remove('analyzing');
            btn.disabled = false;
            activeAnalysis = null;
        }
        _clearHighlight();
        document.getElementById('statusBar').style.display = 'none';
        es.close();
    };
}

// ===== 全选分析 =====
let batchAnalysisEs = null;
let highlightedRow = null;

async function startBatchAnalyze() {
    const unanalyzed = allJobs.filter(j => j.score === 0);
    if (unanalyzed.length === 0) {
        alert('所有岗位已分析完毕');
        return;
    }
    // 如果已分析部分岗位，继续分析不弹确认框；全新分析才确认
    const alreadyAnalyzed = allJobs.filter(j => j.score > 0).length;
    if (alreadyAnalyzed === 0 && !confirm('将对 ' + unanalyzed.length + ' 个未分析岗位进行 AI 分析，是否继续？')) return;

    const btn = document.getElementById('btnAnalyzeAll');
    btn.textContent = '分析中…';
    btn.disabled = true;
    document.getElementById('btnPause').style.display = 'inline-block';
    document.getElementById('btnStop').style.display = 'inline-block';
    document.getElementById('btnSaveScreened').style.display = 'none';
    document.getElementById('statusBar').style.display = 'flex';
    document.getElementById('statusPhase').textContent = '全选分析中...';
    document.getElementById('statusResult').textContent = '';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('statusProgress').textContent = '0 / ' + unanalyzed.length;

    batchAnalysisEs = new EventSource('/api/analyze-all-stream');
    batchAnalysisEs.onmessage = function(e) {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
            document.getElementById('progressFill').style.width = (data.current / data.total * 100) + '%';
            document.getElementById('statusProgress').textContent = data.current + ' / ' + data.total;
            document.getElementById('statusPhase').textContent = data.label || '分析中...';
            // 高亮当前行
            _highlightJobRow(data.job_id);
        } else if (data.type === 'result') {
            const job = jobMap[data.job_id];
            if (job) {
                job.score = data.score;
                job.match = data.match;
                job.reason = data.reason;
                job.greeting = data.greeting;
                if (data.match && data.greeting) checkedSet.add(data.job_id);
            }
            _clearHighlight();
        } else if (data.type === 'done') {
            _finishBatchAnalysis('分析完成');
            renderReview();
        } else if (data.type === 'stopped') {
            _finishBatchAnalysis('已停止');
            renderReview();
        } else if (data.type === 'error') {
            alert('分析失败: ' + (data.message || '未知错误'));
            _finishBatchAnalysis('分析出错');
        }
    };
    batchAnalysisEs.onerror = function() {
        if (batchAnalysisEs) { batchAnalysisEs.close(); batchAnalysisEs = null; }
        _clearHighlight();
        document.getElementById('btnAnalyzeAll').textContent = '🤖 全选分析';
        document.getElementById('btnAnalyzeAll').disabled = false;
        document.getElementById('btnPause').style.display = 'none';
        document.getElementById('btnStop').style.display = 'none';
    };
}

function _highlightJobRow(jobId) {
    if (highlightedRow) highlightedRow.classList.remove('analyzing');
    if (jobId) {
        highlightedRow = document.getElementById('job-row-' + jobId);
        if (highlightedRow) {
            highlightedRow.classList.add('analyzing');
            highlightedRow.scrollIntoView({behavior: 'smooth', block: 'center'});
        }
    }
}

function _clearHighlight() {
    if (highlightedRow) { highlightedRow.classList.remove('analyzing'); highlightedRow = null; }
}

function _finishBatchAnalysis(phaseText) {
    if (batchAnalysisEs) { batchAnalysisEs.close(); batchAnalysisEs = null; }
    _clearHighlight();
    document.getElementById('statusPhase').textContent = phaseText;
    document.getElementById('btnAnalyzeAll').textContent = '🤖 全选分析';
    document.getElementById('btnAnalyzeAll').disabled = false;
    document.getElementById('btnPause').style.display = 'none';
    document.getElementById('btnStop').style.display = 'none';
    const newAnalyzed = allJobs.filter(j => j.score > 0);
    if (newAnalyzed.length > 0) {
        document.getElementById('btnSaveScreened').style.display = 'inline-block';
    }
}

// ===== 暂停分析 =====
async function pauseAnalysis() {
    try { await fetch('/api/stop', {method: 'POST'}); } catch(e) {}
    if (batchAnalysisEs) { batchAnalysisEs.close(); batchAnalysisEs = null; }
    _clearHighlight();
    document.getElementById('statusPhase').textContent = '已暂停';
    document.getElementById('btnAnalyzeAll').textContent = '▶ 继续分析';
    document.getElementById('btnAnalyzeAll').disabled = false;
    document.getElementById('btnPause').style.display = 'none';
    document.getElementById('btnStop').style.display = 'none';
    const newAnalyzed = allJobs.filter(j => j.score > 0);
    if (newAnalyzed.length > 0) {
        document.getElementById('btnSaveScreened').style.display = 'inline-block';
    }
}

// ===== 保存为 screened 格式 =====
async function saveScreened() {
    const keyword = document.getElementById('cfgKeywords').value.trim() || '未设置';
    try {
        const resp = await fetch('/api/save-screened', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({keyword: keyword})
        });
        const data = await resp.json();
        if (data.ok) {
            alert('已保存: ' + data.path + '\n共 ' + data.total + ' 个岗位，匹配 ' + data.matched + ' 个');
            loadHistory(); // 刷新历史记录
        } else {
            alert('保存失败: ' + (data.error || '未知错误'));
        }
    } catch(e) {
        alert('保存失败: ' + e);
    }
}

function toggleJob(id, checked) {
    if (checked) checkedSet.add(id); else checkedSet.delete(id);
}

function toggleAll(el) {
    document.querySelectorAll('#matchedList input[type=checkbox]:not([disabled])').forEach(cb => {
        cb.checked = el.checked;
        if (el.checked) {
            const box = cb.closest('.job-item').querySelector('.greet-box');
            if (box && box.dataset.id) checkedSet.add(box.dataset.id);
        }
    });
    if (!el.checked) checkedSet.clear();
}

async function updateGreeting(id, text) {
    try {
        await fetch('/api/update-greeting', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id: id, greeting: text})
        });
    } catch(e) {}
}

let eventSource = null;

async function startDeliver() {
    if (checkedSet.size === 0) { alert('请至少勾选一个岗位'); return; }
    const greetings = {};
    document.querySelectorAll('.greet-box').forEach(t => { greetings[t.dataset.id] = t.value; });

    document.getElementById('btnDeliver').disabled = true;
    document.getElementById('btnStop').style.display = 'inline-block';
    document.getElementById('statusBar').style.display = 'flex';
    document.getElementById('statusPhase').textContent = '投递中...';
    document.getElementById('statusResult').textContent = '';

    eventSource = new EventSource('/api/deliver-stream');
    eventSource.onmessage = function(e) {
        const data = JSON.parse(e.data);
        if (data.type === 'progress') {
            document.getElementById('progressFill').style.width = (data.current / data.total * 100) + '%';
            document.getElementById('statusProgress').textContent = data.current + ' / ' + data.total;
            document.getElementById('statusPhase').textContent = data.label || '投递中...';
        } else if (data.type === 'result') {
            const s = document.getElementById('statusResult');
            if (data.success) s.innerHTML = '<span class="result-ok">' + data.name + ' ✓</span>';
            else s.innerHTML = '<span class="result-fail">' + data.name + ' ✗ ' + (data.error||'') + '</span>';
        } else if (data.type === 'done') {
            document.getElementById('statusPhase').textContent = '已完成';
            document.getElementById('btnDeliver').disabled = false;
            document.getElementById('btnStop').style.display = 'none';
            document.getElementById('statusResult').textContent = '成功 ' + (data.ok||0) + ' / 失败 ' + (data.fail||0);
            if (eventSource) { eventSource.close(); eventSource = null; }
        } else if (data.type === 'stopped') {
            document.getElementById('statusPhase').textContent = '已停止';
            document.getElementById('btnDeliver').disabled = false;
            document.getElementById('btnStop').style.display = 'none';
            if (eventSource) { eventSource.close(); eventSource = null; }
        } else if (data.type === 'error') {
            document.getElementById('statusPhase').textContent = '错误';
            document.getElementById('statusResult').textContent = data.message || '';
            document.getElementById('btnDeliver').disabled = false;
            document.getElementById('btnStop').style.display = 'none';
            if (eventSource) { eventSource.close(); eventSource = null; }
        }
    };
    eventSource.onerror = function() {
        if (eventSource) { eventSource.close(); eventSource = null; }
    };
}

async function stopDeliver() {
    try { await fetch('/api/stop', {method: 'POST'}); } catch(e) {}
    document.getElementById('btnDeliver').disabled = false;
    document.getElementById('btnStop').style.display = 'none';
    document.getElementById('btnPause').style.display = 'none';
    document.getElementById('statusPhase').textContent = '正在停止...';
    // 同时停止批量分析
    if (batchAnalysisEs) { batchAnalysisEs.close(); batchAnalysisEs = null; }
    _clearHighlight();
    document.getElementById('btnAnalyzeAll').textContent = '🤖 全选分析';
    document.getElementById('btnAnalyzeAll').disabled = false;
}

// ===== 历史记录 =====
async function loadHistory() {
    try {
        const resp = await fetch('/api/history');
        const files = await resp.json();
        const sel = document.getElementById('historySelect');
        sel.innerHTML = '<option value="">当前会话</option>';
        files.forEach(f => {
            const label = f.filename + ' [' + f.time + '] ' + f.total + '岗位' + (f.has_scores ? ' ' + f.matched + '匹配' : '');
            sel.innerHTML += '<option value="' + f.filename + '">' + label + '</option>';
        });
    } catch(e) { console.error(e); }
}

async function onHistoryChange() {
    const filename = document.getElementById('historySelect').value;
    // 统一通过 /api/load-history 处理（空 filename = 恢复当前会话）
    try {
        const resp = await fetch('/api/load-history', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({filename: filename})
        });
        const data = await resp.json();
        allJobs = data.jobs || [];
        jobMap = {};
        checkedSet.clear();
        allJobs.forEach(j => { jobMap[j.id] = j; if (j.match && j.greeting) checkedSet.add(j.id); });
        renderReview();
    } catch(e) { console.error(e); }
}

function refreshHistory() {
    loadHistory();
}

// 加载
loadConfig();
loadReviewData();
loadHistory();
setInterval(loadReviewData, 30000);
</script>
</body>
</html>
"""


class ReviewServer:
    """配置中心 + 审核页面"""

    def __init__(self, screened_jobs=None, cdp_port=9222, image_path=None,
                 port=5200, open_browser=True):
        if Flask is None:
            raise ImportError("需要 flask 依赖: pip install flask")

        self.screened_jobs = screened_jobs or []
        self._original_jobs = list(self.screened_jobs)  # 原始会话数据，用于"当前会话"恢复
        self.cdp_port = cdp_port
        self.image_path = image_path
        self.port = port
        self.open_browser = open_browser
        self._config = load_config()

        self._lock = threading.Lock()
        self.deliver_phase = "idle"
        self.deliver_progress = {"current": 0, "total": 0}
        self.deliver_results = []
        self.stop_flag = False
        self._app = None

        self._job_by_id = {}
        for j in self.screened_jobs:
            jid = j.get("id") or j.get("job_id", "")
            if jid:
                self._job_by_id[jid] = j

    def _create_app(self):
        app = Flask(__name__)
        server = self

        @app.route("/")
        def index():
            return render_template_string(HTML_TEMPLATE)

        # ---- 配置 API ----
        @app.route("/api/config")
        def api_get_config():
            return jsonify(server._config)

        @app.route("/api/config", methods=["POST"])
        def api_save_config():
            data = request.get_json(force=True)
            with server._lock:
                for key in data:
                    if key in DEFAULT_CONFIG:
                        server._config[key] = data[key]
                save_config(server._config)
            return jsonify({"ok": True})

        # ---- 模型列表 API ----
        @app.route("/api/fetch-models", methods=["POST"])
        def api_fetch_models():
            """代理请求到 AI 提供商的 /v1/models，避免前端跨域问题"""
            import requests as _requests
            data = request.get_json(force=True)
            provider = data.get("provider", "deepseek")
            api_key = data.get("key", "")
            base_url = data.get("base_url", "").rstrip("/")
            if not api_key:
                return jsonify({"models": []})

            try:
                if provider == "claude":
                    resp = _requests.get(
                        f"{base_url}/v1/models",
                        headers={
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        timeout=15,
                    )
                else:
                    resp = _requests.get(
                        f"{base_url}/v1/models",
                        headers={"Authorization": f"Bearer {api_key}"},
                        timeout=15,
                    )
                if not resp.ok:
                    return jsonify({"models": []})

                result = resp.json()
                models = [m.get("id", "") for m in (result.get("data", []) or []) if m.get("id")]
                return jsonify({"models": models})
            except Exception as e:
                log.warning("fetch-models 失败: %s", e)
                return jsonify({"models": []})

        # ---- 历史记录 API ----
        @app.route("/api/history")
        def api_history():
            """列出 job-result/ 下所有 JSON 文件"""
            os.makedirs(RESULT_DIR, exist_ok=True)
            files = []
            try:
                for fname in os.listdir(RESULT_DIR):
                    if not fname.endswith(".json"):
                        continue
                    # 显示岗位列表/筛选结果/详情文件
                    if not (fname.startswith("boss_screened_") or fname.startswith("boss_jobs_") or fname.startswith("boss_details_")):
                        continue
                    full = os.path.join(RESULT_DIR, fname)
                    if not os.path.isfile(full):
                        continue
                    stat = os.stat(full)
                    total = 0
                    matched = 0
                    has_scores = False
                    try:
                        with open(full, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        jobs = data if isinstance(data, list) else data.get("jobs", [])
                        total = len(jobs)
                        has_scores = any("score" in j for j in jobs[:5] if isinstance(j, dict))
                        matched = sum(1 for j in jobs if isinstance(j, dict) and j.get("match"))
                    except Exception:
                        pass
                    files.append({
                        "filename": fname,
                        "path": full,
                        "time": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "total": total,
                        "matched": matched,
                        "has_scores": has_scores,
                    })
                files.sort(key=lambda x: x["path"], reverse=True)
            except OSError:
                pass
            return jsonify(files)

        @app.route("/api/load-history", methods=["POST"])
        def api_load_history():
            """加载指定的历史文件；filename 为空时恢复当前会话"""
            data = request.get_json(force=True)
            filename = (data.get("filename", "") or "").strip()
            # 空 filename = 恢复当前会话原始数据
            if not filename:
                with server._lock:
                    server.screened_jobs = list(server._original_jobs)
                    server._job_by_id = {}
                    for j in server.screened_jobs:
                        jid = j.get("id") or j.get("job_id", "")
                        if jid:
                            server._job_by_id[jid] = j
                log.info("已恢复到当前会话 (%d 岗位)", len(server.screened_jobs))
                return jsonify({"jobs": server.screened_jobs})
            # 安全校验
            if ".." in filename or "/" in filename or "\\" in filename:
                return jsonify({"error": "无效文件名", "jobs": []}), 400
            full = os.path.join(RESULT_DIR, filename)
            if not os.path.isfile(full):
                return jsonify({"error": "文件不存在", "jobs": []}), 404
            try:
                with open(full, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                jobs = loaded if isinstance(loaded, list) else loaded.get("jobs", [])
                # 对所有文件统一补全 id/name 字段（screened/jobs 文件可能只有 job_id/title）
                for j in jobs:
                    j.setdefault("id", j.get("job_id", ""))
                    j.setdefault("name", j.get("title", ""))
                # 详情文件额外补充默认字段 + 模式标记
                is_detail = filename.startswith("boss_details_")
                if is_detail:
                    for j in jobs:
                        j.setdefault("match", False)
                        j.setdefault("score", 0)
                        j.setdefault("reason", "")
                        j.setdefault("greeting", "")
                        j["is_detail_mode"] = True
                else:
                    # 非详情文件：清除可能残留的 is_detail_mode 标记（从保存的 screened 文件加载时）
                    for j in jobs:
                        j.pop("is_detail_mode", None)
                with server._lock:
                    server.screened_jobs = jobs
                    server._job_by_id = {}
                    for j in jobs:
                        jid = j.get("id") or j.get("job_id", "")
                        if jid:
                            server._job_by_id[jid] = j
                log.info("加载历史文件: %s (%d 岗位)", filename, len(jobs))
                return jsonify({"jobs": jobs})
            except (json.JSONDecodeError, OSError, ValueError) as e:
                log.warning("加载历史文件失败: %s", e)
                return jsonify({"error": str(e), "jobs": []}), 500

        # ---- 审核数据 API ----
        @app.route("/api/data")
        def api_data():
            return jsonify({"jobs": server.screened_jobs})

        @app.route("/api/update-greeting", methods=["POST"])
        def api_update_greeting():
            data = request.get_json(force=True)
            jid = data.get("id", "")
            greeting = data.get("greeting", "")
            with server._lock:
                job = server._job_by_id.get(jid)
                if job:
                    job["greeting"] = greeting
            return jsonify({"ok": True})

        # ---- 投递 SSE ----
        @app.route("/api/deliver-stream")
        def api_deliver_stream():
            server.stop_flag = False
            server.deliver_results = []
            server.deliver_phase = "delivering"

            def generate():
                with server._lock:
                    jobs_to_deliver = [
                        j for j in server.screened_jobs
                        if j.get("match") and j.get("greeting", "").strip()
                    ]

                if not jobs_to_deliver:
                    yield f"data: {json.dumps({'type': 'error', 'message': '没有可投递的岗位（均无招呼语）'})}\n\n"
                    return

                server.deliver_progress = {"current": 0, "total": len(jobs_to_deliver)}

                # 读取持久化图片
                image_b64 = server._config.get("resume_image", "")
                if image_b64:
                    # 临时写图片文件供 send_to_job 使用
                    import base64
                    import tempfile
                    try:
                        _, data = image_b64.split(",", 1)
                        tmp_img = os.path.join(CONFIG_DIR, "resume.png")
                        with open(tmp_img, "wb") as f:
                            f.write(base64.b64decode(data))
                        server.image_path = tmp_img
                    except Exception:
                        pass

                try:
                    from scripts.boss_auto_sender import send_to_job
                    from scripts.boss_cdp_raw import CDPSession

                    cdp_port = server._config.get("cdp_port", 9222)
                    cdp = CDPSession(cdp_port)

                    for i, job in enumerate(jobs_to_deliver):
                        if server.stop_flag:
                            yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                            break

                        jid = job.get("id") or job.get("job_id", "")
                        greeting = job.get("greeting", "").strip()
                        if not greeting:
                            continue

                        server.deliver_progress = {"current": i + 1, "total": len(jobs_to_deliver)}
                        yield f"data: {json.dumps({'type': 'progress', 'current': i + 1, 'total': len(jobs_to_deliver), 'label': job.get('name', job.get('title', ''))})}\n\n"

                        try:
                            r = send_to_job(cdp, job, greeting, server.image_path)
                            result = {
                                "job_id": jid,
                                "name": job.get("name", job.get("title", "")),
                                "success": r.get("success", False),
                                "error": r.get("error"),
                            }
                        except Exception as e:
                            result = {
                                "job_id": jid,
                                "name": job.get("name", job.get("title", "")),
                                "success": False,
                                "error": str(e),
                            }

                        server.deliver_results.append(result)
                        yield f"data: {json.dumps({'type': 'result', 'name': result['name'], 'success': result['success'], 'error': result.get('error')})}\n\n"

                        if i < len(jobs_to_deliver) - 1 and not server.stop_flag:
                            import random
                            time.sleep(random.uniform(3, 8))

                    cdp.close()

                except Exception as e:
                    log.error("投递异常: %s", e)
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

                finally:
                    ok_count = sum(1 for r in server.deliver_results if r.get("success"))
                    fail_count = len(server.deliver_results) - ok_count
                    server.deliver_phase = "done"
                    yield f"data: {json.dumps({'type': 'done', 'ok': ok_count, 'fail': fail_count})}\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        @app.route("/api/stop", methods=["POST"])
        def api_stop():
            server.stop_flag = True
            return jsonify({"ok": True})

        # ---- 逐条 AI 分析 SSE ----
        @app.route("/api/analyze-job-stream")
        def api_analyze_job_stream():
            """对单个岗位进行 AI 评分 + 招呼语生成，SSE 实时推送"""
            job_id = request.args.get("job_id", "").strip()
            if not job_id:
                def _err_gen():
                    yield f"data: {json.dumps({'type': 'error', 'message': '缺少 job_id'})}\n\n"
                return Response(_err_gen(), mimetype="text/event-stream",
                                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

            job = server._job_by_id.get(job_id)
            if not job:
                def _err_gen():
                    yield f"data: {json.dumps({'type': 'error', 'message': '岗位不存在'})}\n\n"
                return Response(_err_gen(), mimetype="text/event-stream",
                                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

            # 读取 AI 配置
            cfg = server._config
            provider = cfg.get("provider", "deepseek")
            if provider == "claude":
                api_key = cfg.get("claude_key", "") or os.environ.get("CLAUDE_API_KEY", "")
                model = cfg.get("claude_model", "")
                base_url = cfg.get("claude_base_url", "https://api.anthropic.com")
            else:
                api_key = cfg.get("ds_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
                model = cfg.get("deepseek_model", "")
                base_url = cfg.get("deepseek_base_url", "https://api.deepseek.com")

            resume_text = cfg.get("resume_text", "").strip()

            def generate():
                if not api_key:
                    yield f"data: {json.dumps({'type': 'error', 'message': '请先在 ① 配置 中填写 API Key'})}\n\n"
                    return
                if not resume_text:
                    yield f"data: {json.dumps({'type': 'error', 'message': '请先在 ① 配置 中填写个人简历'})}\n\n"
                    return

                job_name = job.get("name") or job.get("title", "")
                yield f"data: {json.dumps({'type': 'progress', 'label': f'正在分析 {job_name}...'})}\n\n"

                try:
                    from scripts.ai_screener import AIScreener

                    screener = AIScreener(
                        provider=provider,
                        api_key=api_key,
                        model=model or "",
                        base_url=base_url or "",
                        score_threshold=int(cfg.get("score_threshold", 60)),
                    )

                    keywords = cfg.get("keywords", "")
                    expect_salary = cfg.get("expect_salary", "")
                    jd = job.get("jd", "")
                    custom_prompt = cfg.get("custom_prompt", "")
                    extra_prompt = cfg.get("extra_prompt", "")

                    result = screener.screen_job(
                        job,
                        resume_text=resume_text,
                        keywords=keywords,
                        salary_range=expect_salary,
                        jd=jd,
                        custom_prompt=custom_prompt,
                        extra_prompt=extra_prompt,
                    )

                    # 写入内存
                    with server._lock:
                        job["score"] = result["score"]
                        job["match"] = result["match"]
                        job["reason"] = result["reason"]
                        job["greeting"] = result["greeting"]

                    yield f"data: {json.dumps({'type': 'result', 'job_id': job_id, 'score': result['score'], 'match': result['match'], 'reason': result['reason'], 'greeting': result['greeting']})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'ok': 1, 'fail': 0})}\n\n"

                except Exception as e:
                    log.error("AI 分析异常: %s", e)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'分析失败: {e}'})}\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        # ---- 批量 AI 分析 SSE ----
        @app.route("/api/analyze-all-stream")
        def api_analyze_all_stream():
            """批量分析所有未分析岗位，SSE 实时推送进度"""
            server.stop_flag = False

            def generate():
                # 筛选未分析岗位
                with server._lock:
                    unanalyzed = [j for j in server.screened_jobs if j.get("score", 0) == 0]
                if not unanalyzed:
                    yield f"data: {json.dumps({'type': 'error', 'message': '所有岗位已分析完毕'})}\n\n"
                    return

                # 读取 AI 配置
                cfg = server._config
                provider = cfg.get("provider", "deepseek")
                if provider == "claude":
                    api_key = cfg.get("claude_key", "") or os.environ.get("CLAUDE_API_KEY", "")
                    model = cfg.get("claude_model", "")
                    base_url = cfg.get("claude_base_url", "https://api.anthropic.com")
                else:
                    api_key = cfg.get("ds_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
                    model = cfg.get("deepseek_model", "")
                    base_url = cfg.get("deepseek_base_url", "https://api.deepseek.com")

                resume_text = cfg.get("resume_text", "").strip()

                if not api_key:
                    yield f"data: {json.dumps({'type': 'error', 'message': '请先在 ① 配置 中填写 API Key'})}\n\n"
                    return
                if not resume_text:
                    yield f"data: {json.dumps({'type': 'error', 'message': '请先在 ① 配置 中填写个人简历'})}\n\n"
                    return

                keywords = cfg.get("keywords", "")
                expect_salary = cfg.get("expect_salary", "")
                custom_prompt = cfg.get("custom_prompt", "")
                extra_prompt = cfg.get("extra_prompt", "")
                score_threshold = int(cfg.get("score_threshold", 60))

                try:
                    from scripts.ai_screener import AIScreener
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    ok_count = 0
                    fail_count = 0
                    total = len(unanalyzed)
                    concurrency = max(1, int(cfg.get("ai_concurrency", 3) or 3))

                    def _analyze_one(job):
                        """线程任务：分析单个岗位，返回 (job, result_dict)"""
                        job_id = job.get("id") or job.get("job_id", "")
                        jd = job.get("jd", "")
                        # 每个线程创建独立的 screener 实例
                        _screener = AIScreener(
                            provider=provider,
                            api_key=api_key,
                            model=model or "",
                            base_url=base_url or "",
                            score_threshold=score_threshold,
                        )
                        try:
                            result = _screener.screen_job(
                                job,
                                resume_text=resume_text,
                                keywords=keywords,
                                salary_range=expect_salary,
                                jd=jd,
                                custom_prompt=custom_prompt,
                                extra_prompt=extra_prompt,
                            )
                            return job, result, True
                        except Exception as e:
                            return job, {
                                "score": 0, "match": False,
                                "reason": f"分析异常: {e}", "greeting": "",
                            }, False

                    yield f"data: {json.dumps({'type': 'progress', 'current': 0, 'total': total, 'label': f'并发 {concurrency} 线程分析中...', 'job_id': ''})}\n\n"

                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futures = {executor.submit(_analyze_one, job): job for job in unanalyzed}
                        for future in as_completed(futures):
                            if server.stop_flag:
                                break
                            try:
                                job, result, success = future.result()
                            except Exception as e:
                                log.error("线程异常: %s", e)
                                fail_count += 1
                                continue

                            job_id = job.get("id") or job.get("job_id", "")
                            job_name = job.get("name") or job.get("title", "")
                            with server._lock:
                                job["score"] = result["score"]
                                job["match"] = result["match"]
                                job["reason"] = result["reason"]
                                job["greeting"] = result["greeting"]

                            if success:
                                ok_count += 1
                            else:
                                fail_count += 1

                            yield f"data: {json.dumps({'type': 'progress', 'current': ok_count + fail_count, 'total': total, 'label': job_name[:20], 'job_id': job_id})}\n\n"
                            yield f"data: {json.dumps({'type': 'result', 'job_id': job_id, 'score': result['score'], 'match': result['match'], 'reason': result['reason'], 'greeting': result['greeting']})}\n\n"

                    if server.stop_flag:
                        yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'done', 'ok': ok_count, 'fail': fail_count})}\n\n"

                except Exception as e:
                    log.error("批量分析异常: %s", e)
                    yield f"data: {json.dumps({'type': 'error', 'message': f'批量分析失败: {e}'})}\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        # ---- 保存为 screened 格式 ----
        @app.route("/api/save-screened", methods=["POST"])
        def api_save_screened():
            """将当前分析结果保存为 boss_screened_*.json"""
            data = request.get_json(force=True)
            keyword = (data.get("keyword", "") or server._config.get("keywords", "") or "").strip()

            os.makedirs(RESULT_DIR, exist_ok=True)
            filename = f"boss_screened_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            full = os.path.join(RESULT_DIR, filename)

            with server._lock:
                jobs = list(server.screened_jobs)
                total = len(jobs)
                matched = sum(1 for j in jobs if j.get("match"))

            screened_data = {
                "keyword": keyword,
                "score_threshold": server._config.get("score_threshold", 60),
                "total": total,
                "matched": matched,
                "screened_at": datetime.now().isoformat(),
                "jobs": jobs,
            }

            try:
                with open(full, "w", encoding="utf-8") as f:
                    json.dump(screened_data, f, ensure_ascii=False, indent=2)
                log.info("已保存筛选结果: %s (%d 岗位, %d 匹配)", filename, total, matched)
                return jsonify({"ok": True, "path": full, "filename": filename, "total": total, "matched": matched})
            except OSError as e:
                log.warning("保存筛选结果失败: %s", e)
                return jsonify({"ok": False, "error": str(e)}), 500

        return app

    def start(self):
        self._app = self._create_app()
        if self.open_browser:
            webbrowser.open(f"http://127.0.0.1:{self.port}")
        log.info("控制台已启动: http://127.0.0.1:%d", self.port)
        print(f"\nBOSS控制台: http://127.0.0.1:{self.port}")
        print("① 配置 → 填写 API Key / 简历 / 偏好 → 保存")
        print("② 审核 → 有筛选数据后可编辑招呼语并投递")
        print("按 Ctrl+C 停止\n")
        self._app.run(host="127.0.0.1", port=self.port, debug=False, use_reloader=False)

    def start_in_thread(self):
        self._app = self._create_app()

        def _run():
            self._app.run(host="127.0.0.1", port=self.port, debug=False, use_reloader=False)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        if self.open_browser:
            time.sleep(0.5)
            webbrowser.open(f"http://127.0.0.1:{self.port}")
        log.info("控制台已启动（后台）: http://127.0.0.1:%d", self.port)
        print(f"\nBOSS控制台: http://127.0.0.1:{self.port}")
        return t


# ============================================================
# 持久化配置读取工具（供 CLI 使用）
# ============================================================

def get_saved_config():
    """获取持久化配置字典"""
    return load_config()


def get_saved_ai_api_key(provider="deepseek"):
    """从持久化配置获取 AI API Key"""
    cfg = load_config()
    if provider == "claude":
        return cfg.get("claude_key", "") or os.environ.get("CLAUDE_API_KEY", "")
    return cfg.get("ds_key", "") or os.environ.get("DEEPSEEK_API_KEY", "")


# ============================================================
# CLI — 无 --input 也可打开
# ============================================================

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="BOSS直聘配置中心 + 审核页面")
    p.add_argument("--input", "-i", default=None,
                   help="AI 筛选结果 JSON 文件路径（可选，无则只显示配置页）")
    p.add_argument("--port", "-p", type=int, default=5200, help="服务端口（默认5200）")
    p.add_argument("--cdp-port", type=int, default=9222, help="Chrome CDP 端口")
    p.add_argument("--resume-image", default=None, help="简历图片路径")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    screened = []
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "jobs" in data:
            screened = data["jobs"]
        elif isinstance(data, list):
            screened = data
        else:
            print("警告: JSON 格式不识别，将以无数据模式启动")
        if screened:
            matched = [j for j in screened if j.get("match")]
            print(f"加载 {len(screened)} 个岗位，匹配 {len(matched)} 个")

    server = ReviewServer(
        screened, cdp_port=args.cdp_port,
        image_path=args.resume_image,
        port=args.port, open_browser=not args.no_browser,
    )
    server.start()
