# Generated-infrastructure security rules — an INDEPENDENT re-implementation of the HIGH rules in
# policy/security_analyzer.py, expressed in Rego for Open Policy Agent / Conftest.
#
# Why two engines for the same rules? Defence in depth + a portability proof: the deterministic Python
# analyzer is the source of truth and the CI gate, and this Rego policy independently re-derives the
# same HIGH violations from the analyzer's own normalized context. When both agree the generated
# bundle is clean, that is two engines confirming it — and it shows the rules are expressible in the
# industry-standard policy language, so they could run at admission-control / gateway time too.
#
# HONEST LIMIT (same as the governance platform's): both engines read the SAME upstream fact
# extraction (policy/security_analyzer.extract_context). A bug there would feed both identically. This
# is a rule-logic cross-check, not a second independent extraction pipeline.
#
# Input: the `context` object emitted by `analyze(...)["context"]`.
# Run:   conftest test <context>.json --policy policy/opa

package main

import rego.v1

deny contains msg if {
	some d in input.dockerfiles
	not d.has_nonroot_user
	msg := sprintf("DOCKERFILE_ROOT_USER: %s has no non-root USER directive", [d.path])
}

deny contains msg if {
	some d in input.dockerfiles
	d.copies_env
	msg := sprintf("DOCKERFILE_COPIES_ENV: %s copies a .env / secrets file", [d.path])
}

deny contains msg if {
	some p in input.pods
	p.inline_secret
	msg := sprintf("K8S_INLINE_SECRET: %s:%s has a credential env with an inline literal value", [p.manifest, p.kind])
}

deny contains msg if {
	some i in input.images
	i.latest
	not i.private
	msg := sprintf("IMAGE_PUBLIC_LATEST: %s:%s uses a public image on :latest (%s)", [i.manifest, i.container, i.ref])
}

deny contains msg if {
	some w in input.workflows
	count(w.inline_secret_keys) > 0
	msg := sprintf("WORKFLOW_INLINE_SECRET: %s hardcodes secret literals %v", [w.path, w.inline_secret_keys])
}
