#!/usr/bin/env bash
# =============================================================================
# test-upload.sh — Upload test video to the raw-videos S3 bucket and monitor
# =============================================================================
set -e

STACK_NAME="${1:-media-intelligence}"
REGION="${2:-$(aws configure get region || echo 'us-east-1')}"

# Get bucket name from CloudFormation stack output
RAW_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs[?OutputKey==`RawVideosBucket`].OutputValue' \
  --output text)

if [[ -z "$RAW_BUCKET" || "$RAW_BUCKET" == "None" ]]; then
  echo "❌ Could not find RawVideosBucket output from stack '$STACK_NAME'"
  echo "   Run: aws cloudformation deploy --stack-name $STACK_NAME --template-file cfn/pipeline.yaml --parameter-overrides NotificationEmail=you@example.com"
  exit 1
fi

echo "📦 Raw bucket: $RAW_BUCKET"
echo "📤 Uploading test videos..."

# Upload each test video
for video in src/test/*.mp4; do
  if [[ -f "$video" ]]; then
    filename=$(basename "$video")
    echo "   → Uploading $filename..."
    aws s3 cp "$video" "s3://$RAW_BUCKET/raw-videos/$filename" \
      --region "$REGION"
    echo "   ✓ Uploaded: $filename"
  fi
done

echo ""
echo "✅ All test videos uploaded to s3://$RAW_BUCKET/raw-videos/"
echo ""
echo "⏳ What to expect:"
echo "   1. MediaConvert job starts within ~10 seconds"
echo "   2. Transcoding completes in 2-5 minutes (depending on video length)"
echo "   3. EventBridge fires on completion → Process Lambda starts"
echo "   4. Rekognition + Transcribe run for 3-8 minutes"
echo "   5. Email notification arrives at your registered address"
echo ""
echo "🔍 Monitor progress:"
echo "   Lambda logs:  aws logs tail /aws/lambda/$STACK_NAME-process --region $REGION --since 5m --follow"
echo "   MediaConvert: aws mediaconvert get-job --region $REGION --id <job-id>"
echo "   S3 bucket:    aws s3 ls s3://$RAW_BUCKET/processed/ --recursive --region $REGION"
