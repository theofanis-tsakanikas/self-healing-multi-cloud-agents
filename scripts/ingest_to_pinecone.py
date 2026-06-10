import os
import yaml
import argparse
import logging
from pathlib import Path
from glob import glob
from pinecone import Pinecone
from openai import OpenAI
from dotenv import load_dotenv

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
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "data-fabric-knowledge")

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
        embedding = get_embedding(raw_content)

        if embedding:
            metadata = {
                "source": str(file_path),
                "category": category,
                "content": raw_content,
                "project_id": project_id,
                "type": "standard", # Label used by the Medic
                "last_updated": "2026-04-23" # Optional timestamp
            }

            # 3. UPSERT TO ENGINEERING-STANDARDS
            index.upsert(
                vectors=[(vector_id, embedding, metadata)],
                namespace="engineering-standards"
            )
            logger.info(f"✅ Ingested: {file_name} -> [engineering-standards / {category}]")
        else:
            logger.warning(f"⚠️ Failed to generate embedding for {file_name}")

    except Exception as e:
        logger.error(f"Failed to process file {file_path}: {e}")

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

    count = 0
    for file_path in files:
        upload_file_to_pinecone(file_path)
        count += 1

    logger.info(f"✨ Process completed. Total files synced: {count}")

if __name__ == "__main__":
    main()
