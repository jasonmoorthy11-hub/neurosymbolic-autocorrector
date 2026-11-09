"""Tests for application configuration."""

import importlib.util
import os
import re
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocorrector.config import Settings, get_settings


class _NoEnvSettings(Settings):
    """Settings subclass that ignores .env file for testing defaults."""

    model_config = {"env_prefix": "AUTOCORRECTOR_TEST_NOEXIST_", "env_file": None}


class TestSettingsDefaults:
    def test_default_host_is_loopback(self):
        # The MCP HTTP server has optional auth; it must not listen on every
        # interface unless someone asks for that
        s = _NoEnvSettings()
        assert s.host == "127.0.0.1"

    def test_default_port(self):
        s = _NoEnvSettings()
        assert s.port == 8000

    def test_default_neo4j_credentials_empty(self):
        s = _NoEnvSettings()
        assert s.neo4j_user == ""
        assert s.neo4j_password == ""

    def test_default_max_correction_attempts(self):
        s = _NoEnvSettings()
        assert s.max_correction_attempts == 3

    def test_default_confidence_score(self):
        s = _NoEnvSettings()
        assert s.correction_confidence_score == 0.92

    def test_default_cors_origins(self):
        s = _NoEnvSettings()
        assert s.cors_origins == ["http://localhost:3000"]

    def test_default_log_level(self):
        s = _NoEnvSettings()
        assert s.log_level == "INFO"


class TestSettingsValidation:
    def test_port_too_low(self):
        with pytest.raises(ValidationError):
            _NoEnvSettings(port=0)

    def test_port_too_high(self):
        with pytest.raises(ValidationError):
            _NoEnvSettings(port=70000)

    def test_valid_port(self):
        s = _NoEnvSettings(port=3000)
        assert s.port == 3000

    def test_max_correction_attempts_zero(self):
        with pytest.raises(ValidationError):
            _NoEnvSettings(max_correction_attempts=0)


class TestSettingsEnvOverride:
    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_TEST_NOEXIST_PORT", "9000")
        monkeypatch.setenv("AUTOCORRECTOR_TEST_NOEXIST_LOG_LEVEL", "DEBUG")
        s = _NoEnvSettings()
        assert s.port == 9000
        assert s.log_level == "DEBUG"

    def test_neo4j_env_override(self, monkeypatch):
        monkeypatch.setenv("AUTOCORRECTOR_TEST_NOEXIST_NEO4J_URI", "bolt://db:7687")
        monkeypatch.setenv("AUTOCORRECTOR_TEST_NOEXIST_NEO4J_USER", "testuser")
        monkeypatch.setenv("AUTOCORRECTOR_TEST_NOEXIST_NEO4J_PASSWORD", "testpass")
        s = _NoEnvSettings()
        assert s.neo4j_uri == "bolt://db:7687"
        assert s.neo4j_user == "testuser"
        assert s.neo4j_password == "testpass"


class TestNeo4jCredentialWarning:
    def test_warns_when_remote_uri_without_credentials(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _NoEnvSettings(
                neo4j_uri="neo4j+s://example.databases.neo4j.io", neo4j_user="", neo4j_password=""
            )
            assert any("credentials are empty" in str(warning.message) for warning in w)

    def test_default_uri_is_empty_so_nothing_dials_neo4j_unasked(self):
        # A default of bolt://localhost:7687 made every service store (and the
        # offline test suite) try a real connection; only explicit config connects.
        assert _NoEnvSettings().neo4j_uri == ""

    def test_warns_for_a_local_uri_without_credentials_too(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _NoEnvSettings(neo4j_uri="bolt://localhost:7687", neo4j_user="", neo4j_password="")
            assert any("credentials are empty" in str(warning.message) for warning in w)

    def test_no_warning_when_credentials_provided(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _NoEnvSettings(
                neo4j_uri="neo4j+s://example.neo4j.io", neo4j_user="neo4j", neo4j_password="secret"
            )
            assert not any("credentials are empty" in str(warning.message) for warning in w)


_ROOT = Path(__file__).resolve().parent.parent


class TestEverySettingIsRead:
    def test_settings_have_no_field_that_nothing_reads(self):
        # A documented setting that no code reads is a false promise to whoever sets
        # it (`debug`, `embedding_*`, `default_language` and an unwired
        # `neo4j_database` were all removed or wired for this reason).
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for folder in ("src", "scripts")
            for path in (_ROOT / folder).rglob("*.py")
            if path.name != "config.py"
        )
        unread = [
            name
            for name in Settings.model_fields
            # a read through a settings object, not any attribute of that name
            # (`logger.debug`, `request.client.host`)
            if not re.search(rf"(\bsettings|\b_settings|\bs|get_settings\(\))\.{name}\b", sources)
        ]
        assert unread == []


_ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
_ENV_LINE = re.compile(r"^(?P<comment>#\s*)?(?P<key>AUTOCORRECTOR_[A-Z0-9_]+)=", re.MULTILINE)


class TestEnvExample:
    """`cp .env.example .env` is a documented step, so the file must be safe as-is."""

    def _lines(self) -> list[tuple[bool, str]]:
        text = _ENV_EXAMPLE.read_text(encoding="utf-8")
        return [(m["comment"] is None, m["key"]) for m in _ENV_LINE.finditer(text)]

    def test_env_example_lists_some_settings(self):
        assert len(self._lines()) >= 5

    def test_env_example_every_key_is_a_settings_field(self):
        fields = set(Settings.model_fields)
        unknown = [
            key
            for _, key in self._lines()
            if key.removeprefix("AUTOCORRECTOR_").lower() not in fields
        ]
        assert unknown == []

    def test_env_example_has_no_active_line(self):
        # An active placeholder is a live setting after the copy: a fake Neo4j URI
        # makes every service dial it, a fake API key switches on the LLM tier.
        # Any uncommented line counts, whatever its prefix or indentation.
        lines = _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        active = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
        assert active == []


_INTEGRATION = Path(__file__).resolve().parent / "test_neo4j_integration.py"


class TestNeo4jIntegrationGuard:
    """The integration module deletes every node, so it must never run by accident."""

    def _load(self, monkeypatch, env: dict[str, str]):
        assert "load_dotenv" not in _INTEGRATION.read_text(encoding="utf-8")
        for key in ("NEO4J_URI", "NEO4J_PASSWORD", "NEO4J_TEST_WIPE"):
            monkeypatch.delenv(f"AUTOCORRECTOR_{key}", raising=False)
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        spec = importlib.util.spec_from_file_location("_neo4j_integration_probe", _INTEGRATION)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_neo4j_integration_skips_with_credentials_but_no_opt_in(self, monkeypatch):
        module = self._load(
            monkeypatch,
            {
                "AUTOCORRECTOR_NEO4J_URI": "neo4j+s://prod.example",
                "AUTOCORRECTOR_NEO4J_PASSWORD": "secret",
            },
        )
        assert module.pytestmark.args[0] is True

    def test_neo4j_integration_runs_only_with_the_wipe_opt_in(self, monkeypatch):
        module = self._load(
            monkeypatch,
            {
                "AUTOCORRECTOR_NEO4J_URI": "bolt://localhost:7687",
                "AUTOCORRECTOR_NEO4J_PASSWORD": "changeme",
                "AUTOCORRECTOR_NEO4J_TEST_WIPE": "1",
            },
        )
        assert module.pytestmark.args[0] is False

    def test_neo4j_integration_ignores_unprefixed_variables(self, monkeypatch):
        module = self._load(
            monkeypatch,
            {
                "NEO4J_URI": "bolt://elsewhere:7687",
                "NEO4J_PASSWORD": "secret",
                "AUTOCORRECTOR_NEO4J_TEST_WIPE": "1",
            },
        )
        assert module.pytestmark.args[0] is True


class TestGetSettings:
    def test_returns_settings_instance(self):
        s = get_settings()
        assert isinstance(s, Settings)


class TestSuiteIsolation:
    def test_suite_ignores_keys_and_neo4j_from_a_local_dotenv(self, tmp_path, monkeypatch):
        # Settings reads `.env` from the working directory. A developer's real key
        # there must not turn a test into a paid API call or a Neo4j connection:
        # tests/conftest.py pins these variables empty in the process environment,
        # which wins over the file.
        (tmp_path / ".env").write_text(
            "AUTOCORRECTOR_ANTHROPIC_API_KEY=sk-ant-from-dotenv\n"
            "AUTOCORRECTOR_OPENAI_API_KEY=sk-from-dotenv\n"
            "AUTOCORRECTOR_NEO4J_URI=bolt://from-dotenv:7687\n"
            "AUTOCORRECTOR_NEMO_RAILS_ENABLED=true\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        assert settings.anthropic_api_key == ""
        assert settings.openai_api_key == ""
        if os.environ.get("AUTOCORRECTOR_NEO4J_TEST_WIPE") != "1":
            assert settings.neo4j_uri == ""
        assert settings.nemo_rails_enabled is False

    @pytest.mark.parametrize("opted_in, expected", [(False, ""), (True, "bolt://disposable:7687")])
    def test_suite_keeps_the_neo4j_uri_only_for_the_opt_in_run(
        self, monkeypatch, opted_in, expected
    ):
        # conftest writes os.environ directly; monkeypatch restores it afterwards
        monkeypatch.setenv("AUTOCORRECTOR_NEO4J_URI", "bolt://disposable:7687")
        monkeypatch.setenv("AUTOCORRECTOR_ANTHROPIC_API_KEY", "sk-ant-leaked")
        monkeypatch.setenv("AUTOCORRECTOR_NEMO_RAILS_ENABLED", "true")
        if opted_in:
            monkeypatch.setenv("AUTOCORRECTOR_NEO4J_TEST_WIPE", "1")
        else:
            monkeypatch.delenv("AUTOCORRECTOR_NEO4J_TEST_WIPE", raising=False)
        conftest = Path(__file__).resolve().parent / "conftest.py"
        spec = importlib.util.spec_from_file_location("_conftest_probe", conftest)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
        assert os.environ["AUTOCORRECTOR_NEO4J_URI"] == expected
        assert os.environ["AUTOCORRECTOR_ANTHROPIC_API_KEY"] == ""
        assert os.environ["AUTOCORRECTOR_NEMO_RAILS_ENABLED"] == "false"
