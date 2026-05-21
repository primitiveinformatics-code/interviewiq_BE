# deploy-ec2.ps1 — Push code to GitHub then rebuild EC2 backend container
# Usage: .\deploy-ec2.ps1
#        .\deploy-ec2.ps1 -SkipGitPush    (used by deploy-all.ps1)
#        .\deploy-ec2.ps1 -SkipCleanup    (skip disk/Docker cleanup step)

param(
    [switch]$SkipGitPush,
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

$KEY_PATH = "$HOME\interviewiq-key.pem"
$EC2_HOST = "ubuntu@3.215.251.179"

if (-not $SkipGitPush) {
    if (git status --porcelain) {
        Write-Host "ERROR: You have uncommitted changes. Commit them first, then re-run."
        exit 1
    }
    Write-Host "==> Pushing to GitHub..."
    git push origin main
}

$CLEANUP_BLOCK = ''
if (-not $SkipCleanup) {
    $CLEANUP_BLOCK = @'

echo "--- space check (before cleanup) ---"
du -sh /var/lib/docker/* 2>/dev/null || true
du -sh /home/ubuntu/* 2>/dev/null || true
echo "--- docker system prune (stopped containers, networks, volumes, build cache) ---"
docker system prune --volumes -f
echo "--- docker image prune (all unused images) ---"
docker image prune -a -f
echo "--- pip cache purge ---"
pip cache purge 2>/dev/null || true
echo "--- apt cache clean ---"
sudo apt-get clean
'@
}

$REMOTE_CMD = @"
set -e
cd ~/interviewiq_BE
$CLEANUP_BLOCK
echo "--- git pull ---"
git pull origin main
echo "--- docker build ---"
docker build -f Dockerfile.backend -t interviewiq-backend .
echo "--- restart container ---"
docker stop interviewiq-backend 2>/dev/null || true
docker rm interviewiq-backend 2>/dev/null || true
docker run -d --name interviewiq-backend --env-file .env --restart unless-stopped -p 8000:8000 interviewiq-backend
sleep 3
echo "--- health check ---"
curl -sf http://localhost:8000/health && echo " Backend OK" || (echo " Backend health check FAILED"; exit 1)
"@

Write-Host "==> Deploying to EC2 (git pull + docker rebuild)..."
ssh -i $KEY_PATH $EC2_HOST $REMOTE_CMD

Write-Host "==> EC2 deploy complete."
