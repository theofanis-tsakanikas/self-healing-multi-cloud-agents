import os
import sys
import datetime
import yaml
import argparse
import logging
from pathlib import Path
from glob import glob
from pinecone import Pinecone
from openai import OpenAI
from dotenv import load_dotenv

# Shared token-budget guard (utils is on sys.path via PYTHONPATH=/app in the image; for local runs
# the repo root is the CWD). Import defensively so the ingest still runs if utils is unavailable.
try:
    from utils.embedding_budget import EMBED_TOKEN_SAFE_CEILING, count_tokens
except Exception:  # pragma: no cover - fallback for odd sys.path setups
    EMBED_TOKEN_SAFE_CEILING = 8000

    def count_tokens(text):
        return (len(text) + 3) // 4

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- ENVIRONMENT LOADING ---
load_dotenv() # Load from .env if local

# --- CLIENT INITIALIZATION ---
PINECONE_KEY = os.getenv("PINECONE_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
# Default MUST match .env.example and agents/tools.py (store_architectural_insight) so an unset
# PINECONE_INDEX_NAME resolves to the same index everywhere — otherwise ingest and retrieval diverge.
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "unified-intelligence-fabric")

if not PINECONE_KEY or not OPENAI_KEY:
    logger.error("Missing API Keys! Ensure PINECONE_API_KEY and OPENAI_API_KEY are set.")
    raise ValueError("Environment variables for API keys are missing.")

try:
    pc = Pinecone(api_key=PINECONE_KEY)
    client = OpenAI(api_key=OPENAI_KEY)
    index = pc.Index(INDEX_NAME)
    logger.info(f"Connected to Pinecone index: {INDEX_NAME}")
except Exception as e:
    logger.error(f"Failed to initialize AI clients: {e}")
    raise

def get_embedding(text):
    """Generates embeddings using OpenAI's text-embedding-3-small."""
    try:
        response = client.embeddings.create(
            input=[text],
            model="text-embedding-3-small"
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Error generating embedding: {e}")
        return None

def upload_file_to_pinecone(file_path):
    """
    Parses standards and specs from the knowledge base and syncs them to Pinecone.
    """
    try:
        # 1. PATH ANALYSIS (Monorepo Aware)
        # Derive the category from the parent folder (e.g., infrastructure, engineering)
        path_obj = Path(file_path)
        category = path_obj.parent.name if path_obj.parent.name else "general"
        file_name = path_obj.name

        with open(file_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()

        # 2. METADATA EXTRACTION
        # For standards, project_id is 'global' because they apply to all projects.
        project_id = "global"

        # If this is a YAML file, try to detect whether it defines a specific project_id.
        if file_path.endswith(('.yaml', '.yml')):
            try:
                data = yaml.safe_load(raw_content)
                if isinstance(data, dict):
                    project_id = data.get("project_id", "global")
            except Exception:
                pass

        # Create a unique ID to avoid duplicates.
        vector_id = f"spec_{category}_{file_name}".replace(".", "_")

        # PRE-FLIGHT TOKEN GUARD — the embed model rejects inputs over its limit; enforce the SAFE
        # ceiling (tiktoken under-counts vs the API, so the raw 8191 is not a safe local check). Catch it
        # here and FAIL LOUD: previously the OpenAI error was swallowed into a None embedding and the
        # file was silently skipped, leaving a STALE version live in Pinecone. Never silent again.
        tokens = count_tokens(raw_content)
        if tokens > EMBED_TOKEN_SAFE_CEILING:
            logger.error(
                f"❌ {file_name} is {tokens} tokens > {EMBED_TOKEN_SAFE_CEILING} safe ceiling for the embed model. "
                f"Pinecone will serve the STALE version. Split this standard before re-ingesting."
            )
            return False

        embedding = get_embedding(raw_content)

        if embedding:
            metadata = {
                "source": str(file_path),
                "category": category,
                "content": raw_content,
                "project_id": project_id,
                "type": "standard", # Label used by the Medic
                # Real file mtime — a hardcoded date here silently lies about staleness.
                "last_updated": datetime.date.fromtimestamp(path_obj.stat().st_mtime).isoformat(),
            }

            # 3. UPSERT TO ENGINEERING-STANDARDS
            index.upsert(
                vectors=[(vector_id, embedding, metadata)],
                namespace="engineering-standards"
            )
            logger.info(f"✅ Ingested: {file_name} -> [engineering-standards / {category}]")
            return True
        else:
            logger.error(f"❌ Failed to generate embedding for {file_name}")
            return False

    except Exception as e:
        logger.error(f"Failed to process file {file_path}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Unified Intelligence Fabric - Knowledge Ingestor")

    # The default path is now knowledge_base.
    parser.add_argument(
        "--path",
        type=str,
        default="knowledge_base",
        help="Directory containing the knowledge base files"
    )

    args = parser.parse_args()
    kb_path = args.path

    # Search for Markdown and YAML files across the entire folder tree.
    search_patterns = [
        os.path.join(kb_path, "**/*.md"),
        os.path.join(kb_path, "**/*.yaml"),
        os.path.join(kb_path, "**/*.yml")
    ]

    files = []
    for pattern in search_patterns:
        files.extend(glob(pattern, recursive=True))

    if not files:
        logger.warning(f"No files found in path: {kb_path}. Check your directory structure.")
        return

    logger.info(f"🚀 Starting ingestion of {len(files)} files from {kb_path}...")

    synced, failed = 0, []
    for file_path in files:
        if upload_file_to_pinecone(file_path):
            synced += 1
        else:
            failed.append(file_path)

    logger.info(f"✨ Process completed. Synced {synced}/{len(files)} files.")
    if failed:
        # Fail LOUD so CI / the operator sees a partial sync instead of a green run that quietly
        # left standards stale in Pinecone.
        logger.error(f"❌ {len(failed)} file(s) FAILED to ingest: {', '.join(failed)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
