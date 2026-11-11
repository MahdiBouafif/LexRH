from lexrh.config import Settings
from lexrh.doctor import Check, check_settings, format_checks


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {
        "mistral_api_key": "k",
        "generator_model": "m",
        "generator_api_key": "g",
        "generator_base_url": "https://api.example.com/v1",
    }
    return Settings(**(base | kw))  # type: ignore[arg-type]


def test_complete_settings_pass_without_leaking_secrets() -> None:
    check = check_settings(settings(mistral_api_key="SECRET-A", generator_api_key="SECRET-B"))
    assert check.ok
    assert "api.example.com" in check.detail
    assert "SECRET-A" not in check.detail and "SECRET-B" not in check.detail


def test_missing_values_are_reported() -> None:
    check = check_settings(
        settings(mistral_api_key="xxx", generator_model="", generator_api_key="")
    )
    assert not check.ok
    for needle in ("MISTRAL_API_KEY", "GENERATOR_MODEL", "GENERATOR_API_KEY"):
        assert needle in check.detail


def test_local_answer_model_needs_no_key() -> None:
    check = check_settings(
        settings(generator_base_url="http://localhost:11434/v1", generator_api_key="")
    )
    assert check.ok


def test_report_distinguishes_fail_and_warning() -> None:
    report = format_checks(
        [Check("a", True, "ok"), Check("b", False, "bad"), Check("c", False, "meh", warning=True)]
    )
    assert "[PASS] a" in report and "[FAIL] b" in report and "[WARN] c" in report
    assert "1 failed, 1 warning" in report
