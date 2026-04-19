# AWS Media Intelligence Pipeline
## Hackathon Prompt — AWS Prompt the Planet Challenge

---

## THE PROMPT
*(Copy and paste this into any AI assistant: Claude, GPT-4, Kiro, Codex, or similar)*

---

You are a senior cloud solutions architect. Build a production-ready, serverless video and audio intelligence pipeline on AWS using Infrastructure as Code (CloudFormation or Terraform).

## Architecture Overview

The pipeline must handle the following end-to-end flow:

1. User uploads a video file (MP4, MOV, MKV, AVI, WebM) to an S3 "raw" bucket under the prefix `raw-videos/`
2. An S3 PutObject event triggers a Lambda function via SNS fan-out
3. That Lambda calls AWS MediaConvert to create adaptive bitrate H.264/MP4 outputs at 1080p (5 Mbps), 720p (2.5 Mbps), and 480p (1 Mbps)
4. MediaConvert's completion event (via EventBridge) triggers a second Lambda
5. The second Lambda runs AWS Rekognition analysis: label detection, content moderation, and (optionally) celebrity recognition — processing the 720p output to reduce cost by ~40%
6. The second Lambda also runs Amazon Transcribe to generate a WebVTT (.vtt) subtitle file from the video's audio track
7. All outputs — transcoded videos, WebVTT subtitles, and a structured JSON moderation report — are written to a "processed" S3 bucket
8. An SES email notification is sent to a configured recipient with direct S3 links to all artifacts and a moderation flag table
9. If SES fails (account in sandbox mode, or sender not verified), an SNS notification fires as fallback with the same payload

## S3 Bucket Configuration

Create two S3 buckets with the following naming convention:
- `{ProjectName}-raw-videos-{AWS::AccountId}-{AWS::Region}` — receives raw uploads. Configure with: versioning enabled, SSE-S3 encryption, public access blocked, lifecycle rule to transition files to S3 Intelligent-Tiering after 30 days, and an S3 event notification on `s3:ObjectCreated:*` with prefix `raw-videos/` and suffix `.mp4,.mov,.mkv,.avi,.webm` targeting an SNS topic named `{ProjectName}-pipeline-trigger`
- `{ProjectName}-processed-{AWS::AccountId}-{AWS::Region}` — stores all outputs. Configure with: versioning enabled, SSE-S3 encryption, public access blocked, lifecycle rule to expire processed files after 365 days, and an additional rule to transition files to Intelligent-Tiering after 90 days

The processed bucket policy must allow the MediaConvert service role to read from the raw bucket and write to the `transcoded/` prefix of the processed bucket.

## Lambda Functions

### Function 1: `VideoPipeline-Trigger`

Runtime: Python 3.11 | Memory: 512 MB | Timeout: 300 seconds

**Environment variables required:**
- `MEDIACONVERT_ENDPOINT` — regional MediaConvert endpoint (e.g., `https://mediaconvert.us-east-1.amazonaws.com`)
- `MEDIACONVERT_ROLE_ARN` — ARN of the IAM role MediaConvert assumes for S3 access
- `OUTPUT_BUCKET` — name of the processed bucket

**Behavior:**
1. On invocation, parse the S3 event to extract `bucket` name and URL-decoded `key` (e.g., `raw-videos/onboarding-60s.mp4`)
2. Check object metadata for `x-amz-meta-processed: true` — if present, log a warning and exit cleanly without reprocessing (idempotency guard)
3. Check the file size. If the file exceeds 8 GB, raise a descriptive error: `"File {filename} exceeds 8GB Rekognition processing limit. Transcoding will proceed. Skip Rekognition/Transcribe for this file."`
4. Build the MediaConvert job with the following specifications:
   - Input: `s3://{raw_bucket}/{key}`
   - Output group: FILE_GROUP_SETTINGS pointing to `s3://{OUTPUT_BUCKET}/transcoded/{key_base}/`
   - Three H.264/MP4 outputs:
     - Output 1: 1920×1080, 5000 kbps VBR, MULTI_PASS_HQ quality, codec profile HIGH, name modifier `_hd`
     - Output 2: 1280×720, 2500 kbps VBR, MULTI_PASS_HQ quality, codec profile HIGH, name modifier `_sd`
     - Output 3: 854×480, 1000 kbps VBR, MULTI_PASS_HQ quality, codec profile MAIN, name modifier `_mobile`
   - Audio: AAC 128kbps stereo from "Audio Selector 1"
   - User metadata: `original_filename={filename}`, `pipeline_version=1.0.0`
5. Log the job ID and input filename to CloudWatch in structured JSON format
6. Return the job ID in the function response

### Function 2: `VideoPipeline-Process`

Runtime: Python 3.11 | Memory: 1024 MB | Timeout: 900 seconds

**Environment variables required:**
- `OUTPUT_BUCKET` — name of the processed bucket
- `NOTIFICATION_EMAIL` — email address for completion notifications
- `SNS_TOPIC_ARN` — ARN of the fallback SNS topic
- `ENABLE_CELEBRITY_RECOGNITION` — "true" or "false"
- `MODERATION_CONFIDENCE_THRESHOLD` — minimum confidence score for moderation flags (default: 75.0)

**Behavior:**

**Step A — Locate the transcoded video for analysis:**
MediaConvert writes to `{OUTPUT_BUCKET}/transcoded/{filename}/{filename}_hd.mp4`, `{filename}_sd.mp4`, `{filename}_mobile.mp4`. Check for the `_sd.mp4` (720p) file first — use it for Rekognition analysis to reduce cost. Fall back to `_hd.mp4` if the 720p file is not present.

**Step B — Start Rekognition jobs in parallel:**
For the selected video, start three Rekognition jobs simultaneously:
- `start_label_detection` — MinConfidence: 50
- `start_content_moderation` — MinConfidence: `MODERATION_CONFIDENCE_THRESHOLD` environment variable
- If `ENABLE_CELEBRITY_RECOGNITION` == "true": `start_celebrity_recognition`

Log each returned JobId.

**Step C — Start Transcribe job in parallel with B:**
- Job name: `{filename_base}-{unix_timestamp}`
- Media format: mp4
- Language: en-US
- Output bucket: `{OUTPUT_BUCKET}`
- Output key: `subtitles/{filename}/{filename}.vtt` (Transcribe generates WebVTT natively)
- Settings: `ShowSpeakerLabels: true`, `MaxSpeakerLabels: 10`

Log the TranscriptionJobName.

**Step D — Poll until all jobs complete:**
Poll Rekognition jobs every 10 seconds with a 15-minute timeout. On SUCCEEDED: collect results. On FAILED: log the error and remove from polling queue. Continue until all jobs are resolved.

Poll the Transcribe job every 15 seconds with a 10-minute timeout. On COMPLETED: note the VTT path as `subtitles/{filename}/{filename}.vtt`.

**Step E — Generate the moderation JSON report:**
Build a structured JSON file with this schema and write it to `s3://{OUTPUT_BUCKET}/reports/{filename}/{filename}-report.json`:

```json
{
  "video_name": "example.mp4",
  "processed_at": "2026-04-19T12:00:00Z",
  "pipeline_version": "1.0.0",
  "transcoding": {
    "status": "COMPLETED",
    "rekognition_analysis_resolution": "720p (sd)",
    "outputs": [
      {"label": "hd", "resolution": "1920x1080", "path": "transcoded/example.mp4/example_hd.mp4"},
      {"label": "sd", "resolution": "1280x720", "path": "transcoded/example.mp4/example_sd.mp4"},
      {"label": "mobile", "resolution": "854x480", "path": "transcoded/example.mp4/example_mobile.mp4"}
    ]
  },
  "transcription": {
    "status": "COMPLETED",
    "vtt_path": "subtitles/example.mp4/example.vtt",
    "language": "en-US"
  },
  "labels": [
    {"name": "Person", "confidence": 97.3, "timestamp_ms": 0}
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
    "total_processing_time_seconds": 359
  }
}
```

Severity classification: confidence > 90 = HIGH, > 75 = MEDIUM, else LOW. Sort flags by confidence descending.

**Step F — Send notification:**
Attempt to send an HTML email via SES:
- From: `{NOTIFICATION_EMAIL}`
- To: `{NOTIFICATION_EMAIL}`
- Subject: `[⚠ Review] {filename}` if any HIGH-severity flags exist, otherwise `[✓ Ready] {filename}`
- Body: Include the video filename, processing timestamp, direct S3 console URLs for each output (format: `https://s3.console.aws.amazon.com/s3/object/{bucket}/{key}?region={region}`), and a moderation flag table if any flags exist (columns: Time, Flag, Confidence, Severity).

If SES throws `MessageRejected` or the account is in sandbox mode, fall back to SNS publish to `{SNS_TOPIC_ARN}` with the full report payload as JSON.

**Step G — Mark as processed:**
Write `x-amz-meta-processed: true` to the metadata of the highest-resolution transcoded output to prevent reprocessing on retries.

## IAM Role Configuration

### MediaConvert Service Role
Trust: `mediaconvert.amazonaws.com`
Permissions:
- `s3:GetObject` on `arn:aws:s3:::{ProjectName}-raw-videos-*/*`
- `s3:PutObject` on `arn:aws:s3:::{ProjectName}-processed-*/*` (transcoded/ prefix only)
- `s3:ListBucket` on both raw and processed bucket ARNs

### Lambda Execution Role
Trust: `lambda.amazonaws.com`
Permissions:
- `s3:GetObject`, `s3:PutObject`, `s3:HeadObject`, `s3:CopyObject`, `s3:ListBucket` on both bucket ARNs
- `mediaconvert:CreateJob`, `mediaconvert:GetJob`, `mediaconvert:ListJobs` (region-scoped)
- `rekognition:StartLabelDetection`, `rekognition:GetLabelDetection`, `rekognition:StartContentModeration`, `rekognition:GetContentModeration`, `rekognition:StartCelebrityRecognition`, `rekognition:GetCelebrityRecognition`
- `transcribe:StartTranscriptionJob`, `transcribe:GetTranscriptionJob`, `transcribe:ListTranscriptionJobs`
- `ses:SendEmail` (condition: `ses:FromAddress == NOTIFICATION_EMAIL`)
- `sns:Publish` to `{SNS_TOPIC_ARN}`
- CloudWatch Logs permissions for both Lambda functions

## EventBridge Rule

Create an EventBridge rule named `{ProjectName}-mediaconvert-completion` with event pattern:
```
source: aws.mediaconvert
detail-type: MediaConvert Job State Change
detail.status: COMPLETE or ERROR
```
Target: Lambda function `VideoPipeline-Process`
Use an input transformer with pathsMap: `jobId: $.detail.jobId`, `status: $.detail.status`, `userMetadata: $.detail.userMetadata`

## CloudWatch Alarms

Create the following alarms, all publishing to `{ProjectName}-pipeline-alerts` SNS topic:

1. **PipelineErrorAlarm** — Lambda function Errors metric > 0 for 1 minute, 1 evaluation period (covers both trigger and process functions)
2. **MediaConvertLagAlarm** — MediaConvert job remains in PROGRESSING state for more than 30 minutes
3. **RekognitionLagAlarm** — Rekognition job remains IN_PROGRESS for more than 20 minutes
4. **SESBounceAlarm** — SES bounce or complaint rate > 0.01 (1%)

## Cost Optimization Requirements

The prompt must include these cost controls:
- MediaConvert: `QualityTuningLevel: MULTI_PASS_HQ` with explicit `Bitrate` and `MaxBitrate` values per output (not single-pass)
- Rekognition: Always analyze the 720p output file, never the 1080p — same detection quality, 40% lower cost
- S3: Both buckets configured with Intelligent-Tiering lifecycle rules
- CloudWatch Logs: Set retention to 14 days on both Lambda function log groups
- Lambda: Set reserved concurrent executions to 5 on both functions to cap maximum parallelism and cost

## File Size Guardrails

Maximum supported file size: 8 GB (Rekognition limit). The trigger function must validate file size before creating the MediaConvert job. If the file exceeds 8 GB:
1. Still create the MediaConvert transcoding job (transcoding has no size limit)
2. Log a warning with the file size and filename
3. Skip Rekognition and Transcribe for that file with a descriptive log message
4. Send a notification via SNS (not SES) indicating manual processing is required for analysis

## Idempotency

The trigger function must check for `x-amz-meta-processed: true` in the S3 object metadata before creating a new MediaConvert job. If already processed, return a 200 response with message `"Already processed"` and log the event. The process function must also write this metadata to the highest-resolution transcoded output upon completion.

## Troubleshooting Guide

Include a troubleshooting FAQ with these entries:

**Problem: Video uploads but no MediaConvert job starts**
- Verify S3 event notification is configured and enabled on the raw bucket
- Check Lambda trigger permission: `aws lambda get-policy --function-name {ProjectName}-trigger`
- Confirm MediaConvert regional endpoint URL is correct (not the global endpoint)
- Check CloudWatch logs for `/aws/lambda/{ProjectName}-trigger`

**Problem: MediaConvert job stays in PROGRESSING forever**
- Most common cause: MediaConvert service role lacks `s3:PutObject` permission on the processed bucket
- Check bucket policy for explicit denies
- Verify the output path in the job matches the bucket name exactly

**Problem: Rekognition returns no labels**
- Confirm the file being analyzed exists (Rekognition is run on the 720p transcoded output, not the raw upload)
- Try switching to the 1080p output by changing the priority order in the Process function
- Check CloudWatch logs for Rekognition-specific error codes

**Problem: SES email never arrives**
- Account is in SES sandbox mode by default — only verified addresses can receive emails
- Add the recipient to SES sandbox verified emails list for testing
- During development, use SNS subscription as the notification target instead of SES email
- Request SES production access before launch: AWS Console → SES → Account dashboard → Request production access

**Problem: Moderation flags are empty on a video with obvious issues**
- Rekognition ContentModeration detects based on trained classifiers — not all "problematic content" triggers moderation labels
- Add `StartLabelDetection` to capture general content issues that aren't moderation violations
- Lower the `MODERATION_CONFIDENCE_THRESHOLD` to 60 to capture more flags

**Problem: Transcribe VTT has no speaker timestamps**
- Transcribe's WebVTT output includes speaker labels when `ShowSpeakerLabels: true` is set
- For scene-level timestamps (useful for video editing), post-process the VTT with MediaConvert's Analyze API

## Prerequisites

Before using this prompt, the developer needs:
1. An AWS account with billing enabled
2. AWS CLI v2 configured with `aws configure`
3. An IAM user or role with permissions to create: S3 buckets, Lambda functions, IAM roles, MediaConvert jobs, Rekognition operations, Transcribe jobs, SES (or SNS) resources
4. A verified SES sender email address OR an SNS topic ARN for fallback notifications
5. Python 3.11+ for Lambda function development
6. (Optional) `aws-sam-cli` for streamlined local testing and deployment

## Use Case

This prompt is for content teams producing video at scale — compliance training, marketing content, internal communications, user-generated content moderation — who need automated post-production workflows without managing infrastructure. It replaces manual transcoding (which takes 30–60 minutes per video in tools like HandBrake) with a fully automated pipeline that processes files in 3–8 minutes while delivering standardized output quality. Human review is required only for flagged moderation content.

## Expected Outcome

After deploying the infrastructure from this prompt and uploading a video to the `raw-videos/` S3 prefix, the developer receives:
- 3 transcoded H.264/MP4 outputs (1080p, 720p, 480p) in the `transcoded/` prefix
- A WebVTT subtitles file in the `subtitles/` prefix
- A structured JSON moderation report with timestamped labels, flags, and processing metrics in the `reports/` prefix
- An email notification with direct S3 console links to all artifacts and a formatted moderation flag table

Total elapsed time: typically 3–8 minutes depending on video length and queue depth.

## AWS Well-Architected Framework Alignment

- **Operational Excellence:** Event-driven Lambda processing, structured JSON logging, CloudWatch dashboard, automated alarms
- **Security:** SSE-S3 on all buckets, IAM least-privilege roles, public access blocked, SES sender restrictions
- **Reliability:** Idempotency guards, SES → SNS fallback, reserved Lambda concurrency, DLQ via failed Lambda invocations
- **Performance Efficiency:** Adaptive bitrate ladder, Rekognition on 720p (not 1080p), Lambda auto-scaling
- **Cost Optimization:** Intelligent-Tiering lifecycles, MediaConvert bitrate caps, CloudWatch log retention (14 days), reserved Lambda concurrency (5)
- **Sustainability:** Event-driven Lambda means zero idle compute; S3 Intelligent-Tiering reduces active storage footprint; no persistent servers

## AWS Services Used

Amazon S3, AWS Lambda, AWS MediaConvert, Amazon Rekognition, Amazon Transcribe, Amazon SES, Amazon SNS, Amazon EventBridge, Amazon CloudWatch, AWS IAM