# Persistent storage for the SQLite file. The service runs a single task, so
# there is only ever one writer; SQLite stays in its default rollback-journal
# mode (WAL does not work over NFS).

resource "aws_efs_file_system" "data" {
  creation_token = "${var.project}-data"
  encrypted      = true

  tags = {
    Name = "${var.project}-data"
  }
}

resource "aws_efs_mount_target" "data" {
  count = length(aws_subnet.public)

  file_system_id  = aws_efs_file_system.data.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.efs.id]
}

# Exposes /data on the file system as the container's /data, owned by the
# container's non-root user (uid/gid 1000, see Dockerfile).
resource "aws_efs_access_point" "data" {
  file_system_id = aws_efs_file_system.data.id

  posix_user {
    uid = 1000
    gid = 1000
  }

  root_directory {
    path = "/data"

    creation_info {
      owner_uid   = 1000
      owner_gid   = 1000
      permissions = "0755"
    }
  }

  tags = {
    Name = "${var.project}-data"
  }
}

# Once a file system policy exists, EFS no longer allows every client that
# can reach it; access then has to be granted (here: the task role, iam.tf).
resource "aws_efs_file_system_policy" "data" {
  file_system_id = aws_efs_file_system.data.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyUnencryptedTransport"
      Effect    = "Deny"
      Principal = { AWS = "*" }
      Action    = "*"
      Resource  = aws_efs_file_system.data.arn
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

# Daily AWS Backup of the file system (35-day retention, AWS default plan).
resource "aws_efs_backup_policy" "data" {
  file_system_id = aws_efs_file_system.data.id

  backup_policy {
    status = "ENABLED"
  }
}
