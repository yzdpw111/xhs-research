# tests/smoke_xhs.py
"""实机 smoke：需要 CDP 在线 + 小红书已登录，并会真实访问网络。

不属于离线单测套件，因此文件名刻意不用 test_ 前缀 —— `pytest tests` 不会自动收集它，
必须显式指定文件才会运行。

运行:
    python -m pytest tests/smoke_xhs.py -v -m smoke
"""
import json
import os
import subprocess
import sys

import pytest

# 本文件在 <repo>/tests/ 下；脚本在 <repo>/scripts/ 下。
# 不依赖调用者的 cwd（原先按 "xhs_search.py" 调用，而脚本实际在 scripts/ 下，故跑不起来）。
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "xhs_search.py")


@pytest.mark.smoke
def test_xhs_search_end_to_end():
    assert os.path.isfile(SCRIPT), f"脚本不存在: {SCRIPT}"

    r = subprocess.run(
        [sys.executable, SCRIPT, "--q", "机器学习", "--rows", "3"],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
        cwd=REPO)

    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr[-500:]}"

    # 脚本约定：stdout 是完整 JSON，结果同时落盘（stdout 可能被宿主截断）
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"stdout 不是合法 JSON ({e})；stdout 末尾: {r.stdout[-500:]!r}")

    # 登录失效 / 风控以 error 字段返回，而不是抛异常 —— 否则会误报成"结果为空"
    if "error" in data:
        pytest.fail(f"搜索返回错误（多为未登录或触发风控）: {data['error']}")

    # 单关键词搜索 → results 有 1 项；每项含该关键词的 items
    assert data["count"] == 1, f"单关键词搜索应返回 1 条 result，实际 {data['count']}"
    assert len(data["results"]) == 1

    entry = data["results"][0]
    items = entry["items"]
    assert len(items) >= 1, f"无结果项: {json.dumps(entry, ensure_ascii=False)[:500]}"
    assert items[0]["link"].startswith("https://www.xiaohongshu.com/explore/")
