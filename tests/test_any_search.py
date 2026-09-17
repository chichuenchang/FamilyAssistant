# tests/test_any_search.py — Any_Search skill tests.
#
# Pure logic (trim/parse_sdp/search/extract/subdomains) is tested with an injected
# `call` transport — no live network. The JSON-RPC adapter (anysearch_call) is
# tested against a fake urlopen. cli.py is exercised via subprocess on error paths
# only. Agent wiring is asserted against agent_core.
import io
import json
import subprocess
import sys as _sys
from pathlib import Path as _Path

import pytest

import anysearch

_CLI = str(
    _Path(__file__).resolve().parent.parent
    / ".codewhale" / "skills" / "Any_Search" / "cli.py"
)


def _run_cli(*args):
    return subprocess.run(
        [_sys.executable, _CLI, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class TestTrim:
    def test_short_text_unchanged(self):
        assert anysearch.trim("hello", cap=100) == "hello"

    def test_long_text_truncated_with_marker(self):
        out = anysearch.trim("a" * 7000, cap=6000)
        assert out.endswith("…[截断]")
        assert len(out) <= 6000 + len("…[截断]")


class TestParseSdp:
    def test_none_and_empty(self):
        assert anysearch.parse_sdp(None) is None
        assert anysearch.parse_sdp("") is None

    def test_dict_passthrough(self):
        assert anysearch.parse_sdp({"a": "1"}) == {"a": "1"}

    def test_json_string(self):
        assert anysearch.parse_sdp('{"type":"stock","symbol":"AAPL"}') == {
            "type": "stock", "symbol": "AAPL"}

    def test_key_value_pairs(self):
        assert anysearch.parse_sdp("type=stock,symbol=AAPL,cn_code=") == {
            "type": "stock", "symbol": "AAPL", "cn_code": ""}


class TestSearch:
    def test_returns_trimmed_results(self):
        out = anysearch.search("latest news", call=lambda name, args: "result text")
        assert "result text" in out

    def test_general_sends_only_query(self):
        seen = {}
        anysearch.search("hello", call=lambda name, args: seen.update(n=name, a=args) or "x")
        assert seen["n"] == "search"
        assert seen["a"] == {"query": "hello"}

    def test_vertical_forwards_domain_and_parsed_sdp(self):
        seen = {}
        anysearch.search(
            "AAPL", call=lambda name, args: seen.update(a=args) or "x",
            domain="finance", sub_domain="finance.quote",
            sub_domain_params="type=stock,symbol=AAPL,cn_code=")
        assert seen["a"]["domain"] == "finance"
        assert seen["a"]["sub_domain"] == "finance.quote"
        assert seen["a"]["sub_domain_params"] == {
            "type": "stock", "symbol": "AAPL", "cn_code": ""}

    def test_sub_domain_ignored_without_domain(self):
        seen = {}
        anysearch.search("q", call=lambda name, args: seen.update(a=args) or "x",
                         sub_domain="finance.quote")
        assert "sub_domain" not in seen["a"]

    def test_max_results_clamped_to_10(self):
        seen = {}
        anysearch.search("q", call=lambda name, args: seen.update(a=args) or "x",
                         max_results=99)
        assert seen["a"]["max_results"] == 10

    def test_empty_query_errors_without_calling(self):
        called = {"n": 0}

        def call(name, args):
            called["n"] += 1
            return "x"

        out = anysearch.search("   ", call=call)
        assert out.startswith("[错误]")
        assert called["n"] == 0

    def test_call_failure_returns_error(self):
        def boom(name, args):
            raise RuntimeError("api down")

        assert anysearch.search("q", call=boom).startswith("[错误]")

    def test_empty_result_returns_error(self):
        assert anysearch.search("q", call=lambda n, a: "   ").startswith("[错误]")


class TestExtract:
    def test_returns_trimmed_page(self):
        out = anysearch.extract("https://example.com", call=lambda n, a: "page body")
        assert "page body" in out

    def test_forwards_url_to_extract_tool(self):
        seen = {}
        anysearch.extract("https://x/a", call=lambda n, a: seen.update(n=n, a=a) or "x")
        assert seen["n"] == "extract"
        assert seen["a"] == {"url": "https://x/a"}

    def test_empty_url_errors(self):
        assert anysearch.extract("  ", call=lambda n, a: "x").startswith("[错误]")

    def test_call_failure_returns_error(self):
        def boom(n, a):
            raise IOError("unreachable")

        assert anysearch.extract("https://x", call=boom).startswith("[错误]")


class TestSubdomains:
    def test_single_domain_uses_domain_key(self):
        seen = {}
        anysearch.subdomains("finance", call=lambda n, a: seen.update(n=n, a=a) or "tbl")
        assert seen["n"] == "get_sub_domains"
        assert seen["a"] == {"domain": "finance"}

    def test_multiple_domains_uses_domains_key(self):
        seen = {}
        anysearch.subdomains("finance,health", call=lambda n, a: seen.update(a=a) or "tbl")
        assert seen["a"] == {"domains": ["finance", "health"]}

    def test_empty_errors(self):
        assert anysearch.subdomains("", call=lambda n, a: "x").startswith("[错误]")


class TestAnysearchCall:
    """JSON-RPC transport against a fake urlopen — no live network."""

    def _fake_urlopen(self, payload, capture=None):
        body = json.dumps(payload).encode("utf-8")

        class _Resp:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def read(self_):
                return body

        def _open(req, timeout=None):
            if capture is not None:
                capture["headers"] = req.headers
                capture["data"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

        return _open

    def test_extracts_text_content(self, monkeypatch):
        payload = {"jsonrpc": "2.0", "id": 1,
                   "result": {"content": [{"type": "text", "text": "hello result"}]}}
        monkeypatch.setattr(anysearch.urllib.request, "urlopen",
                            self._fake_urlopen(payload))
        monkeypatch.setattr(anysearch, "_api_key", lambda: "")
        assert anysearch.anysearch_call("search", {"query": "q"}) == "hello result"

    def test_builds_jsonrpc_payload_and_bearer_header(self, monkeypatch):
        cap = {}
        payload = {"result": {"content": [{"type": "text", "text": "ok"}]}}
        monkeypatch.setattr(anysearch.urllib.request, "urlopen",
                            self._fake_urlopen(payload, cap))
        anysearch.anysearch_call("search", {"query": "q"}, api_key="SECRET")
        assert cap["data"]["method"] == "tools/call"
        assert cap["data"]["params"] == {"name": "search", "arguments": {"query": "q"}}
        # urllib title-cases header keys
        assert cap["headers"].get("Authorization") == "Bearer SECRET"

    def test_no_auth_header_when_anonymous(self, monkeypatch):
        cap = {}
        payload = {"result": {"content": [{"type": "text", "text": "ok"}]}}
        monkeypatch.setattr(anysearch.urllib.request, "urlopen",
                            self._fake_urlopen(payload, cap))
        anysearch.anysearch_call("search", {"query": "q"}, api_key="")
        assert "Authorization" not in cap["headers"]

    def test_api_error_raises(self, monkeypatch):
        payload = {"error": {"message": "quota exhausted"}}
        monkeypatch.setattr(anysearch.urllib.request, "urlopen",
                            self._fake_urlopen(payload))
        with pytest.raises(RuntimeError, match="quota exhausted"):
            anysearch.anysearch_call("search", {"query": "q"}, api_key="")


class TestEnvKeyPriority:
    def test_env_var_wins_over_dotenv(self, monkeypatch, tmp_path):
        # _load_env must NOT override an already-set process env var.
        monkeypatch.setenv("ANYSEARCH_API_KEY", "from_env")
        env_file = tmp_path / ".env"
        env_file.write_text("ANYSEARCH_API_KEY=from_file\n", encoding="utf-8")
        monkeypatch.setattr(anysearch, "__file__",
                            str(tmp_path / "anysearch.py"))
        assert anysearch._api_key() == "from_env"


class TestCli:
    """cli.py error paths — empty args short-circuit before any network call."""

    def test_search_empty_query_prints_error_exit0(self):
        r = _run_cli("any-search", "--query", "   ")
        assert r.returncode == 0, r.stderr
        assert "[错误]" in r.stdout

    def test_extract_empty_url_prints_error_exit0(self):
        r = _run_cli("any-extract", "--url", "   ")
        assert r.returncode == 0, r.stderr
        assert "[错误]" in r.stdout

    def test_missing_required_arg_exits_2(self):
        r = _run_cli("any-search")
        assert r.returncode == 2  # argparse: missing --query


class TestAgentRegistration:
    """Agent 端注册检查：3 个 AnySearch 工具在 schema/map/白名单/路由中都已挂上。"""

    def test_anysearch_tools_registered(self):
        import agent_core
        names = {"anysearch_search", "anysearch_extract", "anysearch_subdomains"}
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert names <= schema_names
        assert names <= set(agent_core._TOOL_MAP)

    def test_anysearch_commands_allowed_and_routed(self):
        import agent_core
        cmds = {"any-search", "any-extract", "any-subdomains"}
        assert cmds <= agent_core.ALLOWED_COMMANDS
        for c in cmds:
            assert agent_core._cli_path(c).parent.name == "Any_Search"


# ── fetch_images：搜图 → 下载 → 返回 data 相对路径 ─────────────

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
GIF = b"GIF89a" + b"0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"0" * 32

_IMG_MD = """## Search Results (2 results, 540ms)

### 1. Praying Mantis
- **URL**: https://unsplash.com/photos/abc
- https://images.unsplash.com/photo-1?w=1080

### 2. Macro mantis
- **URL**: https://www.pexels.com/photo/xyz/
- https://images.pexels.com/photos/2/p.jpeg
"""


class TestParseImageUrls:
    def test_takes_direct_image_lines_not_page_urls(self):
        assert anysearch.parse_image_urls(_IMG_MD) == [
            "https://images.unsplash.com/photo-1?w=1080",
            "https://images.pexels.com/photos/2/p.jpeg",
        ]

    def test_empty_input(self):
        assert anysearch.parse_image_urls("") == []

    def test_bing_murl_extracted_and_unescaped(self):
        html = ('<a m="{&quot;murl&quot;:&quot;https://a.com/1.jpg?x=1&amp;y=2&quot;,'
                '&quot;turl&quot;:&quot;https://t/1&quot;}"></a>'
                '<a m="{&quot;murl&quot;:&quot;https://b.com/2.png&quot;}"></a>')
        assert anysearch.parse_bing_image_urls(html) == [
            "https://a.com/1.jpg?x=1&y=2", "https://b.com/2.png"]


class TestSniffImageExt:
    @pytest.mark.parametrize("data,ext", [(JPEG, ".jpg"), (PNG, ".png"), (GIF, ".gif")])
    def test_sendable_types(self, data, ext):
        assert anysearch.sniff_image_ext(data) == ext

    @pytest.mark.parametrize("data", [WEBP, b"<svg xmlns=", b"<!DOCTYPE html>", b""])
    def test_unsendable_types_rejected(self, data):
        assert anysearch.sniff_image_ext(data) is None


class TestFetchImages:
    def _call(self, md=_IMG_MD):
        return lambda tool, args: md

    def test_saves_images_and_returns_one_path_per_line(self, tmp_path):
        out = anysearch.fetch_images(
            "mantis", call=self._call(), download=lambda u: JPEG, dest_dir=tmp_path)
        paths = out.splitlines()
        assert len(paths) == 2
        for p in paths:
            assert _Path(p).read_bytes() == JPEG
            assert _Path(p).suffix == ".jpg"

    def test_queries_resource_image_subdomain(self, tmp_path):
        seen = {}

        def call(tool, args):
            seen.update(tool=tool, **args)
            return _IMG_MD
        anysearch.fetch_images("mantis", call=call, download=lambda u: PNG,
                               dest_dir=tmp_path)
        assert seen["tool"] == "search"
        assert seen["query"] == "mantis"
        assert (seen["domain"], seen["sub_domain"]) == ("resource", "resource.image")

    def test_count_limits_saved_images(self, tmp_path):
        out = anysearch.fetch_images("mantis", call=self._call(),
                                     download=lambda u: JPEG, dest_dir=tmp_path, count=1)
        assert len(out.splitlines()) == 1
        assert len(list(tmp_path.iterdir())) == 1

    def test_count_clamped_to_five(self, tmp_path):
        md = "\n".join(f"- https://i.com/{i}.jpg" for i in range(20))
        out = anysearch.fetch_images("x", call=self._call(md),
                                     download=lambda u: JPEG, dest_dir=tmp_path, count=99)
        assert len(out.splitlines()) == 5

    def test_failed_and_unsendable_downloads_skipped(self, tmp_path):
        md = "- https://i.com/boom.jpg\n- https://i.com/a.webp\n- https://i.com/ok.png"

        def download(url):
            if "boom" in url:
                raise OSError("timeout")
            return WEBP if "webp" in url else PNG
        out = anysearch.fetch_images("x", call=self._call(md), download=download,
                                     dest_dir=tmp_path)
        assert [_Path(p).suffix for p in out.splitlines()] == [".png"]
        assert len(list(tmp_path.iterdir())) == 1

    def test_fallback_used_when_primary_has_no_images(self, tmp_path):
        out = anysearch.fetch_images(
            "x", call=self._call("no results"), download=lambda u: GIF,
            dest_dir=tmp_path, fallback=lambda q: [f"https://bing/{q}.gif"])
        assert [_Path(p).suffix for p in out.splitlines()] == [".gif"]

    def test_fallback_used_when_primary_raises(self, tmp_path):
        def call(tool, args):
            raise RuntimeError("rate limited")
        out = anysearch.fetch_images("x", call=call, download=lambda u: JPEG,
                                     dest_dir=tmp_path,
                                     fallback=lambda q: ["https://bing/1.jpg"])
        assert len(out.splitlines()) == 1
        assert not out.startswith("[错误]")

    def test_fallback_not_called_when_primary_succeeds(self, tmp_path):
        def fallback(q):
            raise AssertionError("fallback must not run")
        out = anysearch.fetch_images("x", call=self._call(), download=lambda u: JPEG,
                                     dest_dir=tmp_path, fallback=fallback)
        assert not out.startswith("[错误]")

    def test_all_sources_fail_returns_error(self, tmp_path):
        def download(url):
            raise OSError("nope")

        def fallback(q):
            raise RuntimeError("bing down")
        out = anysearch.fetch_images("x", call=self._call(), download=download,
                                     dest_dir=tmp_path, fallback=fallback)
        assert out.startswith("[错误]")

    def test_empty_query_is_error(self, tmp_path):
        out = anysearch.fetch_images("  ", call=self._call(), download=lambda u: JPEG,
                                     dest_dir=tmp_path)
        assert out.startswith("[错误]")

    def test_rel_maps_returned_paths(self, tmp_path):
        out = anysearch.fetch_images("x", call=self._call(), download=lambda u: JPEG,
                                     dest_dir=tmp_path, count=1,
                                     rel=lambda p: "REL/" + _Path(p).name)
        assert out.startswith("REL/")

    def test_time_budget_stops_further_downloads(self, tmp_path):
        # 慢源不能把 Bot 拖到子进程超时：预算用尽后不再发起新下载
        tried = []
        out = anysearch.fetch_images(
            "x", call=self._call(), download=lambda u: tried.append(u) or JPEG,
            dest_dir=tmp_path, budget=0, fallback=lambda q: ["https://bing/1.jpg"])
        assert tried == []
        assert out.startswith("[错误]")

    def test_old_downloads_pruned(self, tmp_path):
        import os
        import time
        old = tmp_path / "old.jpg"
        old.write_bytes(JPEG)
        stale = time.time() - 8 * 86400
        os.utime(old, (stale, stale))
        anysearch.fetch_images("x", call=self._call(), download=lambda u: JPEG,
                               dest_dir=tmp_path, count=1)
        assert not old.exists()


class TestDownloadImage:
    """下载适配器的闸门：协议 / 内网地址 / 体积上限。不联网。"""

    @staticmethod
    def _public(host):
        return ["93.184.216.34"]

    @staticmethod
    def _fake_open(body):
        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return lambda req, timeout: _Resp(body)

    def test_returns_body(self, monkeypatch):
        monkeypatch.setattr(anysearch, "_open", self._fake_open(JPEG))
        assert anysearch.download_image("https://i.com/a.jpg", resolve=self._public) == JPEG

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://i.com/a.jpg", "i.com/a.jpg"])
    def test_non_http_scheme_rejected(self, monkeypatch, url):
        monkeypatch.setattr(anysearch, "_open", self._fake_open(JPEG))
        with pytest.raises(ValueError):
            anysearch.download_image(url, resolve=self._public)

    @pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "192.168.1.1",
                                    "169.254.169.254", "::1"])
    def test_private_address_rejected(self, monkeypatch, ip):
        monkeypatch.setattr(anysearch, "_open", self._fake_open(JPEG))
        with pytest.raises(ValueError):
            anysearch.download_image("http://evil.example/a.jpg", resolve=lambda h: [ip])

    def test_oversize_rejected(self, monkeypatch):
        monkeypatch.setattr(anysearch, "_open", self._fake_open(b"x" * 101))
        with pytest.raises(ValueError):
            anysearch.download_image("https://i.com/a.jpg", resolve=self._public,
                                     max_bytes=100)


class TestFetchImagesWiring:
    def test_registered_as_member_locked_image_tool(self):
        import agent_core
        schema_names = {t["function"]["name"] for t in agent_core.TOOL_SCHEMAS}
        assert "fetch_images" in schema_names
        assert "fetch_images" in agent_core._TOOL_MAP
        assert "fetch_images" in agent_core._IMAGE_TOOLS
        assert agent_core._apply_member("fetch_images", {"member": "X"}, "Jim")["member"] == "Jim"

    def test_command_routed_with_long_timeout(self):
        import agent_core
        assert "any-images" in agent_core.ALLOWED_COMMANDS
        assert agent_core._cli_path("any-images").parent.name == "Any_Search"
        assert agent_core._CLI_TIMEOUTS["any-images"] >= 60

    def test_system_prompt_routes_picture_requests(self):
        import agent_core
        assert "fetch_images" in agent_core._build_system_prompt()

    def test_multi_path_result_becomes_one_sentinel_each(self, monkeypatch):
        import agent_core
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        agent = agent_core.Agent(idle_clear_hours=0)
        monkeypatch.setitem(agent_core._TOOL_MAP, "visualize_data",
                            lambda args: "Jim/web_images/a.jpg\nJim/web_images/b.png\n")
        replies = iter([
            {"content": "", "tool_calls": [{"id": "c1", "function": {
                "name": "visualize_data", "arguments": "{}"}}]},
            {"content": "给你找了两张"},
        ])
        monkeypatch.setattr(agent, "_call_llm", lambda msgs, user="": next(replies))
        reply = agent.handle("看看螳螂的图片", user="u", member="Jim")
        _, imgs, _ = agent_core.split_reply(reply)
        assert imgs == ["Jim/web_images/a.jpg", "Jim/web_images/b.png"]

    def test_downloads_excluded_from_backup(self):
        import backup_sync as bs
        assert bs._excluded("Jim/web_images/1_mantis_0.jpg") is True

    def test_cli_empty_query_prints_error(self):
        r = _run_cli("any-images", "--query", " ")
        assert r.returncode == 0
        assert r.stdout.startswith("[错误]")
