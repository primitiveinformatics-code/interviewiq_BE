# deploy-all.ps1 — Push code to GitHub then deploy to both EC2 and Lambda
# Usage: .\deploy-all.ps1                 (deploys everything)
#        .\deploy-all.ps1 -EC2Only        (git push + EC2 only)
#        .\deploy-all.ps1 -LambdaOnly     (git push + Lambda only)
#        .\deploy-all.ps1 -SkipCleanup    (skip disk/Docker cleanup on EC2)

param(
    [switch]$EC2Only,
    [switch]$LambdaOnly,
    [switch]$SkipCleanup
)

$ErrorActionPreference = "Stop"
$SCRIPT_DIR = $PSScriptRoot

if (git status --porcelain) {
    Write-Host "ERROR: You have uncommitted changes. Commit them first, then re-run."
    exit 1
}

Write-Host "==> Pushing to GitHub..."
git push origin main

if (-not $LambdaOnly) {
    Write-Host ""
    Write-Host "====== EC2 Deploy ======"
    $ec2Args = @("-SkipGitPush")
    if ($SkipCleanup) { $ec2Args += "-SkipCleanup" }
    & "$SCRIPT_DIR\deploy-ec2.ps1" @ec2Args
}

if (-not $EC2Only) {
    Write-Host ""
    Write-Host "====== Lambda Deploy ======"
    $lambdaArgs = @("-SkipGitPush")
    if ($SkipCleanup) { $lambdaArgs += "-SkipCleanup" }
    & "$SCRIPT_DIR\deploy-lambda.ps1" @lambdaArgs
}

Write-Host ""
Write-Host "==> All deployments complete."
