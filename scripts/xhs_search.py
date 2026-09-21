#!/usr/bin/env python3
"""
xhs_search.py — 小红书搜索 CLI（Python 重构版）

用法:
  python xhs_search.py --q "机器学习" --q "深度学习" --rows 25 \
      --sort 最多点赞 --type 图文 --time 一周内

输出: stdout JSON  { filters, count, results: [{keyword, total, items}] }
进度日志 → stderr（不污染 stdout）
"""
import argparse
import json
import sys
import time
from urllib.parse import quote

from cdp_base import (close_page, create_page, ensure_cdp, human_scroll,  # noqa: E402
                      setup_stdout, write_log)
from config import get
from xhs_parser import build_explore_url, dedupe_notes, tidy_note

MAX_ROWS = 100
FILTER_CHOICES = {
    "sort": ["综合", "最新", "最多点赞", "最多评论", "最多收藏"],
    "type": ["不限", "视频", "图文"],
    "time": ["不限", "一天内", "一周内", "半年内"],
    "scope": ["不限", "已看过", "未看过", "已关注"],
    "distance": ["不限", "同城", "附近"],
}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="小红书搜索")
    ap.add_argument("--q", action="append", required=True, help="搜索关键词（可重复）")
    ap.add_argument("--rows", type=int, default=25, help=f"每关键词最大结果数（默认 25，最大 {MAX_ROWS}）")
    ap.add_argument("--parallel", type=int, default=None, help="并行任务数（默认 2，最大 8）")
    for name, choices in FILTER_CHOICES.items():
        ap.add_argument(f"--{name}", default="综合" if name == "sort" else "不限", choices=choices)
    args = ap.parse_args(argv)
    args.rows = max(1, min(args.rows, MAX_ROWS))
    args.parallel = args.parallel if args.parallel is not None else get("parallel.limit")
    args.parallel = max(1, min(args.parallel, 8))
    return args


# ── 提取 JS（在浏览器内执行，返回原始 dict 列表）──────────────────
EXTRACT_JS = """() => Array.from(document.querySelectorAll('section.note-item'))
  .slice(0, %d)
  .map(card => {
    const noteId = card.getAttribute('data-note-id') || '';
    let title = (card.querySelector('a.title span') || {}).textContent
      || (card.querySelector('a.title') || {}).textContent || '';
    title = title.trim();
    const rawHref = (card.querySelector('a.cover') || {}).getAttribute
      ? card.querySelector('a.cover').getAttribute('href') : '';
    const author = (card.querySelector('.author .name') || {}).textContent || '';
    const time = (card.querySelector('.time') || {}).textContent || '';
    const likes = (card.querySelector('.like-wrapper .count') || {}).textContent || '0';
    const isVideo = Array.from(card.querySelectorAll('svg use'))
      .some(u => (u.getAttribute('xlink:href') || '') === '#play-s');
    return {noteId, title, link: rawHref, author: author.trim(), time: time.trim(),
            likes: likes.trim(), type: isVideo ? 'video' : 'normal'};
  })"""

# ── 筛选点击 JS（返回应用了哪些标签）───────────────────────────────
APPLY_FILTERS_JS = """(wanted) => new Promise((resolve) => {
  const applied = [];
  const openPanel = () => {
    const fw = document.querySelector('.filters-wrapper');
    return !!fw && fw.offsetParent !== null;
  };
  const waitOpen = async () => {
    for (let a = 0; a < 4; a++) {
      if (openPanel()) return true;
      const f = document.querySelector('div.filter');
      if (f) f.click();
      for (let i = 0; i < 15; i++) {
        await new Promise(r => setTimeout(r, 200));
        if (openPanel()) return true;
      }
    }
    return false;
  };
  const clickTag = (label) => {
    const tags = Array.from(document.querySelectorAll('.filters-wrapper .tags'))
      .filter(el => !el.hasAttribute('button-hp-installed'));
    const t = tags.find(x => (x.textContent || '').trim() === label);
    if (!t) return 'notfound';
    if (t.className.includes('active')) return 'already';
    t.click();
    return 'clicked';
  };
  (async () => {
    if (!wanted.length) { resolve([]); return; }
    if (!(await waitOpen())) { resolve(['panel-failed']); return; }
    for (const label of wanted) {
      const r = clickTag(label);
      applied.push(label + ':' + r);
      if (r === 'clicked') await new Promise(r => setTimeout(r, 3000));
    }
    const fw = document.querySelector('.filters-wrapper');
    if (fw && fw.offsetParent !== null) {
      const f = document.querySelector('div.filter');
      if (f) f.click();
    }
    await new Promise(r => setTimeout(r, 600));
    resolve(applied);
  })();
})"""


def detect_state(client):
    """Promise.race 等价：轮询判断页面状态 normal / noresult / login / timeout。

    页面跳转或连接中断时 get_body_text/evaluate 会抛 CdpError，
    视为本轮未就绪并重试；轮询耗尽按 timeout 处理。
    """
    for _ in range(int(15 / 0.5)):
        try:
            body = client.get_body_text()
        except Exception:
            time.sleep(0.5)
            continue
        head = body[:500]
        if "暂无相关内容" in head or "没有找到相关" in head or "未找到" in head:
            return "noresult"
        if "登录后查看" in head or "手机号登录" in head:
            return "login"
        if "操作频繁" in head or "安全验证" in head or "验证码" in head:
            return "risk"
        if client.evaluate("location.pathname === '/explore'"):
            return "redirect"
        try:
            if client.evaluate("!!document.querySelector('section.note-item')"):
                return "normal"
        except Exception:
            pass  # 连接中断/页面跳转中：本轮未就绪，继续轮询
        time.sleep(0.5)
    return "timeout"


def search_one(client, kw, args):
    url = (f"https://www.xiaohongshu.com/search_result_ai?keyword="
           f"{quote(kw)}&source=web_explore_feed")
    client.navigate(url)
    time.sleep(2)  # 跳转缓冲：避免 detect_state 读到上一个页面的旧 pathname（如 /explore 首页）误判 redirect
    state = detect_state(client)
    if state == "noresult":
        return {"keyword": kw, "total": 0, "items": [], "notice": "无搜索结果"}
    if state == "login":
        return {"keyword": kw, "total": 0, "items": [], "error": "需要登录"}
    if state == "risk":
        return {"keyword": kw, "total": 0, "items": [], "error": "触发风控/验证，请稍后再试"}
    if state == "redirect":
        return {"keyword": kw, "total": 0, "items": [], "error": "无效 URL（重定向到首页）"}
    if state == "timeout":
        return {"keyword": kw, "total": 0, "items": [], "error": "页面加载超时"}

    time.sleep(2)  # 渲染稳定

    # 筛选
    wanted = [v for v in (args.sort, args.type, args.time, args.scope, args.distance)
              if v not in ("不限", "综合")]
    applied = []
    if wanted:
        try:
            applied = client.evaluate(f"({APPLY_FILTERS_JS})({json.dumps(wanted)})",
                                      await_promise=True) or []
        except Exception as e:
            applied = [f"error:{str(e)[:50]}"]

    # 懒加载滚动
    for _ in range(6):
        human_scroll(client, max_rounds=2)
        count = client.evaluate("document.querySelectorAll('section.note-item').length")
        if count >= args.rows + 5 or count >= 100:
            break

    raw = client.evaluate(f"({EXTRACT_JS % args.rows})()") or []
    tidy_items = []
    for r in raw:
        n = tidy_note(r)
        if not n:
            continue
        url = build_explore_url(n["link"])
        if not url:  # 无 xsec_token 的链接访问会 404，过滤
            continue
        n["link"] = url
        tidy_items.append(n)
    items = dedupe_notes(tidy_items)
    return {"keyword": kw, "total": len(items), "items": items,
            "applied_filters": applied}


def _search_in_tab(port, kw, args):
    """在独立 tab 中搜索单个关键词，返回 (kw, result_or_error)。"""
    client = None
    try:
        client = create_page(port)
        return kw, search_one(client, kw, args)
    except Exception as e:
        return kw, e
    finally:
        close_page(client)


def main():
    from concurrent.futures import ThreadPoolExecutor
    args = parse_args()
    port = ensure_cdp()
    limit = min(len(args.q), args.parallel)
    results = []
    with ThreadPoolExecutor(max_workers=limit) as ex:
        for kw, r in ex.map(_search_in_tab, [port] * len(args.q), args.q, [args] * len(args.q)):
            if isinstance(r, Exception):
                results.append({"keyword": kw, "total": 0, "items": [], "error": str(r)[:200]})
                sys.stderr.write(f"[xhs-search] {kw} 失败: {str(r)[:120]}\n")
            else:
                results.append(r)
                sys.stderr.write(f"[xhs-search] {kw}: {r.get('total', 0)} 条\n")
            sys.stderr.flush()
    out = {
        "filters": {k: getattr(args, k) for k in FILTER_CHOICES},
        "count": len(results),
        "results": results,
    }
    # 先落盘再写 stdout：宿主对 stdout 有大小上限，Agent 拿全文直接读 logPath
    try:
        out["logPath"] = write_log(out, "xhs_search")
        sys.stderr.write(f"[xhs-search] 完整结果已落盘: {out['logPath']}\n")
    except Exception as e:
        sys.stderr.write(f"[xhs-search] 落盘失败: {str(e)[:80]}\n")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    setup_stdout()
    main()
