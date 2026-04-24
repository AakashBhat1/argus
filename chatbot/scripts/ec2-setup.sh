#!/usr/bin/env bash
# scripts/ec2-setup.sh
# One-time bootstrap for a fresh Ubuntu 22.04 EC2 instance.
# Run as the default user (ubuntu) immediately after first SSH login.
#
# Usage:
#   chmod +x ec2-setup.sh && sudo ./ec2-setup.sh

set -euo pipefail

echo "==> Updating system packages"
apt-get update -y && apt-get upgrade -y

# ── Swap (critical for t2.micro – adds 1 GB of virtual memory) ───────────────
echo "==> Configuring 1 GB swap file"
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  # Reduce swappiness so swap is used only under real memory pressure
  sysctl vm.swappiness=10
  echo 'vm.swappiness=10' >> /etc/sysctl.conf
  echo "    swap configured."
else
  echo "    swap already exists, skipping."
fi

# ── Docker ────────────────────────────────────────────────────────────────────
echo "==> Installing Docker"
apt-get install -y ca-certificates curl gnupg lsb-release

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add the default user to the docker group so sudo isn't needed
SUDO_USER="${SUDO_USER:-ubuntu}"
usermod -aG docker "$SUDO_USER"
systemctl enable --now docker

echo "==> Docker $(docker --version) installed."

# ── AWS CLI ───────────────────────────────────────────────────────────────────
echo "==> Installing AWS CLI v2"
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
apt-get install -y unzip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install
rm -rf /tmp/awscliv2.zip /tmp/aws

echo "==> AWS CLI $(aws --version) installed."

# ── App directory ─────────────────────────────────────────────────────────────
echo "==> Creating app directory at /opt/chatbot"
mkdir -p /opt/chatbot
chown "$SUDO_USER:$SUDO_USER" /opt/chatbot

echo ""
echo "============================================================"
echo " Setup complete. Next steps:"
echo "   1. Log out and back in (or run: newgrp docker)"
echo "   2. Copy your chatbot files to /opt/chatbot/"
echo "      e.g. scp -r chatbot/ ubuntu@<EC2_IP>:/opt/chatbot/"
echo "   3. cd /opt/chatbot && cp .env.example .env && nano .env"
echo "   4. docker compose up -d"
echo "   5. docker compose exec ollama ollama pull qwen2.5:0.5b"
echo "   6. docker compose exec chatbot python run_ingestion.py"
echo "============================================================"
