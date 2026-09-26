# Used by `make tf-check` (tflint --recursive --config=$(abspath infra/.tflint.hcl)).
config {
  call_module_type = "local"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

# Beyond "recommended". terraform_standard_module_structure stays off: infra/ is an empty root
# used only by terraform test.
rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true
}

rule "terraform_comment_syntax" {
  enabled = true
}

rule "terraform_unused_required_providers" {
  enabled = true
}

plugin "aws" {
  enabled = true
  version = "0.40.0"
  source  = "github.com/terraform-linters/tflint-ruleset-aws"
}
