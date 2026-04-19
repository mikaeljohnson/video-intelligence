# AWS Media Intelligence Pipeline
## Production-Ready Prompt — Serverless Video/Audio Analysis at Scale

---

## The Prompt

Copy and paste the following into any AI assistant (Claude, GPT-4, Codex, Kiro, etc.) to generate a complete, deployable AWS Media Intelligence Pipeline:

---

```
You are a senior cloud solutions architect. Build a production-ready, serverless video and audio intelligence pipeline on AWS using Infrastructure as Code (CloudFormation or Terraform).

## Architecture Overview

The pipeline must handle the following end-to-end flow:
1. User uploads a video file (MP4, MOV, MKV, AVI, WebM) to an S3 "raw" bucket
2. An S3 PutObject event triggers a Lambda function
3. That Lambda calls AWS MediaConvert to create adaptive bitrate H.264/MP4 outputs at 1080p, 720p, and 480p
4. MediaConvert's completion event (via EventBridge) triggers a second Lambda
5. The second Lambda runs AWS Rekognition analysis: label detection, content moderation, and (optionally) celebrity recognition
6. The second Lambda generates a WebVTT (.vtt) subtitle file from the video's audio track using Amazon Transcribe
7. All outputs (transcoded videos, subtitles, moderation JSON report) are written to a "processed" S3 bucket
8. An SES email notification is sent to a configured recipient with a summary of outputs and any moderation flags
9. If SES is unavailable (or the account is in SES sandbox), an SNS notification is fired as fallback

## S3 Bucket Configuration

Create two S3 buckets:
- `{ProjectName}-raw-videos-{AWS::AccountId}-{AWS::Region}` — receives uploads, versioning enabled, server-side encryption (SSE-S3), lifecycle rule to transition raw files to S3 Intelligent-Tiering after 30 days
- `{ProjectName}-processed-{AWS::AccountId}-{AWS::Region}` — stores all outputs, versioning enabled, SSE-S3, public access blocked

Configure the raw bucket with an S3 event notification on the `s3:ObjectCreated:*` event, filtering on prefix `raw-videos/` and suffix `.mp4,.mov,.mkv,.avi,.webm`, targeting an SNS topic `VideoPipelineTriggerTopic`.

## Lambda Functions

### Function 1: `VideoPipeline-Trigger`
Runtime: Python 3.11
Memory: 512 MB
Timeout: 300 seconds (5 minutes)
Environment variables:
- `MEDIACONVERT_ENDPOINT` — the regional MediaConvert endpoint (e.g., https://abcd1234.mediaconvert.us-east-1.amazonaws.com)
- `MEDIACONVERT_ROLE_ARN` — ARN of the IAM role MediaConvert assumes
- `OUTPUT_BUCKET` — name of the processed bucket
- `SNS_TOPIC_ARN` — ARN of the trigger topic (for fan-out)

Handler: `src/lambda/trigger.handler`

**Behavior:**
1. On invocation, parse the S3 event to extract `bucket` and `key` (URL-encoded)
2. URL-decode the key to get the filename
3. Check object metadata for `x-amz-meta-processed: true` — if present, skip processing (idempotency guard)
4. Query MediaConvert using the regional endpoint to create a job with the following specifications:
   - Input: `s3://{raw_bucket}/raw-videos/{filename}`
   - Outputs: Three MP4 H.264 outputs
     - Output 1: 1920x1080, 5000 kbps, "hd" label
     - Output 2: 1280x720, 2500 kbps, "sd" label
     - Output 3: 854x480, 1000 kbps, "mobile" label
   - Output group: S3 output group pointing to `s3://{OUTPUT_BUCKET}/transcoded/{filename}/`
   - User metadata: include original filename and upload timestamp
5. Write a CloudWatch log entry with job ID and input file name
6. Return the MediaConvert job ID to the caller

### Function 2: `VideoPipeline-Process`
Runtime: Python 3.11
Memory: 1024 MB (Rekognition is memory-intensive)
Timeout: 900 seconds (15 minutes)
Environment variables:
- `REKOGNITION_ROLE_ARN` — ARN of the IAM role Rekognition assumes
- `TRANSCRIBE_ROLE_ARN` — ARN of the IAM role Transcribe assumes
- `OUTPUT_BUCKET` — name of the processed bucket
- `NOTIFICATION_EMAIL` — email address to send completion notifications to
- `SNS_TOPIC_ARN` — ARN of the fallback notification topic
- `ENABLE_CELEBRITY_RECOGNITION` — "true" or "false"
- `MODERATION_CONFIDENCE_THRESHOLD` — default "75.0"

Handler: `src/lambda/process.handler`

**Behavior:**

A. **Transcription (runs first, in parallel with start of analysis):**
1. Call `start_transcription_job` on Amazon Transcribe:
   - MediaFormat: mp4
   - LanguageCode: en-US
   - OutputBucketName: `{OUTPUT_BUCKET}`
   - OutputKey: `subtitles/{input_filename}/{input_filename}.vtt`
   - Settings: ShowSpeakerLabels=true, MaxSpeakerLabels=10
2. Poll `get_transcription_job` every 30 seconds until status is COMPLETED
3. Download the VTT file from S3 (Transcribe generates WebVTT natively)
4. Upload the VTT to `s3://{OUTPUT_BUCKET}/subtitles/{input_filename}/{input_filename}.vtt`

B. **Rekognition Analysis (runs in parallel with transcription):**
1. Call `start_label_detection` on Rekognition:
   - Video: {S3Bucket: OUTPUT_BUCKET, S3ObjectKey: transcoded/.../hd/file.mp4}
   - MinConfidence: 50
2. Call `start_content_moderation` on Rekognition:
   - Video: same as above
   - MinConfidence: float(os.environ['MODERATION_CONFIDENCE_THRESHOLD'])
3. If ENABLE_CELEBRITY_RECOGNITION == "true": call `start_celebrity_recognition`
4. Poll all jobs, polling interval 30 seconds, timeout 15 minutes
5. Collect all results into a structured JSON report

C. **Moderation Report Generation:**
Build a JSON report with the following schema:
```json
{
  "video_name": "onboarding-60s.mp4",
  "processed_at": "2026-04-19T12:00:00Z",
  "pipeline_version": "1.0.0",
  "transcoding": {
    "status": "COMPLETED",
    "outputs": [
      {"label": "hd", "resolution": "1920x1080", "path": "transcoded/onboarding-60s.mp4/hd/onboarding-60s.mp4"},
      {"label": "sd", "resolution": "1280x720", "path": "transcoded/onboarding-60s.mp4/sd/onboarding-60s.mp4"},
      {"label": "mobile", "resolution": "854x480", "path": "transcoded/onboarding-60s.mp4/mobile/onboarding-60s.mp4"}
    ]
  },
  "transcription": {
    "status": "COMPLETED",
    "vtt_path": "subtitles/onboarding-60s.mp4/onboarding-60s.vtt",
    "language": "en-US"
  },
  "labels": [
    {"name": "Person", "confidence": 97.3, "timestamps": [{"start": 0, "end": 60000}]},
    {"name": "Screen Display", "confidence": 88.1, "timestamps": [{"start": 14000, "end": 25000}]}
  ],
  "moderation": {
    "status": "COMPLETED",
    "flags": [
      {
        "label": "Explicit",
        "confidence": 94.2,
        "timestamp_ms": 14000,
        "severity": "HIGH"
      }
    ],
    "summary": "1 moderation flag(s) detected"
  },
  "celebrities": [],
  "cloudwatch_metrics": {
    "transcription_duration_seconds": 47,
    "rekognition_duration_seconds": 312,
    "total_processing_time_seconds": 359
  }
}
```
Write this JSON to `s3://{OUTPUT_BUCKET}/reports/{input_filename}/{input_filename}-report.json`

D. **Notification:**
1. Construct a formatted email (HTML) or SNS message with:
   - Filename and upload timestamp
   - List of all output files with direct S3 links (using the S3 console URL format)
   - Moderation flag table (if any flags with severity HIGH or MEDIUM)
   - VTT subtitle file link
2. Attempt SES send:
   - From: `no-reply@{project domain}` (or the verified sender address)
   - To: `{NOTIFICATION_EMAIL}`
   - Subject: `[✓ Ready] {filename} — {n} output(s) available`
   - Body: the HTML notification
3. If SES throws `MessageRejected` (sandbox mode), fall back to SNS publish

## IAM Roles

### MediaConvert Service Role
Trusts: `mediaconvert.amazonaws.com`
Permissions:
- `s3:GetObject` on the raw bucket
- `s3:PutObject` on the processed bucket (transcoded/ prefix)
- `s3:ListBucket` on both buckets

### Rekognition/Transcribe Role
Trusts: `lambda.amazonaws.com`
Permissions:
- `s3:GetObject` on the processed bucket (read Rekognition results)
- `s3:PutObject` on the processed bucket (write reports, subtitles)
- `rekognition:GetLabelDetection`, `rekognition:GetContentModeration`, `rekognition:GetCelebrityRecognition`
- `transcribe:GetTranscriptionJob`, `transcribe:StartTranscriptionJob`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

### Lambda Execution Role
Trusts: `lambda.amazonaws.com`
Permissions:
- `s3:GetObject`, `s3:PutObject` on both buckets
- `mediaconvert:CreateJob`, `mediaconvert:GetJob`
- `rekognition:StartLabelDetection`, `rekognition:GetLabelDetection`, `rekognition:StartContentModeration`, `rekognition:GetContentModeration`, `rekognition:StartCelebrityRecognition`, `rekognition:GetCelebrityRecognition`
- `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`
- `ses:SendEmail` (restrict to specific from/to addresses using condition)
- `sns:Publish`
- `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents`

## EventBridge Rule (MediaConvert Completion)

Create an EventBridge rule:
- Event pattern: `{ "source": ["aws.mediaconvert"], "detailType": ["MediaConvert Job State Change"] }`
- Target: Lambda function `VideoPipeline-Process`
- Input transformer to extract `detail.jobId`, `detail.status`, `detail.outputGroupDetails[0].outputDetails[0].outputFilePaths[0]`

## SES Configuration

- If in SES sandbox (default for new accounts): document that the user must request production access via AWS console, or use SNS fallback during development
- Configure the sender email as verified (or in the SES sandbox recipient list)
- Enable SPF, DKIM, and DMARC records in Route 53 if a custom domain is used

## CloudWatch Alarms

Create the following alarms:
1. `MediaIntelligence-Pipeline-Error-Alarm` — Lambda function error rate > 0 for 1 minute, 1 evaluation period
2. `MediaIntelligence-MediaConvert-Lag` — MediaConvert job remains in PROGRESSING for > 30 minutes
3. `MediaIntelligence-Rekognition-Lag` — Rekognition job remains in IN_PROGRESS for > 20 minutes
4. `MediaIntelligence-SES-Failure` — SES bounce or complaint rate > 0.01
5. `MediaIntelligence-Bucket-Size` — processed bucket size > 100 GB (notify to review lifecycle policy)

All alarms should publish to an SNS topic `PipelineAlertsTopic`.

## Cost Optimization Settings

- MediaConvert: use `QualityTuningLevel: MULTI_PASS_HQ` (not single-pass) with explicit bitrate caps. Set `MAX_BITRATE` to prevent runaway quality settings
- S3: enable Intelligent-Tiering on both buckets. Set lifecycle rules to transition raw files to Glacier after 90 days and delete processed files after 365 days (configurable)
- Lambda: set reserved concurrency to 5 on both functions to prevent runaway parallel invocations from consuming all available concurrency
- Rekognition: process the 720p output (not 1080p) to reduce per-frame analysis cost by ~40%
- CloudWatch Logs: set retention to 14 days on both Lambda function log groups

## File Size Guardrails

- Maximum supported file size: 8 GB (Rekognition limit)
- Lambda pre-check: if file size > 8 GB, write error to CloudWatch and skip processing with a descriptive message: "File {filename} exceeds 8GB Rekognition limit. Transcoding only — skipping analysis."
- Provide a separate Lambda (or step in the prompt) that handles files > 8GB by transcoding without Rekognition/Transcribe, then notifying the user to process those files manually or split them.

## Idempotency

- On S3 upload, check for `x-amz-meta-processed: true` in object metadata
- If already processed, log a warning and exit without re-triggering
- MediaConvert job ID should be stored in object metadata post-processing to prevent double-processing on retries

## Troubleshooting Guide (include in documentation)

### Problem: Video uploads but no MediaConvert job starts
- Check: S3 event notification is configured and enabled
- Check: Lambda trigger function has permission `s3:ListBucket` on the raw bucket
- Check: MediaConvert endpoint URL is correct for the region (must use the regional endpoint, not the global one)
- Check: Lambda function is not hitting concurrent execution limits

### Problem: MediaConvert job stays in PROGRESSING forever
- Cause: Output S3 bucket policy may block MediaConvert service role write access
- Fix: Ensure the MediaConvert service role has `s3:PutObject` on the processed bucket with the `transcoded/` prefix
- Check: CloudWatch Logs for MediaConvert-specific errors

### Problem: Rekognition returns no labels
- Cause: Processing the 1080p output when Rekognition has issues with specific codec configurations
- Fix: Switch to process the 720p (sd) output instead — add this as a configurable parameter

### Problem: SES email never arrives
- Cause: Account is in SES sandbox mode — only verified addresses can receive
- Fix: Add recipient to SES sandbox verified emails list, OR switch notification mode to SNS for development
- Production: Request SES production access before launch

### Problem: Transcribe VTT has no timestamps
- Cause: Transcribe generates VTT with speaker timestamps by default, not scene-level
- Fix: Post-process the VTT file to add scene change detection via MediaConvert Analyze API (optional enhancement)

### Problem: Moderation flags are empty on a video that clearly has issues
- Cause: Rekognition ContentModeration detects based on trained classifiers — not all "issues" are moderation violations
- Fix: For general label detection of problematic content, use `StartLabelDetection` in addition to ContentModeration

## Testing the Pipeline

Provide a CloudFormation template that deploys the entire pipeline. Also include a `test-upload.sh` script:
```bash
#!/bin/bash
aws s3 cp test-videos/onboarding-60s.mp4 s3://{RawBucket}/raw-videos/
echo "Upload complete. Check your email for the notification."
```

And a `test-status.py` script that:
1. Lists MediaConvert jobs in the account
2. Checks status of the most recent job
3. Polls Rekognition and Transcribe job status
4. Prints CloudWatch logs tail for both Lambda functions

## WAF Alignment

- **Operational Excellence:** S3 event-driven processing, Lambda auto-scaling, CloudWatch alarms, structured JSON logging
- **Security:** S3 server-side encryption, IAM least-privilege roles, SES sender restrictions, no public bucket access
- **Reliability:** Idempotency guards, DLQ via failed Lambda invocations, SES → SNS fallback
- **Performance Efficiency:** Adaptive bitrate ladder, Rekognition processing on 720p (not 1080p), Lambda provisioned concurrency option
- **Cost Optimization:** Intelligent-Tiering, lifecycle policies, reserved Lambda concurrency, MediaConvert bitrate caps, CloudWatch log retention
- **Sustainability:** S3 lifecycle to Glacier reduces active storage footprint; Lambda is serverless and event-driven (no idle compute)

## Prerequisites for the Developer

1. AWS account with billing enabled
2. AWS CLI configured (`aws configure`)
3. Appropriate IAM permissions to create: Lambda functions, S3 buckets, MediaConvert jobs, Rekognition/Transcribe operations, SES (or SNS topic)
4. For email notifications: a verified SES sender address OR SNS topic ARN
5. Python 3.11+ and `requests` library for Lambda runtime
6. `aws-sam-cli` or `aws cloudformation` CLI for deployment
```

---

## Supplementary Documentation

### Prerequisites
- AWS Account (root or IAM user with AdministratorAccess)
- AWS CLI v2 configured with `aws configure`
- Python 3.11+ and `boto3` for Lambda function authoring
- (Optional) `aws-sam-cli` for streamlined deployment

### Use Case
Content teams producing video at scale — compliance training, marketing, internal communications — who need automated transcoding, captioning, and content moderation without managing infrastructure. Replaces manual post-production workflows that take hours, reducing processing to minutes of pipeline time plus human review of flagged content only.

### Expected Outcome
After running this prompt and deploying the CloudFormation template, a developer uploads a video to the `raw-videos/` S3 prefix and receives:
- 3 transcoded H.264 MP4 outputs (1080p, 720p, 480p)
- A WebVTT subtitles file
- A structured JSON moderation report with timestamped flags
- An email notification with direct links to all artifacts

Total elapsed time: typically 3-8 minutes per video depending on length and queue depth.

### Troubleshooting Tips
See the Troubleshooting Guide embedded in the prompt above. Key escalation path:
1. Check Lambda CloudWatch logs first — all function output is structured JSON with job IDs
2. Verify MediaConvert service role has correct S3 bucket policy
3. Check SES sandbox status for email delivery issues
4. For Rekognition timeout: verify video is < 8GB and use 720p output for analysis

### AWS Services Used
- Amazon S3 (storage, event notifications)
- AWS Lambda (compute orchestration)
- AWS MediaConvert (video transcoding)
- Amazon Rekognition (video analysis, moderation)
- Amazon Transcribe (speech-to-text, VTT generation)
- Amazon SES / SNS (notifications)
- Amazon EventBridge (event routing)
- Amazon CloudWatch (logs, metrics, alarms)

### AWS Well-Architected Framework Alignment
See WAF section above. This pipeline addresses all six pillars with explicit configuration for each.
