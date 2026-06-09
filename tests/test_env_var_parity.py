"""
Parity guard: the DB env-var NAMES declared in each db config (``env_var_*``) MUST match
``_ENV_FALLBACKS`` in ``utils/cloud_config.py`` for that pipeline's ``(cloud, db_type)``.

WHY this guard exists
---------------------
The same fact (e.g. ``CRM_DB_PASSWORD``) is declared in TWO places:
  * the db config ``env_var_*`` keys  → feed the **K8s Secret** keys (via ``build_*_context``)
  * ``_ENV_FALLBACKS``                → the table ``cloud_get()`` reads to resolve credentials

``cloud_get()`` resolves the env tier ONLY through ``_ENV_FALLBACKS`` (cloud_config.py) — it
never reads the db yaml. So the two are two declarations of one fact that must agree. Rename one
side only (e.g. ``env_var_password`` in the yaml) and ``cloud_get()`` keeps reading the OLD name →
returns ``None`` at runtime → the pipeline pod dies with a ``host name "None"`` error. This test
catches that divergence at CI time instead of in production.

Databricks pipelines are skipped: they read credentials via ``dbutils.secrets`` / SSM, NOT
``cloud_get()``/``_ENV_FALLBACKS`` (signalled by ``target_infra_config`` → ``databricks.yaml``).
"""
import glob
import os

import pytest
import yaml

from utils.cloud_config import _ENV_FALLBACKS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# db config ``env_var_<suffix>``  ->  cloud_get() generic key used as the _ENV_FALLBACKS triple
_KEY_MAP = {
    "host": "db_host",
    "port": "db_port",
    "user": "db_user",
    "password": "db_password",
    "name": "db_name",
}


def _load_yaml(rel_or_abs_path: str) -> dict:
    path = rel_or_abs_path
    if not os.path.isabs(path):
        path = os.path.join(REPO, path)
    with open(path) as fh:
        return yaml.safe_load(fh)


def _pipeline_specs():
    """All object-storage pipelines (databricks excluded — it doesn't use cloud_get())."""
    specs = []
    for path in sorted(glob.glob(os.path.join(REPO, "configs/pipelines/*_pipeline.yaml"))):
        conf = _load_yaml(path)
        # Databricks-ness is signalled by target_infra_config -> configs/infra/databricks.yaml.
        if "databricks" in str(conf.get("target_infra_config", "")):
            continue
        specs.append(pytest.param(conf, id=os.path.basename(path)))
    return specs


_SPECS = _pipeline_specs()


def test_at_least_one_pipeline_is_checked():
    """Guard against the parametrize list silently going empty (-> false pass)."""
    assert _SPECS, "no object-storage pipeline specs found to check env-var parity for"


@pytest.mark.parametrize("conf", _SPECS)
def test_env_var_names_match_env_fallbacks(conf):
    cloud = str(conf.get("cloud_provider", "")).strip()
    src = conf.get("source_config")
    assert cloud, "pipeline spec missing cloud_provider"
    assert src, "pipeline spec missing source_config"

    db_conf = _load_yaml(src)
    db_type = str(db_conf.get("db_type", "")).strip()
    assert db_type, f"db config {src} missing db_type"

    for suffix, generic_key in _KEY_MAP.items():
        yaml_env = db_conf.get(f"env_var_{suffix}")
        assert yaml_env, f"db config {src} missing env_var_{suffix}"

        fallback = _ENV_FALLBACKS.get((cloud, db_type, generic_key))
        assert fallback is not None, (
            f"no _ENV_FALLBACKS entry for ({cloud}, {db_type}, {generic_key}) — "
            f"cloud_get() cannot resolve this credential at runtime"
        )
        assert yaml_env == fallback, (
            f"env-var name mismatch for {generic_key}: db config {src} declares "
            f"'{yaml_env}' but cloud_get()/_ENV_FALLBACKS uses '{fallback}'. "
            f"Rename one side and cloud_get() returns None at runtime — keep them identical."
        )
