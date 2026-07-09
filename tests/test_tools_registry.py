"""Tool registry tests (offline)."""

from usage_agent import tools


def test_functions_and_specs_align():
    spec_names = {s["name"] for s in tools.get_tool_specs()}
    func_names = set(tools.get_tool_functions())
    assert spec_names == func_names
    assert {"run_dax_query", "describe_model", "run_sql_query"} <= func_names


def test_specs_are_well_formed():
    for spec in tools.get_tool_specs():
        assert set(spec) == {"name", "description", "input_schema"}
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # required entries must exist in properties
        for req in schema["required"]:
            assert req in schema["properties"]
