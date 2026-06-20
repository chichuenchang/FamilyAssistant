# tests/test_web_reach.py — Web_Reach skill tests.
#
# Pure logic (parse_vtt/trim/summarize_youtube/web_search/web_read) is tested with
# injected fetchers — no live network. cli.py is exercised via subprocess on error
# paths only (also no network). Agent wiring is asserted against agent_core.
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

import reach

_CLI = str(
    _Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Web_Reach" / "cli.py"
)


def _run_cli(*args):
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class TestTrim:
    def test_short_text_unchanged(self):
        assert reach.trim("hello world", cap=100) == "hello world"

    def test_long_text_truncated_with_marker(self):
        out = reach.trim("a" * 7000, cap=6000)
        assert out.endswith("…[截断]")
        assert out.startswith("a")
        # body capped at ~cap; marker is the only thing allowed past it
        assert len(out) <= 6000 + len("…[截断]")

    def test_exact_cap_not_truncated(self):
        assert reach.trim("a" * 100, cap=100) == "a" * 100


class TestParseVtt:
    def test_strips_header_timestamps_and_indices(self):
        vtt = (
            "WEBVTT\n\n"
            "1\n00:00:01.000 --> 00:00:03.000\nHello world\n\n"
            "2\n00:00:03.000 --> 00:00:05.000\nGoodbye now\n"
        )
        out = reach.parse_vtt(vtt)
        assert "WEBVTT" not in out
        assert "-->" not in out
        assert "00:00" not in out
        assert "Hello world" in out
        assert "Goodbye now" in out

    def test_dedupes_consecutive_duplicate_lines(self):
        # YouTube auto-subs repeat the same caption across rolling cues.
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\nHello\n\n"
            "00:00:02.000 --> 00:00:03.000\nHello\n\n"
            "00:00:03.000 --> 00:00:04.000\nWorld\n"
        )
        out = reach.parse_vtt(vtt)
        assert out.count("Hello") == 1
        assert "World" in out

    def test_strips_inline_tags(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000 align:start position:0%\n"
            "<c>Hello</c> <00:00:01.500>there\n"
        )
        out = reach.parse_vtt(vtt)
        assert "<c>" not in out and "</c>" not in out
        assert "<00:00" not in out
        assert "align:start" not in out
        assert "Hello" in out and "there" in out


class TestSummarizeYoutube:
    def test_uses_subtitles_when_present(self):
        out = reach.summarize_youtube(
            "u",
            get_subs=lambda u: "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nReal transcript\n",
            get_meta=lambda u: {"title": "T", "description": "D"},
        )
        assert "Real transcript" in out
        assert not out.startswith("[错误]")
        assert "标题" not in out  # used subs, did not fall back to metadata

    def test_falls_back_to_metadata_when_no_subs(self):
        out = reach.summarize_youtube(
            "u",
            get_subs=lambda u: None,
            get_meta=lambda u: {"title": "Cool Video", "description": "about stuff"},
        )
        assert "Cool Video" in out
        assert "about stuff" in out

    def test_error_when_no_subs_and_no_meta(self):
        out = reach.summarize_youtube(
            "u", get_subs=lambda u: None, get_meta=lambda u: None,
        )
        assert out.startswith("[错误]")

    def test_empty_subtitles_fall_through_to_metadata(self):
        out = reach.summarize_youtube(
            "u",
            get_subs=lambda u: "WEBVTT\n\n",  # parses to empty transcript
            get_meta=lambda u: {"title": "Has Title", "description": ""},
        )
        assert "Has Title" in out


class TestWebSearch:
    def test_returns_trimmed_results(self):
        out = reach.web_search("latest news", fetch=lambda url: "result markdown here")
        assert "result markdown here" in out

    def test_routes_search_through_keyless_reader(self):
        seen = {}
        reach.web_search("hello world", fetch=lambda url: seen.update(url=url) or "x")
        # keyless: Jina reader (r.jina.ai) wrapping a DuckDuckGo results page.
        # s.jina.ai (Jina search) requires an API key — 401 keyless — so avoid it.
        assert "r.jina.ai" in seen["url"]
        assert "duckduckgo" in seen["url"]
        assert "hello" in seen["url"]
        assert " " not in seen["url"]  # query url-encoded

    def test_empty_query_errors_without_fetching(self):
        called = {"n": 0}

        def fetch(url):
            called["n"] += 1
            return "x"

        out = reach.web_search("   ", fetch=fetch)
        assert out.startswith("[错误]")
        assert called["n"] == 0

    def test_fetch_failure_returns_error(self):
        def boom(url):
            raise RuntimeError("net down")

        assert reach.web_search("q", fetch=boom).startswith("[错误]")

    def test_empty_result_returns_error(self):
        assert reach.web_search("q", fetch=lambda url: "   ").startswith("[错误]")


class TestWebRead:
    def test_returns_trimmed_page(self):
        out = reach.web_read("https://example.com", fetch=lambda url: "page text body")
        assert "page text body" in out

    def test_uses_jina_reader_prefix(self):
        seen = {}
        reach.web_read("https://example.com/a", fetch=lambda url: seen.update(url=url) or "x")
        assert seen["url"] == "https://r.jina.ai/https://example.com/a"

    def test_empty_url_errors(self):
        assert reach.web_read("  ", fetch=lambda url: "x").startswith("[错误]")

    def test_fetch_failure_returns_error(self):
        def boom(url):
            raise IOError("unreachable")

        assert reach.web_read("https://x", fetch=boom).startswith("[错误]")


class TestBestVtt:
    """Subtitle-file language ranking (en > zh > other); pure, no filesystem."""

    def test_prefers_english(self):
        picked = reach._best_vtt(
            [_Path("v.zh.vtt"), _Path("v.en.vtt"), _Path("v.fr.vtt")])
        assert picked.name == "v.en.vtt"

    def test_falls_back_to_zh_when_no_english(self):
        picked = reach._best_vtt([_Path("v.fr.vtt"), _Path("v.zh-Hans.vtt")])
        assert ".zh" in picked.name.lower()

    def test_ignores_non_vtt_and_none_when_empty(self):
        assert reach._best_vtt([_Path("v.en.srt"), _Path("notes.txt")]) is None
        assert reach._best_vtt([]) is None


class TestYtdlpGracefulDegrade:
    """yt-dlp absent/failing → adapters return None (summarize then falls back/errors)."""

    def test_subs_none_when_ytdlp_absent(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("yt-dlp")
        monkeypatch.setattr(reach.subprocess, "run", boom)
        assert reach.ytdlp_subs("https://youtu.be/x") is None

    def test_meta_none_when_ytdlp_absent(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("yt-dlp")
        monkeypatch.setattr(reach.subprocess, "run", boom)
        assert reach.ytdlp_meta("https://youtu.be/x") is None


class TestCli:
    """cli.py error paths — no network needed (empty args short-circuit before fetch)."""

    def test_web_search_empty_query_prints_error_exit0(self):
        r = _run_cli("web-search", "--query", "   ")
        assert r.returncode == 0, r.stderr
        assert "[错误]" in r.stdout

    def test_web_read_empty_url_prints_error_exit0(self):
        r = _run_cli("web-read", "--url", "   ")
        assert r.returncode == 0, r.stderr
        assert "[错误]" in r.stdout

    def test_missing_required_arg_exits_2(self):
        r = _run_cli("web-search")
        assert r.returncode == 2  # argparse: missing --query


class TestAgentRegistration:
    """Agent 端注册检查：3 个联网工具在 schema/map/白名单/路由中都已挂上。"""

    def test_reach_tools_registered(self):
        import agent_core
        names = {"web_search", "web_read", "youtube_summarize"}
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert names <= schema_names
        assert names <= set(agent_core._TOOL_MAP)

    def test_reach_commands_allowed_and_routed(self):
        import agent_core
        cmds = {"web-search", "web-read", "yt-summary"}
        assert cmds <= agent_core.ALLOWED_COMMANDS
        for c in cmds:
            assert agent_core._cli_path(c).parent.name == "Web_Reach"
