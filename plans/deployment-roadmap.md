# Argus Deployment Roadmap & Cost-Optimization Guide

This roadmap outlines the steps to deploy the hybrid **Argus** system. The core application runs on an AWS Free Tier instance, and camera feeds are pushed from your local network using a secondary laptop.

---

## ── Architectural Design: Hybrid Deployment Model ──

To keep cloud costs at **$0** while maintaining 24/7 security scanning, we are using a **hybrid setup**:

1. **AWS EC2 Instance (Runs 24/7 on Free Tier)**
   * Hosts the Postgres database, Next.js frontend, MediaMTX server, and the YOLO event-detection backend.
   * By using the AWS Free Tier (750 hours/month of a `t2.micro` or `t3.micro`), this runs 24/7 completely free for 12 months.
2. **Local Secondary Laptop (Runs 24/7 in your home network)**
   * Runs the PowerShell relay script **[relay-cam1.ps1](file:///C:/dev/argus/scripts/relay-cam1.ps1)**.
   * Captures the local Godrej LAN camera feed and uses `ffmpeg` to push it to the MediaMTX server running on AWS EC2.

---

## ── Phase 1: Launching the AWS EC2 Instance ──

Follow these steps to spin up your new instance under the AWS Free Tier:

### 1. Launch Instance
1. Log in to your new AWS Console.
2. Search for **EC2** and click **Launch Instance**.
3. **AMI (OS)**: Select **Ubuntu Server 24.04 LTS** (marked *"Free tier eligible"*).
4. **Instance Type**: Select **t2.micro** (or **t3.micro** if it is free-tier eligible in your region).
5. **Key Pair**: Select **"Proceed without a key pair"** (since we will upload the key we generated locally via User Data).

### 2. Configure Security Group (Firewall)
You need to open the following ports in the Security Group settings:

| Port | Protocol | Source | Description |
|---|---|---|---|
| **22** | TCP | `My IP` or `0.0.0.0/0` | SSH Access |
| **80** | TCP | `0.0.0.0/0` | HTTP Web traffic |
| **443** | TCP | `0.0.0.0/0` | HTTPS Web traffic |
| **3001** | TCP | `0.0.0.0/0` | Next.js Frontend |
| **8554** | TCP | `0.0.0.0/0` | RTSP Camera Stream Input (from Laptop) |
| **8889** | TCP | `0.0.0.0/0` | WebRTC Browser Player Output |

---

## ── Phase 2: Inserting the SSH Public Key ──

To make sure you can log in immediately using the new SSH key we generated (`C:\Users\aakas\.ssh\argus_key`), follow these steps:

1. Scroll down to the **Advanced Details** section on the EC2 Launch page.
2. In the **User Data** field, paste the following script:
   ```yaml
   Content-Type: multipart/mixed; boundary="//"
   MIME-Version: 1.0

   --//
   Content-Type: text/cloud-config; charset="us-ascii"
   MIME-Version: 1.0
   Content-Transfer-Encoding: 7bit
   Content-Disposition: attachment; filename="cloud-config.txt"

   #cloud-config
   cloud_final_modules:
   - [users-groups, once]

   users:
     - name: ubuntu
       ssh_authorized_keys:
         - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDqAx9eZo8tf5bZ+T6qUovPRrTz3e9qAFpC31rGpopWpQtksO5NbcduoHqqN8U2+bZ7JI3Ir2Bp+duE2EbngK1Fraj5dx5pzIAGpV4Xwq5oVKwToCkQuwpnJ8SCs8Nh1id7a/jt2CU03RlxHVWmmRAm3aL4o60PCUyQJfLW31KqWIJ8Cqkzd2usxBmFXSLRwEanLuac0Zxh45RmcA1BiyVYX2B3gLGISuOy9hYvhIDpjmBUbq2w2LqTJ4e0DzqkInJTDkwR0+somIYDh3OzpCm1SoQ89l5cBo/qbOPY/nn4aIfzA6hzyTf/GDaI2mY3CjjJgpmZnrVL80AjGlLiQDjgUNIDfbEyf6o/LuUhq1KbHv9Ie2jdTTnD9qsrz5oAgx8lNc/AAwbLf5ORV9/7EImb++j7d99oAFim9+CZ4kZc8lwYFp4AHZWzYLlgplIZBnNV3f99J3dyUE3VqNRdc/dT4DUcR3y9+ZmCmq7I3J2Yx9RFqOsva89BM8GIXzV69DRA9Vg6F2pweyladHJ4T4NaFh0TslqFQQJnjg639IVHUU67bRX0J84QmcMlC8a1TDiuZvSpVparAwc9i3dlz+dMla8FfjxtSuc4UFqGgGNCIdgdsHVK8MkrZYv7iV1lDwr4YwvVI4JpHotOOVfm0O8WVy3/UumMQ8+lwL8kiC3ZbQ== aakas@Aakash
   ```
3. Click **Launch Instance**.
4. Once the instance transitions to `Running`, test your login from your local Windows command line:
   ```powershell
   ssh -i C:\Users\aakas\.ssh\argus_key ubuntu@<EC2-PUBLIC-IP>
   ```

---

## ── Phase 3: Deploying the Application on EC2 ──

Once you are logged into your EC2 instance via SSH:

### 1. Install Docker & Git
Run the following commands on your EC2 instance to set up Docker:
```bash
# Update package index
sudo apt update && sudo apt upgrade -y

# Install Docker
sudo apt install -y docker.io docker-compose-v2 git

# Add your user to the docker group so you don't need 'sudo' for docker commands
sudo usermod -aG docker $USER

# Log out and log back in for docker group settings to apply
exit
```
*Log back in using SSH.*

### 2. Clone and Launch the Project
```bash
# Clone the repository
git clone <your-git-repo-url> argus
cd argus

# Build and start services in the background
docker compose up -d --build
```

### 3. Verification Checks
Ensure everything is running correctly:
```bash
# Check service statuses
docker compose ps

# Check the YOLO backend logs for any runtime errors
docker compose logs backend

# Verify frontend accessibility
curl -I http://localhost:3001
```

---

## ── Phase 4: Setting up the Local Relay ──

On your secondary laptop, we will configure the **[relay-cam1.ps1](file:///C:/dev/argus/scripts/relay-cam1.ps1)** script to push the camera feed to AWS:

### 1. Install FFmpeg
Make sure FFmpeg is installed on the laptop. You can install it using PowerShell:
```powershell
winget install Gyan.FFmpeg
```
*(Restart PowerShell after installation for PATH changes to apply).*

### 2. Run the Relay Script
Run the script, passing the new EC2 Public IP address to the `-Dest` parameter:
```powershell
# Change directory to the repository
cd C:\dev\argus

# Run the relay with your new EC2 Public IP address
.\scripts\relay-cam1.ps1 -Dest "rtsp://<YOUR-NEW-EC2-IP>:8554/live/cam1"
```

### 3. Monitor Relay Logs
The script will print out status updates and auto-reconnect if connection drops. You can view logs in:
`C:\dev\argus\logs\relay-cam1_*.log`
