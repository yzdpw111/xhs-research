# python/tests/test_xhs_detail.py
from xhs_detail import COMMENT_SCROLL_JS, media_dir_name


class TestMediaDirName:
    def test_basic(self):
        assert media_dir_name("abc", "标题") == "abc_标题"

    def test_illegal_chars_replaced(self):
        assert media_dir_name("abc", 'a:b*c?d') == "abc_a b c d"

    def test_truncates_long_title(self):
        name = media_dir_name("abc", "x" * 100)
        assert name.startswith("abc_")
        assert len(name) == 4 + 24  # noteId + '_' + 24 字符

    def test_empty_title_falls_back_to_note_id(self):
        assert media_dir_name("abc", "") == "abc"
        assert media_dir_name("abc", None) == "abc"

    def test_whitespace_merged_and_stripped(self):
        assert media_dir_name("abc", "  a   b  ") == "abc_a b"


class TestCommentScrollTarget:
    """回归：真正可滚动的是 .note-scroller。

    #noteContainer 的 overflowY 是 visible、scrollHeight == clientHeight，
    对它 scrollBy 纹丝不动（实测 top 恒为 0）—— 评论因此恒停在第 10 条，
    实测 --max-comments 30 也只拿到 10 条。
    """

    def test_prefers_note_scroller(self):
        assert ".note-scroller" in COMMENT_SCROLL_JS

    def test_note_scroller_checked_before_note_container(self):
        assert (COMMENT_SCROLL_JS.index(".note-scroller")
                < COMMENT_SCROLL_JS.index("#noteContainer"))
