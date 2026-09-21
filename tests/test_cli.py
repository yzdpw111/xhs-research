# xhs-research/tests/test_cli.py
import json

import pytest
from xhs_search import parse_args, MAX_ROWS, FILTER_CHOICES
from xhs_detail import parse_args as detail_parse_args
from xhs_detail import parse_pipe_input, read_targets


class TestXhsSearchCli:
    def test_requires_q(self):
        with pytest.raises(SystemExit):
            parse_args([])

    def test_multi_q(self):
        args = parse_args(["--q", "a", "--q", "b", "--rows", "10"])
        assert args.q == ["a", "b"]
        assert args.rows == 10

    def test_rows_capped(self):
        args = parse_args(["--q", "a", "--rows", "999"])
        assert args.rows == MAX_ROWS

    def test_rows_default(self):
        args = parse_args(["--q", "a"])
        assert args.rows == 25

    def test_invalid_sort_rejected(self):
        with pytest.raises(SystemExit):
            parse_args(["--q", "a", "--sort", "随便"])

    def test_filter_choices_defined(self):
        assert set(FILTER_CHOICES.keys()) == {"sort", "type", "time", "scope", "distance"}


class TestXhsDetailCli:
    def test_requires_url_or_stdin(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with pytest.raises(SystemExit):
            detail_parse_args(["--max-comments", "5"])

    def test_max_comments_capped(self):
        args = detail_parse_args(["--url", "http://x", "--max-comments", "999"])
        assert args.max_comments == 80

    def test_reply_limit_clamped_at_least_1(self):
        args = detail_parse_args(["--url", "http://x", "--reply-limit", "-5"])
        assert args.reply_limit == 1

    def test_defaults_are_widened(self):
        args = detail_parse_args(["--url", "http://x"])
        assert args.max_comments == 30
        assert args.reply_limit == 2

    def test_shallow_removed(self):
        with pytest.raises(SystemExit):
            detail_parse_args(["--url", "http://x", "--shallow"])

    def test_media_dir_default_none(self):
        args = detail_parse_args(["--url", "http://x"])
        assert args.media_dir is None

    def test_media_dir_set(self):
        args = detail_parse_args(["--url", "http://x", "--media-dir", "D:/media"])
        assert args.media_dir == "D:/media"

    def test_video_flag_removed(self):
        with pytest.raises(SystemExit):
            detail_parse_args(["--url", "http://x", "--video"])


class TestParsePipeInput:
    def test_invalid_json_returns_none(self):
        assert parse_pipe_input("not json") is None
        assert parse_pipe_input("{broken") is None

    def test_non_dict_structure_returns_none(self):
        assert parse_pipe_input("[1, 2]") is None

    def test_valid_pipe_structure_returns_links(self):
        data = json.dumps({"results": [
            {"items": [{"link": "https://x/1"}, {"link": "https://x/2"}]},
            {"link": "https://x/3"},
            {"items": []},
        ]})
        links = parse_pipe_input(data)
        assert sorted(links) == ["https://x/1", "https://x/2", "https://x/3"]

    def test_valid_pipe_dedupes(self):
        data = json.dumps({"results": [{"items": [{"link": "https://x/1"}, {"link": "https://x/1"}]}]})
        assert parse_pipe_input(data) == ["https://x/1"]

    def test_empty_or_missing_results_returns_empty(self):
        assert parse_pipe_input('{"results": []}') == []
        assert parse_pipe_input("{}") == []

    def test_bom_prefixed_pipe_ok(self):
        # PowerShell 管道会给 stdin 注入 UTF-8 BOM，JSON 解析应容忍
        data = '\ufeff{"results": [{"items": [{"link": "https://x/1"}]}]}'
        assert parse_pipe_input(data) == ["https://x/1"]


class TestReadTargets:
    def test_urls_flag_wins(self, monkeypatch):
        args = detail_parse_args(["--url", "https://x/a", "--url", "https://x/b"])
        assert read_targets(args) == ["https://x/a", "https://x/b"]

    def test_invalid_pipe_exits_friendly(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: "not json")
        with pytest.raises(SystemExit) as e:
            read_targets(detail_parse_args([]))
        assert e.value.code == 1
        err = capsys.readouterr().err
        assert "JSON" in err and "stdin" in err

    def test_valid_pipe_returns_links(self, monkeypatch):
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("sys.stdin.read", lambda: json.dumps(
            {"results": [{"items": [{"link": "https://x/1"}, {"link": "https://x/2"}]}]}))
        links = read_targets(detail_parse_args([]))
        assert sorted(links) == ["https://x/1", "https://x/2"]
