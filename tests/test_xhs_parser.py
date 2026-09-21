# tests/test_xhs_parser.py
from xhs_parser import (
    clean_count, build_explore_url, dedupe_notes, tidy_note, tidy_comment,
)

class TestCleanCount:
    def test_plain_number(self):
        assert clean_count("1234") == "1234"
    def test_wan_units(self):
        assert clean_count("1.2万") == "1.2万"
    def test_k_units(self):
        assert clean_count("3k") == "3k"
    def test_invalid_falls_back(self):
        assert clean_count("赞") == "0"

class TestBuildExploreUrl:
    def test_with_token(self):
        href = "/search_result/abc123?xsec_token=TOK&xsec_source=pc_search"
        assert build_explore_url(href) == (
            "https://www.xiaohongshu.com/explore/abc123"
            "?xsec_token=TOK&xsec_source=pc_search&source=web_explore_feed")
    def test_explore_href_passthrough(self):
        href = "/explore/abc123?xsec_token=TOK&xsec_source=pc_search"
        assert build_explore_url(href).startswith(
            "https://www.xiaohongshu.com/explore/abc123?")
    def test_preencoded_token_not_double_encoded(self):
        # href 里已是 `%3D` 编码形式：unquote→quote 保证只编码一次，绝不出现 %25
        href = "/search_result/abc123?xsec_token=TOK%3D123&xsec_source=pc_search"
        url = build_explore_url(href)
        assert "xsec_token=TOK%3D123" in url
        assert "%25" not in url
    def test_plain_base64url_token_encoded_once(self):
        # 真实页面 token（base64url，末尾 =）也应只编码一次
        href = ("/search_result/abc123?"
                "xsec_token=ABWWf4CQEW5hNiF7FNb1CtJ--54V4wufJfcUzvO2o0gdY="
                "&xsec_source=pc_search")
        url = build_explore_url(href)
        assert "%25" not in url
        assert "xsec_token=ABWWf4CQEW5hNiF7FNb1CtJ--54V4wufJfcUzvO2o0gdY%3D" in url
    def test_missing_token(self):
        assert build_explore_url("/search_result/abc123") is None

class TestTidyNote:
    def test_filters_invalid(self):
        assert tidy_note({"noteId": "", "title": "x"}) is None
        assert tidy_note({"noteId": "abc", "link": ""}) is None
    def test_keeps_valid_and_normalizes(self):
        raw = {"noteId": "abc", "title": " 标题 ", "likes": "赞",
               "author": "作者", "time": "06-08", "type": "normal", "link": "L"}
        n = tidy_note(raw)
        assert n["title"] == "标题"
        assert n["likes"] == "0"
        assert n["type"] == "normal"

class TestDedupeNotes:
    def test_dedupes_by_note_id(self):
        a = {"noteId": "a", "link": "1"}
        b = {"noteId": "a", "link": "2"}
        assert len(dedupe_notes([a, b])) == 1

class TestTidyComment:
    def test_normalizes_counts_and_text(self):
        raw = {"author": " 张三 ", "content": " 好文 ", "likes": "赞",
               "date": "07-08", "location": "河南", "replies": []}
        c = tidy_comment(raw)
        assert c["author"] == "张三"
        assert c["content"] == "好文"
        assert c["likes"] == "0"
        assert c["location"] == "河南"
