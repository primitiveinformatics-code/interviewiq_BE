# deploy-lambda.ps1 — Push code to GitHub then rebuild ECR image and update Lambda functions
# Usage: .\deploy-lambda.ps1
#        .\deploy-lambda.ps1 -SkipGitPush   (used by deploy-all.ps1)

param([switch]$SkipGitPush)

$ErrorActionPreference = "Stop"

$ECR_URI   = "918349931006.dkr.ecr.us-east-1.amazonaws.com/interviewiq-lambda"
$REGION    = "us-east-1"
$FUNCTIONS = @("interviewiq-audio", "interviewiq-report", "interviewiq-corpus")

if (-not $SkipGitPush) {
    if (git status --porcelain) {
        Write-Host "ERROR: You have uncommitted changes. Commit them first, then re-run."
        exit 1
    }
    Write-Host "==> Pushing to GitHub..."
    git push origin main
}

Write-Host "==> Authenticating Docker to ECR..."
aws ecr get-login-password --region $REGION |
    docker login --username AWS --password-stdin 918349931006.dkr.ecr.us-east-1.amazonaws.com

Write-Host "==> Building Lambda container image (Dockerfile.lambda)..."
docker build -f Dockerfile.lambda -t interviewiq-lambda .

Write-Host "==> Tagging and pushing to ECR..."
docker tag interviewiq-lambda:latest "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

Write-Host "==> Updating Lambda functions..."
foreach ($fn in $FUNCTIONS) {
    Write-Host "    Updating $fn..."
    aws lambda update-function-code `
        --function-name $fn `
        --image-uri "${ECR_URI}:latest" `
        --region $REGION | Out-Null
}

Write-Host "==> Waiting for all functions to finish updating..."
foreach ($fn in $FUNCTIONS) {
    aws lambda wait function-updated --function-name $fn --region $REGION
    Write-Host "    $fn ready"
}

Write-Host "==> Lambda deploy complete. All 3 functions updated."
