import os
import sys
import datetime
import logging
from dotenv import load_dotenv

from agents.constants import CONFIGS_DIR
from agents.state import build_initial_state
from utils.prompt_utils import format_prompt
from utils.file_utils import read_file
from utils.config_utils import load_pipeline_bundle
from utils.nlp_parser import build_pipeline_bundle_from_nl

# The Compiled Graph
from graph import app

# Load environment variables
load_dotenv()

# --- LOGGING CONFIGURATION ---
# Human format by default (unchanged); set LOG_FORMAT=json for one JSON object per line with a
# per-run correlation id. See utils/logging_setup.py.
from utils.logging_setup import setup_logging  # noqa: E402

logger = setup_logging(level=logging.INFO)


class MissionFailedError(RuntimeError):
    """The graph terminated without a verified deployment (mission_status != 'verified').

    Thrown INTO the stream generator at the FINISH update so the LangSmith root run is
    marked status=error with this message, then re-raised to the caller which exits 1 —
    the GitHub Action goes red. 'The graph ran to completion' is not success; only the
    Medic's end-to-end verification is."""


_MISSION_FAILURE_SUMMARIES = {
    "escalated": (
        "self-healing was abandoned — the same error survived 3 fix rounds, or an "
        "operational blocker (e.g. a Terraform state lock) needs a human. "
        "See the Medic's last message in the log above for the exact diagnosis."
    ),
    "ci_unverified": (
        "the CI run never produced a result within the polling budget (~10 min) — "
        "the deployment outcome is UNKNOWN. Check GitHub Actions manually "
        "(workflow not pushed / wrong branch / GHA disabled / GH_TOKEN 403)."
    ),
    "": (
        "the graph finished without explicit Medic verification (e.g. an LLM-fallback "
        "FINISH). Treating an unverified end as failure."
    ),
}


def mission_failure_summary(mission_status: str) -> str:
    """Human-readable failure reason for a terminal mission_status (fail-safe default)."""
    return _MISSION_FAILURE_SUMMARIES.get(mission_status, _MISSION_FAILURE_SUMMARIES[""])


def _consume_stream(stream) -> str:
    """Drive the graph stream to completion, tracking the terminal mission_status.

    On a FINISH update without mission_status == 'verified', throws MissionFailedError
    INTO the generator (stream.throw) so the tracing callbacks record the root run as
    an error, then lets the exception propagate to the caller. Returns the final
    mission_status when the run is verified.
    """
    mission_status = ""
    for output in stream:
        for node_name, state_update in output.items():
            logger.info(f"Node '{node_name.upper()}' finished execution.")
            if "written_files" in state_update:
                logger.info(f"📂 Files: {state_update['written_files']}")
            if state_update.get("mission_status"):
                mission_status = state_update["mission_status"]
            if "next_step" in state_update:
                print(f"    👉 Routing to: {state_update['next_step']}")
            if state_update.get("error_log"):
                logger.warning(f"Health issues reported by '{node_name}'.")
            if state_update.get("next_step") == "FINISH" and mission_status != "verified":
                stream.throw(MissionFailedError(
                    f"mission_status='{mission_status or 'unset'}' — "
                    f"{mission_failure_summary(mission_status)}"
                    f"\n\n⚠️  COST/CLEANUP: a failed run may have left PAID cloud resources running "
                    f"(cluster + node groups, DB, LoadBalancer Services, images). Verify and tear down: "
                    f"run the `destroy.yml` workflow (or `make {{aws,azure,gcp}}-pause`) and confirm no "
                    f"orphaned LBs/instances remain. See docs/RUNBOOK.md."
                ))
    return mission_status


def _is_natural_language(arg: str) -> bool:
    """
    Heuristic: if the argument is longer than 4 words it is a natural language
    description, not a pipeline YAML slug.
    """
    return len(arg.split()) > 4


def _launch(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task):
    """
    Common launch path: initialize state and run the LangGraph workflow.
    Called by both the YAML-based and NL-based entry points.
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    unique_project_id = f"{pipeline_id.upper()}-{timestamp}"
    logger.info(f"Generated Project ID: {unique_project_id}")

    os.environ["PROJECT_ID"] = unique_project_id

    # CLOUD_PROVIDER drives every agent-runtime cloud_get() call (e.g. read_data_schema's
    # schema introspection). It is read from the pipeline config — NEVER assumed. Without
    # this, tools default to "aws" and resolve the wrong credential keys on GCP/Azure
    # (e.g. POSTGRES_DB_HOST instead of CRM_DB_HOST → host "None").
    os.environ["CLOUD_PROVIDER"] = pipe_conf.get("cloud_provider", "aws")

    # PIPELINE_PLATFORM lets the file tools (write_project_file, validate_generated_code) — which
    # receive only (filename, content), not the infra config — know this is a Databricks pipeline.
    # Without it they cannot tell that requirements.txt isn't needed (the LLM emits it empty or
    # pyspark-only) or that a .json is a Lakeview dashboard. "kubernetes" for the object-storage clouds.
    os.environ["PIPELINE_PLATFORM"] = infra_conf.get("provider", "kubernetes").lower()

    # PII_SENSITIVE lets read_data_schema mask sample rows (no raw PII to the LLM/LangSmith) and lets
    # validate_generated_code enforce that the generated script actually anonymizes PII before writing.
    os.environ["PII_SENSITIVE"] = "true" if pipe_conf.get("pii_sensitive") else "false"

    initial_state = build_initial_state(
        project_id=unique_project_id,
        task=task,
        raw_configs={
            "pipeline": pipe_conf,
            "database": db_conf,
            "rules": rules_conf,
            "infrastructure": infra_conf,
        },
        target_infra=infra_conf.get("service_name", pipe_conf.get("cloud_provider", "unknown")),
    )

    print("\n" + "=" * 75)
    print(f"🚀 LAUNCHING PIPELINE: {pipeline_id.upper()}")
    print(f"🆔 PROJECT ID:         {unique_project_id}")
    print(f"☁️  CLOUD:              {pipe_conf.get('cloud_provider', '?').upper()}")
    print("=" * 75 + "\n")

    try:
        run_config = {
            "run_name": f"Run_{pipeline_id}_{timestamp}",
            "recursion_limit": 200,
            "configurable": {"thread_id": unique_project_id},
        }
        logger.info("Handing over to LangGraph Supervisor...")
        _consume_stream(app.stream(initial_state, config=run_config))
    except MissionFailedError as e:
        # The graph ENDED, but without a verified deployment — this is a mission
        # failure, not an engine crash. Exit 1 so the GitHub Action goes red; the
        # LangSmith root run already carries this error via stream.throw().
        logger.error(f"❌ MISSION FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        logger.critical(f"Workflow engine crashed: {str(e)}")
        sys.exit(1)

    logger.info("✅ MISSION VERIFIED — deployment completed and validated end-to-end.")


def run_from_natural_language(description: str):
    """
    Entry point for natural language pipeline creation.
    Parses the description, generates a full config bundle, and launches.
    """
    print("\n" + "=" * 75)
    print("🧠 NATURAL LANGUAGE MODE")
    print(f"📝 Input: {description[:100]}{'...' if len(description) > 100 else ''}")
    print("=" * 75)
    print("Parsing intent...\n")

    try:
        pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task = \
            build_pipeline_bundle_from_nl(description)
    except Exception as e:
        logger.error(f"NL parsing failed: {e}")
        sys.exit(1)

    logger.info(
        f"Parsed: pipeline_id={pipeline_id}, "
        f"cloud={pipe_conf.get('cloud_provider')}, "
        f"db={db_conf.get('db_type')}, "
        f"rules={len(rules_conf.get('quality_standards', []))}"
    )

    _launch(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task)


def run_self_healing_system(pipeline_name: str):
    """
    Main entry point to run the self-healing data engineering system.
    Dynamically loads the full configuration bundle and initiates the LangGraph workflow.
    """

    pipeline_dir = os.path.join(CONFIGS_DIR, "pipelines")
    spec_file = os.path.join(pipeline_dir, f"{pipeline_name}_pipeline.yaml")
    objective_file = os.path.join(pipeline_dir, f"{pipeline_name}_objective.md")

    logger.info(f"Initializing pipeline: {pipeline_name}")

    if not os.path.exists(spec_file) or not os.path.exists(objective_file):
        logger.error(
            f"Configuration files missing for '{pipeline_name}'. "
            f"Expected: {spec_file} and {objective_file}"
        )
        sys.exit(1)

    try:
        pipe_conf, db_conf, rules_conf, infra_conf = load_pipeline_bundle(os.getcwd(), spec_file)
        pipeline_id = pipe_conf.get("pipeline_id", pipeline_name)
        logger.info(f"Configuration bundle loaded for {pipeline_id}.")
    except Exception as e:
        logger.error(f"Failed to load configuration bundle: {str(e)}")
        sys.exit(1)

    # Build a temporary project_id for placeholder resolution in the objective template
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    temp_project_id = f"{pipeline_name.upper()}-{timestamp}"

    pipeline_objective = read_file(objective_file)
    try:
        task = format_prompt(
            pipeline_objective,
            project_id=temp_project_id,
            infra_standards=infra_conf,
            **pipe_conf,
        )
    except Exception as e:
        logger.error(f"Objective formatting failed: {str(e)}")
        sys.exit(1)

    _launch(pipe_conf, db_conf, rules_conf, infra_conf, pipeline_id, task)

if __name__ == "__main__":
    from agents.llm_factory import _infer_provider
    _model    = os.getenv("LLM_MODEL", "gpt-4o")
    _provider = (os.getenv("LLM_PROVIDER") or _infer_provider(_model)).lower()
    if _provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        logger.error("OPENAI_API_KEY not set. Add it to .env (local) or GitHub Secrets (CI).")
        sys.exit(1)
    if _provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set. Add it to .env (local) or GitHub Secrets (CI).")
        sys.exit(1)
    # vertexai relies on GOOGLE_APPLICATION_CREDENTIALS — validated by the SDK at call time

    if len(sys.argv) < 2:
        print("⚠️  Usage:")
        print("  YAML mode : python main.py eu_sales")
        print('  NL mode   : python main.py "I need daily sales data from PostgreSQL to GCP"')
        sys.exit(1)

    user_input = " ".join(sys.argv[1:])  # supports multi-word args without quotes

    if _is_natural_language(user_input):
        run_from_natural_language(user_input)
    else:
        run_self_healing_system(user_input)
