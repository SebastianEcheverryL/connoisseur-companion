# Bedrock Guardrails replace Llama Guard + Prompt Guard on AWS (GUARDRAIL_PROVIDER=bedrock).
# The app calls ApplyGuardrail on the user message (INPUT) and on the answer (OUTPUT).
# Billed per text unit (1,000 characters) evaluated, only when called: $0 when idle.

variable "name" {
  type = string
}

resource "aws_bedrock_guardrail" "this" {
  name                      = var.name
  description               = "Input and output checks for the Connoisseur Companion agents"
  blocked_input_messaging   = "Sorry, I can't help with that request. / Lo siento, no puedo ayudar con esa solicitud."
  blocked_outputs_messaging = "Sorry, I can't share that answer. / Lo siento, no puedo compartir esa respuesta."

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }
    # Jailbreaks and prompt injection in the user's message (applies to input only)
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  # Restaurant phone numbers and addresses are legitimate answers: only block data that is never needed
  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = ["CREDIT_DEBIT_CARD_NUMBER", "US_SOCIAL_SECURITY_NUMBER", "PASSWORD", "AWS_ACCESS_KEY", "AWS_SECRET_KEY"]
      content {
        type   = pii_entities_config.value
        action = "BLOCK"
      }
    }
  }

  word_policy_config {
    managed_word_lists_config {
      type = "PROFANITY"
    }
  }
}

# A numbered, immutable version: the app pins it instead of using the editable DRAFT
resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Managed by Terraform"
}

output "id" {
  value = aws_bedrock_guardrail.this.guardrail_id
}

output "arn" {
  value = aws_bedrock_guardrail.this.guardrail_arn
}

output "version" {
  value = aws_bedrock_guardrail_version.this.version
}
