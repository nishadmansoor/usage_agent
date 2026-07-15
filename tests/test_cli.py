"""CLI-level tests (offline) — the agent is stubbed."""

import usage_agent.cli as cli


def test_cli_handles_agent_error_gracefully(monkeypatch, capsys):
    """A failing model call should print a clean message + hint, not a traceback,
    and exit non-zero."""

    class Boom:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, prompt):
            raise RuntimeError("Error code: 403 - Public access is disabled.")

    monkeypatch.setattr(cli, "UsageAgent", Boom)
    rc = cli.main(["hello"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "Agent error" in out
    assert "Hint:" in out  # 403 hint fired
    assert "Traceback" not in out


def test_cli_prints_answer_on_success(monkeypatch, capsys):
    from usage_agent.agent import AgentResult

    class Ok:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, prompt):
            return AgentResult("42 users", posted_to_teams=False)

    monkeypatch.setattr(cli, "UsageAgent", Ok)
    rc = cli.main(["how many users"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "42 users" in out
