from services.api_response import fail, ok


def test_ok_envelope_shape():
    data = ok(result={"x": 1})
    assert data["state"] == "SUCCESS"
    assert data["result"] == {"x": 1}
    assert data["error"] is None


def test_fail_envelope_shape():
    data = fail(error="boom")
    assert data["state"] == "FAILURE"
    assert data["error"] == "boom"
