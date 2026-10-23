# One-time bootstrap: the S3 bucket that stores Terraform state for infra/terraform.
# Run once with local state (this folder's own terraform.tfstate stays on your machine):
#   terraform init && terraform apply -var="bucket_name=connoisseur-tfstate-<your-account-id>"

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "bucket_name" {
  description = "Globally unique bucket name, e.g. connoisseur-tfstate-123456789012"
  type        = string
}

variable "region" {
  type    = string
  default = "us-east-1"
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "connoisseur-companion", ManagedBy = "terraform", Stack = "bootstrap" }
  }
}

resource "aws_s3_bucket" "state" {
  #checkov:skip=CKV_AWS_145:SSE-S3 (AES256) is enough for state; a KMS key would add $1/month
  #checkov:skip=CKV_AWS_144:Single-region demo; versioning covers recovery
  #checkov:skip=CKV_AWS_18:Access logs for a private state bucket would need a second bucket
  #checkov:skip=CKV2_AWS_62:No consumer for bucket event notifications
  bucket = var.bucket_name
  # Protects the state of the whole deployment from an accidental `terraform destroy` here
  lifecycle {
    prevent_destroy = true
  }
}

# Every apply writes a new state version: keep old ones to recover from mistakes
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.state.bucket
}
