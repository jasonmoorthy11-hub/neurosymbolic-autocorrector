"""Tests for application configuration."""

import warnings

import pytest
from pydantic import ValidationError

from autocorrector.config import Settings, get_settings


class _NoEnvSettings(Settings):
    """Settings subclass that ignores .env file for testing defaults."""

    model_config = {"env_prefix": "AUTOCORRECTOR_TEST_NOEXIST_", "env_file": None}


class TestSettingsDefaults:
    def test_default_host(self):
        s = _NoEnvSettings()
        assert s.host == "0.0.0.0"

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

    def test_no_warning_for_localhost(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _NoEnvSettings(neo4j_uri="bolt://localhost:7687", neo4j_user="", neo4j_password="")
            assert not any("credentials are empty" in str(warning.message) for warning in w)

    def test_no_warning_when_credentials_provided(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _NoEnvSettings(
                neo4j_uri="neo4j+s://example.neo4j.io", neo4j_user="neo4j", neo4j_password="secret"
            )
            assert not any("credentials are empty" in str(warning.message) for warning in w)


class TestGetSettings:
    def test_returns_settings_instance(self):
        s = get_settings()
        assert isinstance(s, Settings)
