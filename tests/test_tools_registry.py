"""Tool registry tests (offline)."""

from usage_agent import tools


def test_functions_and_specs_align():
    spec_names = {s["name"] for s in tools.get_tool_specs()}
    func_names = set(tools.get_tool_functions())
    assert spec_names == func_names
    # Fixed, measure-reading tools are the only compute path.
    assert {"measure_values", "org_adoption", "org_headcount", "top_users"} <= func_names
    assert {"list_data_tables", "preview_table", "table_row_count"} <= func_names
    # The agent must NOT be able to write its own query in EITHER language.
    assert "run_sql_query" not in func_names
    assert "run_dax_query" not in func_names


def test_no_spec_accepts_free_form_query_text():
    """No tool may take a query/DAX/SQL string — that is the whole guarantee."""
    for spec in tools.get_tool_specs():
        for prop in spec["input_schema"]["properties"]:
            assert prop not in {"dax", "sql", "query_text", "expression"}, spec["name"]


def test_specs_are_well_formed():
    for spec in tools.get_tool_specs():
        assert set(spec) == {"name", "description", "input_schema"}
        schema = spec["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        # required entries must exist in properties
        for req in schema["required"]:
            assert req in schema["properties"]
