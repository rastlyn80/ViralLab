"""Per-category spending caps, and where authorisation is allowed to live.

Two defects are covered here, both of which let configuration and reality
disagree about money.

1. `Spending.cap_for` always honoured `category_caps_usd`, but
   `load_settings` never filled it. Every per-category cap written in
   `settings.yaml` was silently ignored and only the global cap applied.

2. Authorisation lived in `config/settings.yaml`, which `deploy.py` copies
   to the server on every deploy. A deploy pushed the repository's `0.0`
   over an approved cap - and, in the direction that actually matters, a
   non-zero cap committed to the repository would have been deployed
   everywhere, authorising charges nobody approved on those machines.

The invariant throughout: **every ambiguity resolves to "not authorised"**.
A missing key, a malformed value, a negative number and an unreadable file
must all mean zero, never the global cap and never unlimited.
"""

from __future__ import annotations

import textwrap

import pytest
import yaml

from server import config
from server.config import Spending


def write_settings(tmp_path, spending_yaml: str):
    path = tmp_path / "settings.yaml"
    path.write_text(textwrap.dedent(spending_yaml), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def no_override(tmp_path, monkeypatch):
    """Point the override somewhere empty unless a test says otherwise."""
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH",
                        tmp_path / "absent.yaml")
    yield


# ---------------------------------------------------------------------------
# the caps are read at all
# ---------------------------------------------------------------------------

def test_category_caps_are_read_from_the_file(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            discovery: 6.0
            cloud_vision: 2.5
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("discovery") == 6.0
    assert sp.cap_for("cloud_vision") == 2.5


def test_a_category_cap_is_bounded_by_the_global_cap(tmp_path):
    """The global cap stays the hard ceiling whatever a category says."""
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 5.0
          providers_authorised: [apify]
          category_caps_usd:
            cloud_vision: 100.0
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("cloud_vision") == 5.0


def test_list_of_pairs_form_is_accepted(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            - [cloud_audio, 3.0]
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("cloud_audio") == 3.0


# ---------------------------------------------------------------------------
# every ambiguity resolves to "not authorised"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["not-a-number", None, "", "1.0; rm -rf"])
def test_a_non_numeric_cap_authorises_nothing(tmp_path, value):
    p = write_settings(tmp_path, f"""
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            cloud_vision: {value!r}
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("cloud_vision") == 0.0


def test_a_negative_cap_authorises_nothing(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            cloud_vision: -5.0
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("cloud_vision") == 0.0


def test_a_scalar_instead_of_a_mapping_authorises_nothing(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd: 7
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("cloud_vision") == 0.0
    assert sp.cap_for("cloud_audio") == 0.0


def test_an_unconfigured_new_category_is_not_authorised(tmp_path):
    """A missing key must never read as permission."""
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            discovery: 4.0
        """)
    sp = config.load_settings(p).spending
    for category in ("cloud_vision", "cloud_audio", "strategy", "invented"):
        assert sp.cap_for(category) == 0.0, category


# ---------------------------------------------------------------------------
# the categories stay independent
# ---------------------------------------------------------------------------

def test_the_four_categories_are_independent(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 20.0
          providers_authorised: [apify, openai]
          category_caps_usd:
            discovery: 8.0
            cloud_vision: 4.0
            cloud_audio: 2.0
            strategy: 1.0
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("discovery") == 8.0
    assert sp.cap_for("cloud_vision") == 4.0
    assert sp.cap_for("cloud_audio") == 2.0
    assert sp.cap_for("strategy") == 1.0


# ---------------------------------------------------------------------------
# existing installations keep working
# ---------------------------------------------------------------------------

def test_an_installation_with_no_category_caps_keeps_discovery(tmp_path):
    """The owner's actual configuration. Adding categories must not switch
    off a feature that was already approved."""
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 5.0
          providers_authorised: [apify]
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("discovery") == 5.0
    assert sp.cap_for("cloud_vision") == 0.0
    assert sp.any_authorised is True


def test_an_explicit_discovery_cap_overrides_the_legacy_inheritance(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 10.0
          providers_authorised: [apify]
          category_caps_usd:
            discovery: 2.0
        """)
    sp = config.load_settings(p).spending
    assert sp.cap_for("discovery") == 2.0


def test_nothing_is_authorised_by_default(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 0.0
          providers_authorised: []
        """)
    sp = config.load_settings(p).spending
    assert sp.any_authorised is False
    for category in ("discovery", "cloud_vision", "cloud_audio", "strategy"):
        assert sp.cap_for(category) == 0.0, category


def test_a_cap_without_a_provider_authorises_nothing(tmp_path):
    """Both conditions, or a request could reach a provider nobody named."""
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 50.0
          providers_authorised: []
        """)
    assert config.load_settings(p).spending.any_authorised is False


def test_a_provider_without_a_cap_authorises_nothing(tmp_path):
    p = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 0.0
          providers_authorised: [apify]
        """)
    assert config.load_settings(p).spending.any_authorised is False


# ---------------------------------------------------------------------------
# the runtime override
# ---------------------------------------------------------------------------

def test_the_override_wins_over_the_committed_file(tmp_path, monkeypatch):
    """Deploying must not be able to change what was approved here."""
    settings = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 0.0
          providers_authorised: []
        """)
    override = tmp_path / "spending.local.yaml"
    override.write_text(yaml.safe_dump({"spending": {
        "monthly_cap_usd": 5.0, "providers_authorised": ["apify"]}}),
        encoding="utf-8")
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH", override)

    sp = config.load_settings(settings).spending
    assert sp.monthly_cap_usd == 5.0
    assert sp.providers_authorised == ("apify",)
    assert sp.any_authorised is True


def test_a_corrupt_override_does_not_authorise_anything(tmp_path,
                                                        monkeypatch):
    """An unreadable override must fall back, never guess upward."""
    settings = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 0.0
          providers_authorised: []
        """)
    override = tmp_path / "spending.local.yaml"
    override.write_text("{{{ not yaml at all", encoding="utf-8")
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH", override)

    sp = config.load_settings(settings).spending
    assert sp.any_authorised is False
    assert sp.monthly_cap_usd == 0.0


def test_an_override_that_is_not_a_mapping_is_ignored(tmp_path, monkeypatch):
    settings = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 3.0
          providers_authorised: [apify]
        """)
    override = tmp_path / "spending.local.yaml"
    override.write_text("- just\n- a\n- list\n", encoding="utf-8")
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH", override)

    sp = config.load_settings(settings).spending
    # Falls back to the committed file rather than dropping to zero or
    # inventing something.
    assert sp.monthly_cap_usd == 3.0


def test_an_absent_override_leaves_the_committed_file_alone(tmp_path,
                                                            monkeypatch):
    settings = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 3.0
          providers_authorised: [apify]
        """)
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH",
                        tmp_path / "nope.yaml")
    assert config.load_settings(settings).spending.monthly_cap_usd == 3.0


def test_the_override_can_carry_category_caps(tmp_path, monkeypatch):
    settings = write_settings(tmp_path, """
        spending:
          monthly_cap_usd: 0.0
          providers_authorised: []
        """)
    override = tmp_path / "spending.local.yaml"
    override.write_text(yaml.safe_dump({"spending": {
        "monthly_cap_usd": 9.0,
        "providers_authorised": ["apify"],
        "category_caps_usd": {"discovery": 4.0, "cloud_vision": 1.0},
    }}), encoding="utf-8")
    monkeypatch.setattr(config, "SPENDING_OVERRIDE_PATH", override)

    sp = config.load_settings(settings).spending
    assert sp.cap_for("discovery") == 4.0
    assert sp.cap_for("cloud_vision") == 1.0
    assert sp.cap_for("strategy") == 0.0


# ---------------------------------------------------------------------------
# the deploy script must not ship the override
# ---------------------------------------------------------------------------

def test_deploy_excludes_the_spending_override():
    """Shipping it would defeat the entire point of separating it."""
    import ast
    from pathlib import Path

    # `scripts/` is not deployed to the server, so this assertion can only
    # run where the repository is checked out.
    path = Path(__file__).resolve().parents[2] / "scripts" / "deploy.py"
    if not path.exists():
        pytest.skip("scripts/deploy.py is not present (server checkout)")
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "EXCLUDE_NAMES"
                        for t in node.targets)):
            names = ast.literal_eval(node.value)
    assert names is not None, "deploy.py lost EXCLUDE_NAMES"
    assert "spending.local.yaml" in names


def test_deploy_preserves_unmanaged_env_keys():
    """The credentials dashboard writes into .env; deploy rewrites it.

    Without the preservation step a deploy deletes a pasted APIFY_TOKEN and
    research silently drops to fixture data.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "deploy.py"
    if not path.exists():
        pytest.skip("scripts/deploy.py is not present (server checkout)")
    src = path.read_text(encoding="utf-8")
    assert "preserved" in src and "managed" in src, (
        "deploy.py no longer preserves externally-set .env keys")


def test_spending_dataclass_is_frozen():
    """Authorisation must not be mutable in flight."""
    sp = Spending(monthly_cap_usd=1.0, providers_authorised=("apify",))
    with pytest.raises(Exception):
        sp.monthly_cap_usd = 999.0
