# deploy-lambda.ps1 — Push code to GitHub then rebuild ECR image and update Lambda functions
# Usage: .\deploy-lambda.ps1
#        .\deploy-lambda.ps1 -SkipGitPush    (used by deploy-all.ps1)
#        .\deploy-lambda.ps1 -SkipCleanup    (skip local Docker and ECR stale-image cleanup)

param(
    [switch]$SkipGitPush,
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"

$ECR_ACCOUNT = "918349931006"
$ECR_REPO    = "interviewiq-lambda"
$ECR_URI     = "${ECR_ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/${ECR_REPO}"
$REGION      = "us-east-1"
$FUNCTIONS   = @("interviewiq-audio", "interviewiq-report", "interviewiq-corpus")

if (-not $SkipGitPush) {
    if (git status --porcelain) {
        Write-Host "ERROR: You have uncommitted changes. Commit them first, then re-run."
        exit 1
    }
    Write-Host "==> Pushing to GitHub..."
    git push origin main
}

if (-not $SkipCleanup) {
    Write-Host "==> Cleaning up local Docker images and build cache..."
    # Remove old local lambda images; suppress output/errors if they don't exist
    docker rmi interviewiq-lambda:latest 2>&1 | Out-Null
    docker rmi "${ECR_URI}:latest" 2>&1 | Out-Null
    # Remove dangling images and build cache
    docker image prune -f
    docker builder prune -f
}

Write-Host "==> Authenticating Docker to ECR..."
aws ecr get-login-password --region $REGION |
    docker login --username AWS --password-stdin "${ECR_ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com"

Write-Host "==> Building Lambda container image (Dockerfile.lambda)..."
docker build -f Dockerfile.lambda -t interviewiq-lambda .

Write-Host "==> Tagging and pushing to ECR..."
docker tag interviewiq-lambda:latest "${ECR_URI}:latest"
docker push "${ECR_URI}:latest"

if (-not $SkipCleanup) {
    Write-Host "==> Cleaning up untagged ECR images (stale from previous pushes)..."
    $UNTAGGED_JSON = aws ecr list-images `
        --repository-name $ECR_REPO `
        --region $REGION `
        --filter tagStatus=UNTAGGED `
        --query 'imageIds[*]' `
        --output json
    $UNTAGGED = $UNTAGGED_JSON | ConvertFrom-Json
    if ($UNTAGGED.Count -gt 0) {
        Write-Host "    Deleting $($UNTAGGED.Count) untagged ECR image(s)..."
        aws ecr batch-delete-image `
            --repository-name $ECR_REPO `
            --region $REGION `
            --image-ids $UNTAGGED_JSON | Out-Null
        Write-Host "    ECR cleanup done."
    } else {
        Write-Host "    No untagged ECR images found."
    }
}

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
