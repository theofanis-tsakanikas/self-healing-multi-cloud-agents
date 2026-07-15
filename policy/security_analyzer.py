"""Security gate over the agent's GENERATED infrastructure — the deterministic source of truth.

The agent autonomously writes Dockerfiles, Kubernetes manifests and CI workflows and ships them to
production. This module is a holistic, policy-as-code gate over that generated bundle: it extracts a
normalized security *context* (facts) and derives HIGH violations from it. It is the analog of the
`policy_analyzer.py` in the governance platform, and it is mirrored by `policy/opa/generated_infra.rego`
(a second, independent rule engine — defence in depth + a portability proof that the rules can run at
admission-control time too).

Scope note (honest, like the governance platform's): the fact extraction below is shared upstream, so
a bug in extraction would feed both the Python rules and the Rego rules identically. This is a
rule-logic cross-check, not two independent pipelines. It is deliberately scoped to artifacts the
project already treats as security-relevant and for which a clean golden exists (Dockerfile, K8s,
workflows); securityContext/runAsNonRoot is surfaced as ADVISORY, not a denial, because enforcing it
needs per-image numeric UIDs validated on a live cluster (see SECURITY.md, deliberate trade-off #3).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from agents.contracts import RESOLVE_FROM_TF
except Exception:  # keep policy usable even if agents isn't importable
    RESOLVE_FROM_TF = "RESOLVE_FROM_EXECUTE_TERRAFORM_OUTPUT"

# Private registries whose `:latest` is acceptable (the project pushes an immutable :sha alongside).
_PRIVATE_REGISTRY = (".pkg.dev", ".dkr.ecr.", ".azurecr.io", ".azurecr.us")
# Env-var name fragments that mark a credential; an inline literal value for one is a plaintext leak.
_SECRETISH = ("password", "secret", "token", "access_key", "api_key", "private_key")


@dataclass
class Finding:
    rule: str
    severity: str  # "HIGH" (denies) | "ADVISORY" (reported, never denies)
    object: str
    detail: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "object": self.object, "detail": self.detail}


@dataclass
class Context:
    """Normalized security facts extracted from a generated-artifact directory."""

    dockerfiles: list[dict] = field(default_factory=list)
    pods: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    workflows: list[dict] = field(default_factory=list)
    terraform: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "dockerfiles": self.dockerfiles,
            "pods": self.pods,
            "images": self.images,
            "workflows": self.workflows,
            "terraform": self.terraform,
        }


# --------------------------------------------------------------------------- extraction (facts) --- #

def _image_is_private(ref: str) -> bool:
    host = ref.split("/", 1)[0]
    return any(m in host for m in _PRIVATE_REGISTRY)


def _image_is_latest(ref: str) -> bool:
    tag = ref.rsplit(":", 1)[1] if ":" in ref.rsplit("/", 1)[-1] else "latest"
    return tag == "latest"


def _extract_dockerfile(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    has_nonroot_user = bool(re.search(r"(?im)^\s*USER\s+(?!root\s*$)\S+", text))
    copies_env = bool(re.search(r"(?im)^\s*COPY\b.*(?:^|[\s/])\.env\b", text))
    return {"path": path.name, "has_nonroot_user": has_nonroot_user, "copies_env": copies_env}


def _pod_spec_of(doc: dict) -> dict | None:
    if not isinstance(doc, dict):
        return None
    kind = doc.get("kind")
    if kind in ("Deployment", "Job", "StatefulSet", "DaemonSet"):
        return (doc.get("spec", {}) or {}).get("template", {}).get("spec")
    if kind == "Pod":
        return doc.get("spec")
    return None


def _extract_k8s(path: Path, ctx: Context) -> None:
    try:
        docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    except Exception:
        return
    for doc in docs:
        pod = _pod_spec_of(doc)
        if pod is None:
            continue
        containers = pod.get("containers", []) or []
        inline_secret = False
        for c in containers:
            ref = c.get("image", "")
            if ref:
                ctx.images.append(
                    {
                        "manifest": path.name,
                        "container": c.get("name", "?"),
                        "ref": ref,
                        "private": _image_is_private(ref),
                        "latest": _image_is_latest(ref),
                    }
                )
            for env in c.get("env", []) or []:
                name = str(env.get("name", "")).lower()
                if "value" in env and any(s in name for s in _SECRETISH):
                    inline_secret = True
        ctx.pods.append(
            {
                "manifest": path.name,
                "kind": doc.get("kind"),
                "has_service_account": bool(pod.get("serviceAccountName")),
                "runs_nonroot": _pod_runs_nonroot(pod, containers),
                "inline_secret": inline_secret,
            }
        )


def _pod_runs_nonroot(pod: dict, containers: list) -> bool:
    if (pod.get("securityContext") or {}).get("runAsNonRoot") is True:
        return True
    return all((c.get("securityContext") or {}).get("runAsNonRoot") is True for c in containers) if containers else False


def _extract_workflow(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    hits = []
    for ln in text.splitlines():
        m = re.match(r"\s*([A-Za-z0-9_]*(?:PASSWORD|SECRET|TOKEN|ACCESS_KEY|API_KEY)[A-Za-z0-9_]*)\s*:\s*(\S.*)$", ln)
        if not m:
            continue
        value = m.group(2).strip().strip("'\"")
        # A GitHub expression (${{ secrets.* }} / ${{ vars.* }}) or an empty value is fine; a literal is a leak.
        if value and not value.startswith("${{"):
            hits.append(m.group(1))
    return {"path": path.name, "inline_secret_keys": hits}


# Segments skipped ONLY when they appear in a path RELATIVE to the scan root — so `analyze(".")` over
# the live repo ignores the test fixtures/goldens and vendored dirs, while `analyze(fixtures/unsafe)`
# pointed straight at a fixture still scans it (the ignored segment isn't in its relative path).
# "bootstrap" is excluded: the gate governs the agent's GENERATED infra, not the human-authored
# one-time bootstrap, whose public DB endpoints / demo trade-offs are documented in SECURITY.md.
_IGNORE_SEGMENTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "fixtures", "goldens", "bootstrap"}
)


def extract_context(artifact_dir: str | Path) -> Context:
    root = Path(artifact_dir)
    ctx = Context()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if _IGNORE_SEGMENTS & set(p.relative_to(root).parts):
            continue
        name = p.name.lower()
        if name == "dockerfile":
            ctx.dockerfiles.append(_extract_dockerfile(p))
        elif p.suffix == ".tf":
            ctx.terraform.append(_extract_terraform(p))
        elif p.suffix in (".yaml", ".yml") and "workflow" in str(p.parent).lower():
            ctx.workflows.append(_extract_workflow(p))
        elif p.suffix in (".yaml", ".yml"):
            _extract_k8s(p, ctx)
    return ctx


def _extract_terraform(path: Path) -> dict:
    """Regex-scan generated Terraform for the clearest HIGH signals (no HCL parser dependency)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": path.name,
        "publicly_accessible": bool(re.search(r"(?im)^\s*publicly_accessible\s*=\s*true", text)),
        "iam_wildcard_resource": bool(re.search(r'(?i)"?Resource"?\s*[=:]\s*\[?\s*"\*"', text)),
        "open_ingress": bool(re.search(r'0\.0\.0\.0/0', text)),
        "public_acl": bool(re.search(r'(?i)acl\s*=\s*"public-read(-write)?"', text)),
    }


# ------------------------------------------------------------------------------- rules (Python) --- #

def derive_findings(ctx: Context) -> list[Finding]:
    """Python rule engine — the gate's source of truth. Mirrored by generated_infra.rego."""
    out: list[Finding] = []
    for d in ctx.dockerfiles:
        if not d["has_nonroot_user"]:
            out.append(Finding("DOCKERFILE_ROOT_USER", "HIGH", d["path"], "no non-root USER directive"))
        if d["copies_env"]:
            out.append(Finding("DOCKERFILE_COPIES_ENV", "HIGH", d["path"], "COPY of a .env / secrets file"))
    for pod in ctx.pods:
        obj = f"{pod['manifest']}:{pod['kind']}"
        # NOTE: serviceAccountName is intentionally NOT a HIGH rule here — the monitoring pods
        # (grafana/prometheus) legitimately need no cloud identity, and the pipeline Job's SA is
        # already enforced by validate_generated_code. Flagging every pod would false-positive.
        if pod["inline_secret"]:
            out.append(Finding("K8S_INLINE_SECRET", "HIGH", obj, "credential env with an inline literal value"))
        if not pod["runs_nonroot"]:
            out.append(Finding("POD_NOT_NONROOT", "ADVISORY", obj, "no runAsNonRoot (SECURITY.md trade-off #3)"))
    for img in ctx.images:
        # The pre-sed job.yaml image sentinel is a valid placeholder (the CI step rewrites it to the
        # real registry URL); it is NOT a public :latest image. validate_generated_code exempts it too.
        if img["ref"] == RESOLVE_FROM_TF:
            continue
        if img["latest"] and not img["private"]:
            out.append(
                Finding("IMAGE_PUBLIC_LATEST", "HIGH", f"{img['manifest']}:{img['container']}", f"public image on :latest ({img['ref']})")
            )
    for wf in ctx.workflows:
        if wf["inline_secret_keys"]:
            out.append(
                Finding("WORKFLOW_INLINE_SECRET", "HIGH", wf["path"], f"inline secret literals: {', '.join(wf['inline_secret_keys'])}")
            )
    for tf in ctx.terraform:
        if tf["publicly_accessible"]:
            out.append(Finding("TF_PUBLIC_DB", "HIGH", tf["path"], "publicly_accessible = true on a database"))
        if tf["iam_wildcard_resource"]:
            out.append(Finding("TF_IAM_WILDCARD_RESOURCE", "HIGH", tf["path"], 'IAM policy grants Resource = "*"'))
        if tf["open_ingress"]:
            out.append(Finding("TF_OPEN_INGRESS", "HIGH", tf["path"], "0.0.0.0/0 in generated Terraform"))
        if tf["public_acl"]:
            out.append(Finding("TF_PUBLIC_BUCKET_ACL", "HIGH", tf["path"], "public-read(-write) bucket ACL"))
    return out


def analyze(artifact_dir: str | Path) -> dict:
    ctx = extract_context(artifact_dir)
    findings = derive_findings(ctx)
    highs = [f for f in findings if f.severity == "HIGH"]
    return {
        "context": ctx.as_dict(),
        "findings": [f.as_dict() for f in findings],
        "high_count": len(highs),
        "passed": len(highs) == 0,
    }


def _main(argv: list[str]) -> int:
    import json

    target = argv[1] if len(argv) > 1 else "."
    result = analyze(target)
    for f in result["findings"]:
        marker = "✗" if f["severity"] == "HIGH" else "·"
        print(f"  {marker} [{f['severity']}] {f['rule']}: {f['object']} — {f['detail']}")
    if result["passed"]:
        print(f"\n✔ security gate PASSED — 0 HIGH findings over {target}")
        return 0
    print(f"\n✗ security gate FAILED — {result['high_count']} HIGH finding(s) over {target}")
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
