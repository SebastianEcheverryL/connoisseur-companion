# Private registry for the API image. ~$0.10 per GB-month of stored images.

variable "name" {
  type = string
}

variable "keep_images" {
  description = "How many tagged images to keep (older ones are deleted to save storage)"
  type        = number
  default     = 10
}

resource "aws_ecr_repository" "this" {
  #checkov:skip=CKV_AWS_136:AES256 encryption at rest; a customer KMS key would add cost and no benefit here
  name = var.name
  # Tags are git SHAs: an existing tag is never overwritten with different code
  image_tag_mutability = "IMMUTABLE"
  # `terraform destroy` also removes the images (otherwise it fails on a non-empty repository)
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true # basic scanning: free, reports OS package CVEs in the console
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Delete untagged layers after a day"
        selection    = { tagStatus = "untagged", countType = "sinceImagePushed", countUnit = "days", countNumber = 1 }
        action       = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the last ${var.keep_images} images"
        selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = var.keep_images }
        action       = { type = "expire" }
      },
    ]
  })
}

output "repository_url" {
  value = aws_ecr_repository.this.repository_url
}

output "arn" {
  value = aws_ecr_repository.this.arn
}
