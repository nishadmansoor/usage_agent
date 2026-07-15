"""Tool registry tests (offline)."""

from usage_agent import tools


def test_functions_and_specs_align():
    spec_names = {s["name"] for s in tools.get_tool_specs()}
    func_names = set(tools.get_tool_functions())
    assert spec_names == func_names
    # DAX is the compute path; the SQL tools are fixed/read-only inspection only.
    assert {"run_dax_query", "describe_model"} <= func_names
    assert {"list_data_tables", "preview_table", "table_row_count"} <= func_names
    # The agent must NOT be able to write its own SQL.
    assert "run_sql_query" not in func_names


def test_specs_are_well_formed():
    for spec in tools.get_tool_specs():
        assert set(spec) == {"name", "description", "input_schema"}
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # required entries must exist in properties
        for req in schema["required"]:
            assert req in schema["properties"]
