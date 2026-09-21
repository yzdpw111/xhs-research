# tests/smoke_xhs.py
"""实机 smoke：需要 CDP 在线 + 小红书已登录。
运行: python -m pytest tests/smoke_xhs.py -v -m smoke
"""
import json
import subprocess
import sys
import pytest

sys.path.insert(0, ".")


@pytest.mark.smoke
def test_xhs_search_end_to_end():
    r = subprocess.run(
        [sys.executable, "xhs_search.py", "--q", "机器学习", "--rows", "3"],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert r.returncode == 0, f"exit={r.returncode} stderr={r.stderr[-500:]}"
    data = json.loads(r.stdout)
    assert data["count"] == 1
    assert len(data["results"]) == 1
    items = data["results"][0]["items"]
    assert len(items) >= 1, f"no items: {data['results'][0]}"
    assert items[0]["link"].startswith("https://www.xiaohongshu.com/explore/")
