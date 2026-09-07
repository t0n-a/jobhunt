import pytest

from jobhunt.llm.agy import AgyProvider


@pytest.mark.live
def test_real_agy_returns_schema_conforming_object():
    provider = AgyProvider(model="gemini-3.8-flash-medium", binary="agy.exe")
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    }
    result = provider.complete("Return a JSON object with ok set to true", schema=schema)
    assert isinstance(result, dict)
    assert result.get("ok") is True
