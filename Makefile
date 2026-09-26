# Jarvis Makefile. Sections are owned by different builders; add targets inside your own section.

SHELL := /bin/bash

# ---------------------------------------------------------------------------------------------
# Infra (terraform-engineer). These targets never apply: plans stop at plan.out / plan.txt and
# the human applies (brief rule 0.1). Profiles are set here, never in Terraform code (AD21).
# ---------------------------------------------------------------------------------------------

TF               ?= terraform
TFLINT           := $(shell command -v tflint 2>/dev/null || echo $(HOME)/.local/bin/tflint)
INFRA            := infra
TF_ROOTS         := bootstrap envs/prod org
PROD_AWS_PROFILE ?= jarvis-prod
ORG_AWS_PROFILE  ?= sparko

.PHONY: tf-check tf-fmt tf-validate tf-lint tf-scan tf-test plan plan-org plan-bootstrap

tf-check: tf-fmt tf-validate tf-lint tf-scan tf-test ## fmt, validate, tflint, trivy, terraform test
	@echo "tf-check: all passed"

tf-fmt:
	$(TF) fmt -check -recursive $(INFRA)

# macOS make 3.81 ignores .SHELLFLAGS, so multi-command recipes set -e themselves.
tf-validate:
	@set -euo pipefail; for d in $(TF_ROOTS); do \
	  echo "== validate $$d"; \
	  $(TF) -chdir=$(INFRA)/$$d init -backend=false -input=false -no-color >/dev/null; \
	  $(TF) -chdir=$(INFRA)/$$d validate -no-color; \
	done

tf-lint:
	@test -x "$(TFLINT)" || { echo "tflint not found (expected on PATH or at ~/.local/bin/tflint)"; exit 1; }
	$(TFLINT) --init --config=$(CURDIR)/$(INFRA)/.tflint.hcl
	$(TFLINT) --chdir=$(INFRA) --recursive --config=$(CURDIR)/$(INFRA)/.tflint.hcl

tf-scan:
	trivy config $(INFRA) --severity HIGH,CRITICAL --exit-code 1 --quiet

tf-test:
	$(TF) -chdir=$(INFRA) init -input=false -no-color >/dev/null
	$(TF) -chdir=$(INFRA) test -no-color

# Real plans (need credentials). Output: <root>/plan.out and <root>/plan.txt.
plan: ## envs/prod plan (AWS_PROFILE=$(PROD_AWS_PROFILE)); needs infra/envs/prod/backend.hcl
	@test -f $(INFRA)/envs/prod/backend.hcl || { echo "missing infra/envs/prod/backend.hcl (copy backend.hcl.example)"; exit 1; }
	AWS_PROFILE=$(PROD_AWS_PROFILE) $(TF) -chdir=$(INFRA)/envs/prod init -input=false -reconfigure -backend-config=backend.hcl
	AWS_PROFILE=$(PROD_AWS_PROFILE) $(TF) -chdir=$(INFRA)/envs/prod plan -input=false -out plan.out
	$(TF) -chdir=$(INFRA)/envs/prod show -no-color plan.out > $(INFRA)/envs/prod/plan.txt
	@echo "Review $(INFRA)/envs/prod/plan.txt. The human applies; this target never does."

plan-org: ## org/ plan in the management account (AWS_PROFILE=$(ORG_AWS_PROFILE)); local state (AD36)
	AWS_PROFILE=$(ORG_AWS_PROFILE) $(TF) -chdir=$(INFRA)/org init -input=false -reconfigure
	AWS_PROFILE=$(ORG_AWS_PROFILE) $(TF) -chdir=$(INFRA)/org plan -input=false -out plan.out
	$(TF) -chdir=$(INFRA)/org show -no-color plan.out > $(INFRA)/org/plan.txt
	@echo "Review $(INFRA)/org/plan.txt. The human applies; this target never does."

plan-bootstrap: ## bootstrap/ plan (local state, AWS_PROFILE=$(PROD_AWS_PROFILE))
	AWS_PROFILE=$(PROD_AWS_PROFILE) $(TF) -chdir=$(INFRA)/bootstrap init -input=false
	AWS_PROFILE=$(PROD_AWS_PROFILE) $(TF) -chdir=$(INFRA)/bootstrap plan -input=false -out plan.out
	$(TF) -chdir=$(INFRA)/bootstrap show -no-color plan.out > $(INFRA)/bootstrap/plan.txt
	@echo "Review $(INFRA)/bootstrap/plan.txt. The human applies; this target never does."

# ---------------------------------------------------------------------------------------------
# End of infra section.
# ---------------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------------
# Deploy (deploy-engineer). Release packaging, SSM-driven deploy/rollback, status, restart,
# secrets sync, vault template sync, and OAuth login. Never runs `terraform apply`; never calls
# `aws ssm send-command`/`start-session` against anything but the four jarvis-* documents and the
# AWS-owned AWS-StartPortForwardingSession/AWS-StartInteractiveCommand ones used by
# oauth-login.sh and sync-agents.sh (neither script uses AWS-RunShellScript/send-command at all --
# JarvisOperator does not grant it). Every aws call is pinned to --region $(AWS_REGION)
# (region-deny SCP, PLAN.md 3.2).
#
# IAM reality: JarvisOperator (day-to-day deploys) cannot read the Terraform state bucket, so
# every target here uses OPERATOR_AWS_PROFILE (default jarvis-operator) and resolves the instance
# id/artifacts bucket without `terraform output`. PROD_AWS_PROFILE (infra section, above) stays
# reserved for the `plan`/`plan-bootstrap` targets, which do need state-bucket/Terraform-admin
# access.
# ---------------------------------------------------------------------------------------------

AWS                  ?= aws
AWS_REGION           ?= us-east-1
OPERATOR_AWS_PROFILE ?= jarvis-operator
ARTIFACTS_BUCKET     ?=
MAKE_CACHE_DIR       := .make
INSTANCE_ID_FILE     := $(MAKE_CACHE_DIR)/instance_id
MODE                 ?=
SHA                  ?=
ROTATE               ?= false

.PHONY: release deploy status restart secrets-sync sync-agents oauth-login test clean

release: ## build + upload a release tarball; ARTIFACTS_BUCKET, or the jarvis-artifacts-<account-id> convention
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) ARTIFACTS_BUCKET=$(ARTIFACTS_BUCKET) AWS_REGION=$(AWS_REGION) \
	  scripts/release.sh

$(INSTANCE_ID_FILE): ## the running jarvis instance, found by tag (no terraform/state-bucket access needed)
	@mkdir -p $(MAKE_CACHE_DIR)
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) $(AWS) ec2 describe-instances --region $(AWS_REGION) \
	  --filters Name=tag:app,Values=jarvis Name=instance-state-name,Values=running \
	  --query 'Reservations[].Instances[].InstanceId' --output text > $@.tmp
	@count="$$(wc -w < $@.tmp | tr -d '[:space:]')"; \
	  if [ "$$count" != "1" ]; then \
	    echo "expected exactly 1 running jarvis instance (tag app=jarvis, region $(AWS_REGION)), found $$count: $$(cat $@.tmp)"; \
	    rm -f $@.tmp; exit 1; \
	  fi
	@mv $@.tmp $@

deploy: $(INSTANCE_ID_FILE) ## make deploy SHA=<short-or-full sha>; resolved locally to the full 40-char sha (refused if unknown); writes releases/DEPLOYED on success
	@test -n "$(SHA)" || { echo "usage: make deploy SHA=<sha>"; exit 1; }
	@set -euo pipefail; \
	  resolved="$$(git rev-parse --verify "$(SHA)^{commit}" 2>/dev/null)"; \
	  test -n "$$resolved" || { echo "SHA=$(SHA) is not a known commit in this repo"; exit 1; }; \
	  echo "resolved $(SHA) -> $$resolved"; \
	  AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) \
	    scripts/ssm-run.sh jarvis-deploy "$$(cat $(INSTANCE_ID_FILE))" --parameters Sha=$$resolved; \
	  bucket="$(ARTIFACTS_BUCKET)"; \
	  if [ -z "$$bucket" ]; then \
	    bucket="jarvis-artifacts-$$(AWS_PROFILE=$(OPERATOR_AWS_PROFILE) $(AWS) sts get-caller-identity --query Account --output text --region $(AWS_REGION))"; \
	  fi; \
	  printf '%s' "$$resolved" | AWS_PROFILE=$(OPERATOR_AWS_PROFILE) $(AWS) s3 cp - "s3://$$bucket/releases/DEPLOYED" --region $(AWS_REGION) --only-show-errors; \
	  echo "wrote s3://$$bucket/releases/DEPLOYED = $$resolved (ops/aws/lib/first-deploy.sh reads this at boot)"

status: $(INSTANCE_ID_FILE) ## service states, heartbeat, outbox counts, disk, tailscale
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) \
	  scripts/ssm-run.sh jarvis-status "$$(cat $(INSTANCE_ID_FILE))"

restart: $(INSTANCE_ID_FILE) ## make restart MODE=work|personal [ROTATE=true]
	@test -n "$(MODE)" || { echo "usage: make restart MODE=work|personal"; exit 1; }
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) \
	  scripts/ssm-run.sh jarvis-restart "$$(cat $(INSTANCE_ID_FILE))" --parameters Mode=$(MODE),Rotate=$(ROTATE)

secrets-sync: $(INSTANCE_ID_FILE) ## re-sync Secrets Manager values into the per-mode env files
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) \
	  scripts/ssm-run.sh jarvis-secrets-sync "$$(cat $(INSTANCE_ID_FILE))"

sync-agents: $(INSTANCE_ID_FILE) ## make sync-agents MODE=work|personal; diffs, confirms, applies (interactive SSM session, nothing stored)
	@test -n "$(MODE)" || { echo "usage: make sync-agents MODE=work|personal"; exit 1; }
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) \
	  scripts/sync-agents.sh "$(MODE)" "$$(cat $(INSTANCE_ID_FILE))"

oauth-login: $(INSTANCE_ID_FILE) ## make oauth-login MODE=work|personal
	@test -n "$(MODE)" || { echo "usage: make oauth-login MODE=work|personal"; exit 1; }
	AWS_PROFILE=$(OPERATOR_AWS_PROFILE) AWS_REGION=$(AWS_REGION) JARVIS_INSTANCE_ID_FILE=$(INSTANCE_ID_FILE) \
	  scripts/oauth-login.sh "$(MODE)"

test: ## whole repo suite (tests/ and windows_client/tests/, per pytest.ini)
	.venv/bin/pytest -q

clean: ## remove the cached instance id (never touches AWS)
	rm -rf $(MAKE_CACHE_DIR)

# ---------------------------------------------------------------------------------------------
# End of deploy section.
# ---------------------------------------------------------------------------------------------
