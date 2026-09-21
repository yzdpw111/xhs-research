#!/usr/bin/env python3
"""
xhs_detail.py — 小红书笔记详情 CLI

用法:
  python xhs_detail.py --url "https://www.xiaohongshu.com/explore/xxx?xsec_token=..."
  python xhs_detail.py --url "https://www.xiaohongshu.com/explore/xxx?xsec_token=..." --max-comments 15 --no-ocr
  node 管道: python xhs_search.py --q 机器学习 --rows 5 | python xhs_detail.py --max-comments 10

输出: stdout JSON { count, succeeded, failed, notes }
媒体落盘：默认不持久（图片临时目录 OCR 后删、视频只记 videoUrl）；
  --media-dir <dir> 时图片/视频保存到 <dir>/<noteId>_<标题>/

注意:
  - 评论懒加载要滚动弹窗内**可滚动**的容器，不是 window：真正能滚的是 .note-scroller
    （#noteContainer 的 overflowY 是 visible、scrollHeight == clientHeight，滚它纹丝不动
    → 评论恒停在第 10 条）；容器内 scrollBy 1500px + 等 800ms 每轮加载 ~10 条
"""
import argparse
import json
import os
import re
import sys
import time
import shutil
import tempfile

from cdp_base import (close_page, create_page, ensure_cdp, setup_stdout,  # noqa: E402
                      write_log)
from config import get
from ocr_images import ocr_directory
from xhs_parser import clean_count, tidy_comment

MAX_COMMENTS = 80
NOTE_ID_RE = re.compile(r"/explore/([0-9a-f]+)")
_ILLEGAL_FS_CHARS = re.compile(r'[\\/:*?"<>|]')


def media_dir_name(note_id, title, max_title_len=24):
    """生成媒体子目录名 `{noteId}_{清洗后标题}`。

    Windows 非法字符 `\\ / : * ? " < > |` 替换为空格，连续空格合并，
    标题截断到 max_title_len（去尾部空格）；清洗后为空则回退为纯 noteId。
    """
    cleaned = _ILLEGAL_FS_CHARS.sub(" ", title or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned[:max_title_len].rstrip()
    return f"{note_id}_{cleaned}" if cleaned else note_id


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="小红书笔记详情")
    ap.add_argument("--url", action="append", help="笔记 URL（可重复）")
    ap.add_argument("--max-comments", type=int, default=30, help=f"每篇最大顶级评论数（默认 30，最大 {MAX_COMMENTS}）")
    ap.add_argument("--no-ocr", action="store_true", help="跳过 OCR")
    ap.add_argument("--reply-limit", type=int, default=2, help="每条评论最多回复数（默认 2，>1 时点「展开」抓取）")
    ap.add_argument("--media-dir", default=None, help="媒体（图片+视频）保存目录；不指定则图片临时 OCR 后删、视频不下载")
    ap.add_argument("--parallel", type=int, default=None, help="并行任务数（默认 2，最大 8）")
    args = ap.parse_args(argv)
    args.max_comments = max(1, min(args.max_comments, MAX_COMMENTS))
    args.reply_limit = max(1, args.reply_limit)  # 至少 1（1=不展开），负数钳到 1
    args.parallel = args.parallel if args.parallel is not None else get("parallel.limit")
    args.parallel = max(1, min(args.parallel, 8))
    # 无 --url 且 stdin 不是管道（tty）时直接报错退出，否则可能阻塞等待 stdin
    if not args.url and sys.stdin.isatty():
        ap.error("需要 --url 或 stdin 管道输入")
    return args


def parse_pipe_input(data):
    """解析 xhs_search.py 的管道输出，返回去重后的 URL 列表。

    非法 JSON 或结构不符时返回 None（由 read_targets 转成友好报错）。
    """
    try:
        # PowerShell 管道会给 stdin 注入 UTF-8 BOM（\ufeff），需先剔除
        parsed = json.loads(data.lstrip("\ufeff").strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    links = set()
    for g in parsed.get("results", []):
        if not isinstance(g, dict):
            continue
        items = g.get("items") or ([g] if g.get("link") else [])
        for r in items:
            if isinstance(r, dict) and r.get("link"):
                links.add(r["link"])
    return list(links)


def read_targets(args):
    if args.url:
        return args.url
    if sys.stdin.isatty():
        sys.stderr.write("需要 --url 或 stdin 管道输入\n")
        sys.exit(1)
    data = sys.stdin.read()
    links = parse_pipe_input(data)
    if links is None:
        sys.stderr.write("stdin 输入不是合法的 JSON（预期 xhs_search.py 管道输出）\n")
        sys.exit(1)
    return links


# ── 提取 JS ──────────────────────────────────────────
DETAIL_BASE_JS = r"""() => {
  const q = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; };
  const isVideo = !!document.querySelector('#noteContainer video, .note-detail-mask video, .video-player-media');
  const imgUrls = [...new Set(
    Array.from(document.querySelectorAll('#noteContainer .swiper-slide img'))
      .map(i => i.src).filter(s => s && s.includes('xhscdn.com')))];
  const videoUrl = (document.querySelector('meta[property="og:video"], meta[name="og:video"]') || {}).content || null;
  return {
    type: isVideo ? 'video' : 'normal',
    title: q('#detail-title'), desc: q('#detail-desc'),
    author: q('.author-container .username'), date: q('.bottom-container .date'),
    likes: q('.engage-bar .like-wrapper .count'),
    collects: q('.engage-bar .collect-wrapper .count'),
    commentCount: (() => {
      // 互动栏 .chat-wrapper .count 渲染较晚（约 5s），提取 base 数据时可能还未渲染
      // 回退到评论区 .total（"共 N 条评论"），其渲染更早（约 3s）
      const cc = q('.engage-bar .chat-wrapper .count');
      if (/^[\d.]+[万wk]?$/i.test(cc)) return cc;
      const total = q('.comments-container .total');
      const m = total.match(/([\d.]+[万wk]?)/);
      return m ? m[1] : '0';
    })(),
    videoUrl,
    imgUrls,
  };
}"""

COMMENTS_JS = """(rl, max) => Array.from(document.querySelectorAll('.comments-container .parent-comment'))
  .slice(0, max)
  .map(pc => {
    const rootEl = pc.querySelector('.comment-item:not(.comment-item-sub)');
    if (!rootEl) return null;
    const pick = item => ({
      author: (item.querySelector('.author .name') || {}).textContent || '',
      content: (item.querySelector('.content .note-text') || {}).textContent || '',
      date: ((item.querySelector('.info .date span') || {}).textContent || '').trim(),
      location: (item.querySelector('.info .date .location') || {}).textContent || '',
      likes: (item.querySelector('.like .count') || {}).textContent || '0',
    });
    const root = pick(rootEl);
    const subs = Array.from(pc.querySelectorAll('.reply-container .comment-item-sub'));
    root.replies = subs.slice(0, rl).map(pick);
    return root;
  }).filter(Boolean)"""

EXPAND_REPLIES_JS = """() => {
  let clicked = 0;
  document.querySelectorAll('.comments-container .parent-comment').forEach(pc => {
    const sm = pc.querySelector('.reply-container .show-more');
    if (sm && sm.offsetParent !== null && !sm.dataset.expanded) {
      sm.dataset.expanded = '1';
      sm.click();
      clicked++;
    }
  });
  return clicked;
}"""

# 评论懒加载必须滚动弹窗内可滚动的容器，window 滚动无效。
# 实测（2026-09）：能滚的是 .note-scroller（overflowY: scroll，sh 2539 > ch 721）；
# 而 #noteContainer 的 overflowY 是 visible、scrollHeight == clientHeight，
# 对它 scrollBy 完全无效（scrollTop 恒为 0）→ 评论恒停在第 10 条。
# 页面上可能有多个 .note-scroller（feed 卡片里也有），只挑可见且真能滚的那个。
# 参照 dom-notes：scrollBy 1500px + 等 800ms，每轮 ~10 条。
COMMENT_SCROLL_JS = """(rounds) => new Promise((resolve) => {
  const scrollables = Array.from(document.querySelectorAll('.note-scroller'))
    .filter(el => el.scrollHeight > el.clientHeight + 50 && el.offsetParent !== null);
  const container = scrollables[0] ||
                    document.querySelector('#noteContainer') || document.querySelector('.note-container');
  if (!container) { resolve(false); return; }
  let i = 0;
  const step = () => {
    if (i >= rounds) { resolve(true); return; }
    i++;
    container.scrollBy(0, 1500);
    setTimeout(step, 800);
  };
  step();
})"""


def download_image(url, path):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if len(data) < 3000:
        return False  # 占位/头像
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return True


def download_video(url, v_dir, label):
    """下载视频 mp4 到 v_dir/video.mp4，返回文件路径或 None。"""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            total = int(resp.headers.get("content-length") or 0)
            total_mb = f"{total / 1048576:.1f}MB" if total else "?MB"
            sys.stderr.write(f"{label} 下载视频中（{total_mb}）...\n")
            chunks = []
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        data = b"".join(chunks)
        os.makedirs(v_dir, exist_ok=True)
        v_file = os.path.join(v_dir, "video.mp4")
        with open(v_file, "wb") as f:
            f.write(data)
        sys.stderr.write(f"{label} 视频已保存（{len(data) / 1048576:.1f}MB）\n")
        return v_file
    except Exception as e:
        sys.stderr.write(f"{label} 视频下载失败: {str(e)[:120]}\n")
        return None


def scrape_note(client, url, args):
    note_id = (NOTE_ID_RE.search(url) or [None, "unknown"])[1]
    label = f"[{note_id}]"
    client.navigate(url)
    time.sleep(2)

    # 等待弹窗加载（含 404/登录检测）
    state = None
    for _ in range(30):
        body = client.get_body_text()[:500]
        if client.evaluate("!!document.querySelector('#noteContainer #detail-title, #noteContainer .note-content, #noteContainer video')"):
            state = "loaded"
            break
        if "你访问的页面不见了" in body or "404" in body:
            state = "404"
            break
        if "登录后查看" in body:
            state = "login"
            break
        if "操作频繁" in body or "安全验证" in body or "验证码" in body:
            state = "risk"
            break
        if client.evaluate("location.pathname === '/explore'"):
            state = "redirect"
            break
        time.sleep(0.5)
    if state == "redirect":
        raise RuntimeError(f"{label} 笔记不存在或 token 失效（重定向到首页）")
    if state != "loaded":
        raise RuntimeError(f"{label} 加载失败: {state}")

    time.sleep(1.5)
    base = client.evaluate(f"({DETAIL_BASE_JS})()") or {}
    note = {
        "url": url, "noteId": note_id,
        "type": base.get("type", "normal"),
        "title": base.get("title", ""), "author": base.get("author", ""),
        "date": base.get("date", ""), "desc": base.get("desc", ""),
        "likes": clean_count(base.get("likes")),
        "collects": clean_count(base.get("collects")),
        "commentCount": clean_count(base.get("commentCount")),
    }

    # 评论：懒加载滚动 #noteContainer（不是 window）；连续 5 轮评论数不再增长视为到底，提前退出
    # 评论区不存在（如无评论的视频笔记）→ 直接空，避免滚动空转 15-20s
    if not client.evaluate("!!document.querySelector('.comments-container')"):
        note["comments"] = []
    else:
        prev_count, stable = -1, 0
        for _ in range(30):
            client.evaluate(f"({COMMENT_SCROLL_JS})(2)", await_promise=True)
            time.sleep(1)
            count = client.evaluate("document.querySelectorAll('.comments-container .parent-comment').length")
            if count >= args.max_comments:
                break
            if "到底了" in (client.evaluate("(document.querySelector('.comments-container')||{}).textContent||''") or ""):
                break
            if count == prev_count:
                stable += 1
                if stable >= 5:
                    break
            else:
                stable = 0
            prev_count = count

        # 展开回复（reply-limit > 1 才点「展开 x 条回复」）
        if args.reply_limit > 1:
            for _ in range(15):
                n = client.evaluate(f"({EXPAND_REPLIES_JS})()") or 0
                if n == 0:
                    break
                time.sleep(1.8)

        raw_comments = client.evaluate(
            f"({COMMENTS_JS})({args.reply_limit}, {args.max_comments})") or []
        note["comments"] = [tidy_comment(c) for c in raw_comments]

    # 图片：有 --media-dir 时保存到子目录并持久；否则仅 OCR 时下载到临时目录，OCR 后删除。
    # 下载触发条件 = 有保存目录（要保存）或 未 --no-ocr（要 OCR），二者都不满足则跳过。
    img_urls = base.get("imgUrls") or []
    want_images = bool(args.media_dir) or not args.no_ocr
    if note["type"] == "normal" and img_urls and want_images:
        subdir = media_dir_name(note_id, note["title"])
        persist = bool(args.media_dir)
        img_dir = os.path.join(args.media_dir, subdir) if persist else tempfile.mkdtemp(prefix="xhs-ocr-")
        try:
            saved = []
            for i, u in enumerate(img_urls):
                p = os.path.join(img_dir, f"img-{i + 1}.jpg")
                if download_image(u, p):
                    saved.append(p)
            if persist:
                note["images"] = saved
            if saved and not args.no_ocr:
                sys.stderr.write(f"{label} OCR {len(saved)} 张...\n")
                ocr = ocr_directory(img_dir)
                if ocr.get("images"):
                    note["ocrText"] = "\n\n---\n\n".join(x["text"] for x in ocr["images"] if x.get("text"))
                elif ocr.get("error"):
                    note["ocrError"] = ocr["error"]
        finally:
            if not persist:
                shutil.rmtree(img_dir, ignore_errors=True)

    # 视频：og:video meta 暴露 mp4 直链（无防盗链），有 --media-dir 时才下载
    video_url = base.get("videoUrl")
    if note["type"] == "video" and video_url:
        note["videoUrl"] = video_url
        if args.media_dir:
            v_dir = os.path.join(args.media_dir, media_dir_name(note_id, note["title"]))
            vf = download_video(video_url, v_dir, label)
            if vf:
                note["videoFile"] = vf

    sys.stderr.write(f"{label} 完成: {note['title'][:20]} 评论 {len(note['comments'])}\n")
    return note


def _scrape_in_tab(port, u, args):
    """在独立 tab 中抓取单篇笔记，返回 (u, note_or_error)。"""
    client = None
    try:
        client = create_page(port)
        return u, scrape_note(client, u, args)
    except Exception as e:
        return u, e
    finally:
        close_page(client)


def main():
    from concurrent.futures import ThreadPoolExecutor
    args = parse_args()
    urls = read_targets(args)
    if not urls:
        sys.stderr.write("没有可处理的 URL\n")
        sys.exit(1)
    port = ensure_cdp()
    limit = min(len(urls), args.parallel)
    notes, failures = [], []
    with ThreadPoolExecutor(max_workers=limit) as ex:
        for u, r in ex.map(_scrape_in_tab, [port] * len(urls), urls, [args] * len(urls)):
            if isinstance(r, Exception):
                failures.append({"url": u[:120], "error": str(r)[:200]})
                sys.stderr.write(f"失败: {str(r)[:120]}\n")
            else:
                notes.append(r)
    out = {"count": len(notes) + len(failures), "succeeded": len(notes),
           "failed": len(failures), "notes": notes}
    if failures:
        out["failures"] = failures
    # 先落盘再写 stdout：宿主对 stdout 有大小上限，Agent 拿全文直接读 logPath
    try:
        out["logPath"] = write_log(out, "xhs_detail")
        sys.stderr.write(f"[xhs-detail] 完整结果已落盘: {out['logPath']}\n")
    except Exception as e:
        sys.stderr.write(f"[xhs-detail] 落盘失败: {str(e)[:80]}\n")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    setup_stdout()
    main()
