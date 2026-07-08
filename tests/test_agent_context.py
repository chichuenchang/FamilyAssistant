# tests/test_agent_context.py — agent_core 上下文自动管理（token 预算裁剪 + 闲置清空）。
import agent_core


def test_estimate_tokens_cjk_and_ascii():
    assert agent_core._estimate_tokens("") == 0
    assert agent_core._estimate_tokens("你好啊") == 3          # CJK ≈ 1 token/字
    assert agent_core._estimate_tokens("abcdefgh") == 2        # ASCII ≈ 4 字符/token
    assert agent_core._estimate_tokens("你好ab") == 3          # 2 CJK + 2 ASCII(向上取整)


def test_save_history_trims_oldest_pairs_over_token_budget():
    agent = agent_core.Agent(history_size=100, context_max_tokens=50,
                             idle_clear_hours=0)
    for i in range(5):
        agent._save_history("u", f"问题{i}" + "字" * 20, f"回答{i}" + "字" * 20)
    h = agent.history["u"]
    total = sum(agent_core._estimate_tokens(m["content"]) for m in h)
    assert total <= 50
    # 成对丢弃最旧、保留最新：首条必是 user、末条是最后一轮的 assistant
    assert h[0]["role"] == "user" and h[-1]["content"].startswith("回答4")
    assert not any(m["content"].startswith(("问题0", "回答0")) for m in h)


def test_save_history_keeps_last_pair_even_if_over_budget():
    agent = agent_core.Agent(context_max_tokens=5, idle_clear_hours=0)
    agent._save_history("u", "字" * 100, "字" * 100)
    assert len(agent.history["u"]) == 2  # 单轮超预算也不清成空


def test_save_history_no_trim_when_budget_disabled():
    agent = agent_core.Agent(history_size=100, context_max_tokens=0,
                             idle_clear_hours=0)
    for i in range(5):
        agent._save_history("u", "字" * 50, "字" * 50)
    assert len(agent.history["u"]) == 10


def test_idle_clears_history_before_next_message(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = agent_core.Agent(idle_clear_hours=2)
    agent.history["u"] = [{"role": "user", "content": "旧话题"},
                          {"role": "assistant", "content": "旧回复"}]
    agent._last_active["u"] = 1_000_000.0
    monkeypatch.setattr(agent_core.time, "time",
                        lambda: 1_000_000.0 + 3 * 3600)  # 闲置 3 小时 > 2
    agent.handle("新话题", user="u", member="爸爸")  # 无 API key，早退但已过闲置检查
    assert "u" not in agent.history
    assert agent._last_active["u"] == 1_000_000.0 + 3 * 3600


def test_no_idle_clear_within_window(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = agent_core.Agent(idle_clear_hours=2)
    agent.history["u"] = [{"role": "user", "content": "旧话题"},
                          {"role": "assistant", "content": "旧回复"}]
    agent._last_active["u"] = 1_000_000.0
    monkeypatch.setattr(agent_core.time, "time", lambda: 1_000_000.0 + 3600)  # 1 小时
    agent.handle("继续", user="u", member="爸爸")
    assert len(agent.history["u"]) == 2


def test_no_idle_clear_when_disabled(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = agent_core.Agent(idle_clear_hours=0)
    agent.history["u"] = [{"role": "user", "content": "旧话题"}]
    agent._last_active["u"] = 1_000_000.0
    monkeypatch.setattr(agent_core.time, "time",
                        lambda: 1_000_000.0 + 1000 * 3600)
    agent.handle("嗨", user="u", member="爸爸")
    assert len(agent.history["u"]) == 1


def test_idle_clear_is_per_user(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    agent = agent_core.Agent(idle_clear_hours=2)
    agent.history["idle_u"] = [{"role": "user", "content": "a"}]
    agent.history["fresh_u"] = [{"role": "user", "content": "b"}]
    agent._last_active["idle_u"] = 1_000_000.0
    agent._last_active["fresh_u"] = 1_000_000.0 + 3 * 3600 - 60
    monkeypatch.setattr(agent_core.time, "time",
                        lambda: 1_000_000.0 + 3 * 3600)
    agent.handle("x", user="idle_u", member="爸爸")
    agent.handle("y", user="fresh_u", member="妈妈")
    assert "idle_u" not in agent.history
    assert len(agent.history["fresh_u"]) == 1


def test_config_defaults_applied():
    agent = agent_core.Agent()
    assert agent.context_max_tokens == agent_core._CTX_MAX_TOKENS
    assert agent.idle_clear_seconds == agent_core._IDLE_CLEAR_HOURS * 3600
