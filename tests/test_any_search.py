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
