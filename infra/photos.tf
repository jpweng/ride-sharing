# Profile photos (backend/photos.py, docs/profiles.md): one private bucket.
# Photos have no public address: the app reads them with the instance role
# and serves them only to those who may see the profile.
#
# The app uses the bucket once the repository variable PHOTO_BUCKET is set
# (the github_actions_variables output has its name); until then the site
# offers no photos.

# Versioning stays off, on purpose: a photo a user removes (or whose account
# is deleted) must be gone, not kept as an old version.
resource "aws_s3_bucket" "photos" {
  # Bucket names are global, hence the account id.
  bucket = "${var.project}-photos-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "photos" {
  bucket = aws_s3_bucket.photos.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "photos" {
  bucket = aws_s3_bucket.photos.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "photos" {
  bucket = aws_s3_bucket.photos.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "photos_bucket" {
  statement {
    sid       = "TlsOnly"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.photos.arn, "${aws_s3_bucket.photos.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "photos" {
  bucket = aws_s3_bucket.photos.id
  policy = data.aws_iam_policy_document.photos_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.photos]
}

# The app container uses the instance role (server.tf, IMDS hop limit 2).
data "aws_iam_policy_document" "photos" {
  statement {
    sid       = "ProfilePhotos"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.photos.arn}/profiles/*"]
  }

  # Without it, reading a photo that isn't there answers "access denied"
  # instead of "not found".
  statement {
    sid       = "TellMissingFromForbidden"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.photos.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["profiles/*"]
    }
  }
}

resource "aws_iam_role_policy" "photos" {
  name   = "profile-photos"
  role   = aws_iam_role.app.id
  policy = data.aws_iam_policy_document.photos.json
}
