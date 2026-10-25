terraform {
  required_version = ">= 1.10" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: the bucket name lives in backend.hcl (see backend.hcl.example)
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project     = "connoisseur-companion"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
