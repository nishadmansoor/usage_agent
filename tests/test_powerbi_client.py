"""PowerBIClient tests (offline) — the HTTP transport is monkeypatched."""

from usage_agent.clients.powerbi import PowerBIClient

_EXECUTE_QUERIES_RESPONSE = {
    "results": [
        {"tables": [{"rows": [{"DimUser[Department]": "Eng", "[Total Cost USD]": 100.0}]}]}
    ]
}


def _client(**kwargs):
    return PowerBIClient(
        token_provider=lambda: "fake-token",
        base_url="https://api.powerbi.com/v1.0/myorg",
        **kwargs,
    )


def test_execute_dax_unwraps_rows(monkeypatch):
    client = _client(dataset_id="ds-1")
    captured = {}

    def fake_request(method, path, json=None):
        captured["method"] = method
        captured["path"] = path
        captured["json"] = json
        return _EXECUTE_QUERIES_RESPONSE

    monkeypatch.setattr(client, "_request", fake_request)

    rows = client.execute_dax("EVALUATE X")
    assert rows == [{"DimUser[Department]": "Eng", "[Total Cost USD]": 100.0}]
    assert captured["method"] == "POST"
    assert captured["json"]["queries"][0]["query"] == "EVALUATE X"


def test_dataset_scoped_path():
    assert _client(dataset_id="ds-1")._execute_path("ds-1") == "/datasets/ds-1/executeQueries"


def test_workspace_scoped_path():
    client = _client(workspace_id="ws-9", dataset_id="ds-1")
    assert client._execute_path("ds-1") == "/groups/ws-9/datasets/ds-1/executeQueries"


def test_empty_results_returns_empty_list(monkeypatch):
    client = _client(dataset_id="ds-1")
    monkeypatch.setattr(client, "_request", lambda *a, **k: {"results": []})
    assert client.execute_dax("EVALUATE X") == []
