# --- Variables ---
PYTHON = uv run python
APP = main.py
SCRIPTS_DIR = scripts

# --- Commands ---

.PHONY: bootstrap-aws
bootstrap-aws: ## Provision baseline AWS infrastructure (EKS + RDS + S3 + ECR)
	@echo "Provisioning AWS baseline infrastructure..."
	terraform -chdir=bootstrap/aws init
	terraform -chdir=bootstrap/aws apply
	@echo "Exporting bootstrap outputs -> .bootstrap_outputs.json (bridge for the NL/Streamlit deploy path)..."
	$(PYTHON) scripts/export_bootstrap_outputs.py aws

.PHONY: bootstrap-azure
bootstrap-azure: ## Provision baseline Azure infrastructure (AKS + PostgreSQL + ACR)
	@echo "Provisioning Azure baseline infrastructure..."
	terraform -chdir=bootstrap/azure init
	terraform -chdir=bootstrap/azure apply
	@echo "Exporting bootstrap outputs -> .bootstrap_outputs.json (bridge for the NL/Streamlit deploy path)..."
	$(PYTHON) scripts/export_bootstrap_outputs.py azure

.PHONY: bootstrap-gcp
bootstrap-gcp: ## Provision baseline GCP infrastructure (GKE + Cloud SQL + AR)
	@echo "Provisioning GCP baseline infrastructure..."
	terraform -chdir=bootstrap/gcp init
	terraform -chdir=bootstrap/gcp apply
	@echo "Exporting bootstrap outputs -> .bootstrap_outputs.json (bridge for the NL/Streamlit deploy path)..."
	$(PYTHON) scripts/export_bootstrap_outputs.py gcp

.PHONY: bootstrap-databricks
bootstrap-databricks: ## Provision baseline Databricks infrastructure (workspace + Unity Catalog + jobs cluster + SQL warehouse + source RDS)
	@echo "Provisioning Databricks baseline infrastructure..."
	terraform -chdir=bootstrap/databricks init
	terraform -chdir=bootstrap/databricks apply

.PHONY: destroy-aws
destroy-aws: ## DANGER: Tear down all baseline AWS infrastructure
	@echo "Destroying baseline AWS infrastructure..."
	terraform -chdir=bootstrap/aws init
	terraform -chdir=bootstrap/aws destroy

.PHONY: destroy-azure
destroy-azure: ## DANGER: Tear down all baseline Azure infrastructure
	@echo "Destroying baseline Azure infrastructure..."
	terraform -chdir=bootstrap/azure init
	terraform -chdir=bootstrap/azure destroy

.PHONY: destroy-gcp
destroy-gcp: ## DANGER: Tear down all baseline GCP infrastructure
	@echo "Destroying baseline GCP infrastructure..."
	terraform -chdir=bootstrap/gcp init
	terraform -chdir=bootstrap/gcp destroy

.PHONY: destroy-databricks
destroy-databricks: ## DANGER: Databricks teardown — use the destroy.yml workflow (two-phase: force_destroy/force_update flags, then destroy)
	@echo "Databricks teardown is a TWO-PHASE process (runtime-created managed tables need"
	@echo "force_destroy/force_update flags applied into state before terraform destroy)."
	@echo "Use the supported path: GitHub Actions → destroy.yml → cloud: databricks"
	@exit 1

## Single source of truth: pyproject.toml
## Before running: cp .env.example .env  (then fill in your credentials)
.PHONY: azure-pause
azure-pause: ## Stop AKS + Postgres to cut cost between sessions (keeps everything)
	@bash bootstrap/azure/power.sh pause

.PHONY: azure-resume
azure-resume: ## Start AKS + Postgres to resume work
	@bash bootstrap/azure/power.sh resume

.PHONY: azure-status
azure-status: ## Show Azure baseline power state (AKS + Postgres)
	@bash bootstrap/azure/power.sh status

.PHONY: aws-pause
aws-pause: ## Stop RDS + EKS nodes to cut cost between sessions (keeps everything)
	@bash bootstrap/aws/power.sh pause

.PHONY: aws-resume
aws-resume: ## Start RDS + EKS nodes to resume work
	@bash bootstrap/aws/power.sh resume

.PHONY: aws-status
aws-status: ## Show AWS baseline power state (RDS + EKS node groups)
	@bash bootstrap/aws/power.sh status

.PHONY: gcp-pause
gcp-pause: ## Stop Cloud SQL + scale GKE workloads to 0 to cut cost (keeps everything)
	@bash bootstrap/gcp/power.sh pause

.PHONY: gcp-resume
gcp-resume: ## Start Cloud SQL + scale GKE workloads back up
	@bash bootstrap/gcp/power.sh resume

.PHONY: gcp-status
gcp-status: ## Show GCP baseline power state (Cloud SQL + GKE workloads)
	@bash bootstrap/gcp/power.sh status

.PHONY: install
install: ## Install all dependencies using uv
	@echo "Installing dependencies..."
	uv sync

.PHONY: ingest
ingest: ## Sync Knowledge Base standards (all 4 providers) to Pinecone
	@echo "Syncing Global Standards to Pinecone..."
	$(PYTHON) $(SCRIPTS_DIR)/ingest_to_pinecone.py --path knowledge_base

.PHONY: chaos
chaos: ## Seed dirty data. Usage: make chaos target=eu_sales db_type=postgres rows=100
	@$(if $(target),,$(error Error: target is undefined. Usage: make chaos target=eu_sales))
	@echo "Seeding chaos into $(target) (db_type=$(db_type))..."
	$(PYTHON) $(SCRIPTS_DIR)/seed_chaos.py --target $(target) --db-type $(if $(db_type),$(db_type),postgres) --rows $(if $(rows),$(rows),100)

.PHONY: run
run: ## Run the Self-Healing Agent. Usage: make run p=eu_sales
	@$(if $(p),,$(error Error: p is undefined. Usage: make run p=eu_sales))
	@echo "Starting Multi-Cloud Self-Healing Agent for: $(p)..."
	$(PYTHON) $(APP) $(p)

.PHONY: run-all
run-all: ## Run all four pipelines sequentially (AWS, Azure, GCP, Databricks)
	@make run p=eu_sales
	@make run p=us_crm
	@make run p=global_marketing
	@make run p=sales_lakehouse

.PHONY: demo-aws
demo-aws: ## Full AWS demo: ingest + chaos + run eu_sales
	@make ingest
	@make chaos target=eu_sales db_type=postgres rows=100
	@make run p=eu_sales

.PHONY: demo-azure
demo-azure: ## Full Azure demo: ingest + chaos + run us_crm
	@make ingest
	@make chaos target=us_crm db_type=postgres rows=100
	@make run p=us_crm

.PHONY: demo-gcp
demo-gcp: ## Full GCP demo: ingest + chaos + run global_marketing
	@make ingest
	@make chaos target=global_marketing db_type=mysql rows=100
	@make run p=global_marketing

.PHONY: demo-databricks
demo-databricks: ## Full Databricks demo: ingest + chaos + run sales_lakehouse
	@make ingest
	@make chaos target=sales_lakehouse db_type=postgres rows=100
	@make run p=sales_lakehouse

.PHONY: test
test: ## Run the full test suite
	uv run pytest tests/ -v --tb=short

.PHONY: format
format: ## Autofix lint findings with ruff (generated pipe_*.py excluded via pyproject)
	@echo "Fixing lint findings..."
	uv run ruff check --fix .

.PHONY: lint
lint: ## Lint the whole repo with ruff (same command CI runs)
	uv run ruff check .

.PHONY: clean
clean: ## Remove temporary files and caches
	@echo "Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .uv_cache pipeline_execution.log

.PHONY: help
help: ## Display this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
