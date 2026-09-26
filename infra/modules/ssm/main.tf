locals {
  # DESIGN.md 8.2. Parameter values are constrained by allowedPattern/allowedValues, so the
  # {{ }} substitution below can only ever produce the listed strings. The body reads them as
  # SHA, MODE, ROTATE, WAIT_SECONDS.
  specs = {
    "jarvis-deploy" = {
      description = "Deploy a release by git sha (runs /opt/jarvis/bin/jarvis-deploy)"
      timeout     = 1800
      parameters = {
        Sha = { type = "String", description = "40-character git sha", allowedPattern = "^[0-9a-f]{40}$" }
      }
      env = ["export SHA='{{ Sha }}'"]
    }
    "jarvis-restart" = {
      description = "Restart one mode daemon, optionally rotating its API token"
      timeout     = 300
      parameters = {
        Mode   = { type = "String", description = "Mode to restart", allowedValues = ["work", "personal"] }
        Rotate = { type = "String", description = "Rotate the API token first", allowedValues = ["false", "true"], default = "false" }
      }
      env = ["export MODE='{{ Mode }}'", "export ROTATE='{{ Rotate }}'"]
    }
    "jarvis-secrets-sync" = {
      description = "Sync Secrets Manager values to the mode env files"
      timeout     = 300
      parameters  = {}
      env         = []
    }
    "jarvis-status" = {
      description = "Unit states, heartbeat age, outbox counts, disk, Tailscale state"
      timeout     = 300
      parameters  = {}
      env         = []
    }
    # PLAN AD40. The step timeout covers the longest allowed wait (WaitSeconds up to 9999)
    # plus the unit stop and start.
    "jarvis-ofw-login" = {
      description = "Headed OFW login for ofw-mcp: stop the unit, wait for the human, save the session, start the unit"
      timeout     = 10800
      parameters = {
        WaitSeconds = { type = "String", description = "Seconds to wait for the human to log in", allowedPattern = "^[0-9]{1,4}$", default = "900" }
      }
      env = ["export WAIT_SECONDS='{{ WaitSeconds }}'"]
    }
    "jarvis-ofw-reset" = {
      description = "Close the ofw-mcp login breaker (removes breaker.json as jarvis-ofw)"
      timeout     = 300
      parameters  = {}
      env         = []
    }
  }

  # A leading shebang in the body is replaced by ours so the parameter exports come first.
  bodies = { for name, body in var.documents : name => split("\n", trimsuffix(replace(body, "/^#![^\\n]*\\n/", ""), "\n")) }

  command_documents = { for name, spec in local.specs : name => jsonencode(merge(
    {
      schemaVersion = "2.2"
      description   = spec.description
      mainSteps = [{
        action = "aws:runShellScript"
        name   = replace(name, "-", "_")
        inputs = {
          timeoutSeconds = tostring(spec.timeout)
          runCommand     = concat(["#!/usr/bin/env bash"], spec.env, local.bodies[name])
        }
      }]
    },
    length(spec.parameters) > 0 ? { parameters = spec.parameters } : {}
  )) }

  # Session preferences: no S3 or CloudWatch session logging (rule 0.4), runAs disabled.
  session_preferences = jsonencode({
    schemaVersion = "1.0"
    description   = "Jarvis Session Manager preferences"
    sessionType   = "Standard_Stream"
    inputs = {
      s3BucketName                = ""
      s3KeyPrefix                 = ""
      s3EncryptionEnabled         = true
      cloudWatchLogGroupName      = ""
      cloudWatchEncryptionEnabled = true
      cloudWatchStreamingEnabled  = false
      kmsKeyId                    = ""
      runAsEnabled                = false
      runAsDefaultUser            = ""
      idleSessionTimeout          = tostring(var.idle_session_timeout_minutes)
      shellProfile = {
        linux   = ""
        windows = ""
      }
    }
  })

  # Every document body, keyed by document name (the source scan requires content to come from here).
  documents = merge(local.command_documents, { "SSM-SessionManagerRunShell" = local.session_preferences })
}

resource "aws_ssm_document" "command" {
  for_each = local.specs

  name            = each.key
  document_type   = "Command"
  document_format = "JSON"
  content         = local.documents[each.key]
}

resource "aws_ssm_document" "session_preferences" {
  name            = "SSM-SessionManagerRunShell"
  document_type   = "Session"
  document_format = "JSON"
  content         = local.documents["SSM-SessionManagerRunShell"]
}
