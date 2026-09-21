#!/usr/bin/env python3
"""
xhs_parser.py — 小红书搜索结果后处理纯函数

从浏览器提取 JS 返回的原始 dict 清洗为结构化结果。
全部为纯函数，可离线单测。
"""
import re
from urllib.parse import quote, unquote

_COUNT_RE = re.compile(r"^[\d.]+[万wk]?$", re.I)


def clean_count(raw):
    """点赞/收藏/评论数清洗：'1.2万'/'3k' 原样保留，无效回退 '0'"""
    if not raw:
        return "0"
    s = str(raw).strip()
    return s if _COUNT_RE.match(s) else "0"


def build_explore_url(href):
    """封面 href → 详情 URL。必须含 xsec_token，否则返回 None（会 404）"""
    if not href:
        return None
    m = re.search(r"/(?:search_result|explore)/([0-9a-f]+)\?(.*)", href)
    if not m:
        return None
    note_id, query = m.group(1), m.group(2)
    params = {}
    for kv in query.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            params[k] = v
    token = params.get("xsec_token")
    if not token:
        return None
    # href 中的 token 可能是已编码形式（如 `%3D`），先 decode 再 quote，
    # 保证恰好编码一次：`%253D` 这种二次编码不会出现。
    return (f"https://www.xiaohongshu.com/explore/{note_id}"
            f"?xsec_token={quote(unquote(token))}"
            f"&xsec_source=pc_search&source=web_explore_feed")


def tidy_note(raw):
    """单条笔记结果清洗；无效（无 noteId 或 link）返回 None"""
    note_id = (raw.get("noteId") or "").strip()
    link = raw.get("link") or ""
    if not note_id or not link:
        return None
    return {
        "noteId": note_id,
        "title": (raw.get("title") or "").strip(),
        "link": link,
        "author": (raw.get("author") or "").strip(),
        "time": (raw.get("time") or "").strip(),
        "likes": clean_count(raw.get("likes")),
        "type": raw.get("type") if raw.get("type") in ("video", "normal") else "normal",
    }


def dedupe_notes(items):
    """按 noteId 去重（保持顺序）"""
    seen, out = set(), []
    for it in items:
        if it and it["noteId"] not in seen:
            seen.add(it["noteId"])
            out.append(it)
    return out


def tidy_comment(raw):
    """评论条目清洗"""
    return {
        "author": (raw.get("author") or "").strip(),
        "content": (raw.get("content") or "").strip(),
        "date": (raw.get("date") or "").strip(),
        "location": (raw.get("location") or "").strip(),
        "likes": clean_count(raw.get("likes")),
        "replies": [tidy_comment(r) for r in (raw.get("replies") or [])],
    }
