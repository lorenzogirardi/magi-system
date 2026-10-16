variable "region" {
  description = "AWS Region with Amazon Bedrock AgentCore Runtime and Code Interpreter."
  type        = string
}

variable "name" {
  description = "Name prefix for every resource (AgentCore names allow letters, digits and underscores only)."
  type        = string
  default     = "magi"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,15}$", var.name))
    error_message = "name must be 2-16 lowercase letters/digits, starting with a letter."
  }
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}

variable "vpc_name" {
  description = "Name tag of the dedicated VPC; subnets are named subnet-<tier>-00<n>-<vpc_name>."
  type        = string
  default     = "vpc-lab-ai-001"
}

variable "vpc_cidr" {
  description = "CIDR of the dedicated VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "nat_enabled" {
  description = "Create the NAT gateway and its Elastic IP, the only hourly-billed resources. false: the runtime has no egress (see deploy/agentcore/shutdown.sh)."
  type        = bool
  default     = true
}

variable "az_count" {
  description = "Availability Zones used (subnets and EFS mount targets). Must be zones supported by AgentCore in the Region."
  type        = number
  default     = 2
}

variable "image_tag" {
  description = "Tag of the magi-agentcore image in the ECR repository created here (build and push it before apply)."
  type        = string
}

variable "posix_uid" {
  description = "POSIX uid/gid of the runtime image user and of every EFS access point (Dockerfile: 1000)."
  type        = number
  default     = 1000
}

variable "max_session_seconds" {
  description = "AgentCore Runtime session max lifetime (microVM limit: 8 h)."
  type        = number
  default     = 28800
}

variable "checkpoint_ttl_days" {
  description = "Days after which LangGraph checkpoints expire (DynamoDB TTL + S3 lifecycle, set by the saver); 0 disables. Run records are kept."
  type        = number
  default     = 30
}

variable "run_settings_file" {
  description = "Path inside the image of the run settings YAML (budgets, OpenRouter models). Empty: callers send `settings` per run."
  type        = string
  default     = ""
}

variable "ci_toolchain_path" {
  description = "PATH for target commands inside the Code Interpreter (e.g. a toolchain installed on EFS under the run mount)."
  type        = string
  default     = "/usr/local/bin:/usr/bin:/bin"
}
