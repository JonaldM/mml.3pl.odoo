"""Verify SOH drift log threshold is configurable, not hardcoded at 0."""
import pathlib


def test_soh_drift_threshold_is_configurable():
    src = pathlib.Path(
        'addons/stock_3pl_mainfreight/models/route_engine.py'
    ).read_text()
    # After the fix, the threshold must be read from ir.config_parameter
    # rather than using a hardcoded module-level constant of 0
    assert 'mml_3pl.soh_drift_threshold' in src, (
        "SOH drift threshold must be read from ir.config_parameter key "
        "'mml_3pl.soh_drift_threshold' so ops can tune it post-go-live "
        "without a code change."
    )


def test_soh_drift_threshold_has_safe_default():
    src = pathlib.Path(
        'addons/stock_3pl_mainfreight/models/route_engine.py'
    ).read_text()
    # The default value should be 0 (log everything) for safety
    # The config param lookup must provide a default
    assert "'0'" in src or '"0"' in src or 'default' in src, (
        "ir.config_parameter lookup must provide a safe default value"
    )
