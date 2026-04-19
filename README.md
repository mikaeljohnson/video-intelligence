# 🎬 AWS Media Intelligence Pipeline

**Serverless video and audio intelligence at scale — deploys in one CloudFormation template.**

Upload a video → get transcoded H.264 outputs, WebVTT captions, content moderation report, and an email notification with direct links to all artifacts. Total elapsed time: 3–8 minutes per video.

---

## 🏆 Hackathon Submission

**AWS Prompt the Planet Challenge** — `$5,000 AWS Activate Credits`  
**BUIDL #001** — Serverless Video/Audio Intelligence Pipeline

---

## Architecture

```
User Upload → S3 (raw) → Lambda Trigger → MediaConvert (H.264 ladder)
                                                    ↓
                                              S3 (transcoded)
                                                    ↓
                         EventBridge ──────────────→ Lambda Process
                                                    ↓
                              ┌──────────────────────┤
                              ↓                      ↓
                       Rekognition              Transcribe
                       (labels,                  (WebVTT
                        moderation,                captions)
                        celebrities)
                              ↓                      ↓
                              └──────────┬───────────┘
                                         ↓
                               JSON Moderation Report
                                         ↓
                               SES Email + SNS Fallback
```

## Services Used

| Service | Purpose |
|---|---|
| Amazon S3 | Video storage, event triggers, output storage |
| AWS Lambda | Event-driven compute |
| AWS MediaConvert | Adaptive bitrate H.264 transcoding |
| Amazon Rekognition | Label detection, content moderation, celebrity recognition |
| Amazon Transcribe | Speech-to-text → WebVTT captions |
| Amazon SES / SNS | Notifications |
| Amazon EventBridge | MediaConvert completion → Lambda routing |
| Amazon CloudWatch | Logs, metrics, alarms |

## Quick Start

### 1. Prerequisites

```bash
# AWS CLI configured
aws configure

# Region set
export AWS_REGION=us-east-1
```

### 2. Deploy

```bash
aws cloudformation deploy \
  --stack-name media-intelligence \
  --template-file cfn/pipeline.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    NotificationEmail=your@email.com \
    EnableCelebrityRecognition=false \
    ModerationThreshold=75
```

### 3. Upload a Test Video

```bash
aws s3 cp test-videos/sample.mp4 \
  s3://media-intelligence-raw-videos-<ACCOUNTID>-<REGION>/raw-videos/sample.mp4
```

### 4. Watch It Process

```bash
# Tail Lambda logs in real-time
aws logs tail /aws/lambda/media-intelligence-process --follow --since 5m

# Check MediaConvert jobs
aws mediaconvert list-jobs --region $AWS_REGION --status SUBMITTED
```

## What You Get

After processing, your `processed` S3 bucket will contain:

```
processed/
├── transcoded/
│   └── sample.mp4/
│       ├── sample_hd.mp4      # 1920×1080 @ 5 Mbps
│       ├── sample_sd.mp4       # 1280×720  @ 2.5 Mbps
│       └── sample_mobile.mp4   # 854×480   @ 1 Mbps
├── subtitles/
│   └── sample.mp4/
│       └── sample.vtt          # WebVTT captions
└── reports/
    └── sample.mp4/
        └── sample-report.json  # Structured moderation + label report
```

And an email to your inbox with direct S3 links and a moderation flag table.

## Project Structure

```
.
├── cfn/
│   └── pipeline.yaml          # CloudFormation — all infrastructure
├── src/
│   ├── lambda/
│   │   ├── trigger.py         # S3 → MediaConvert trigger
│   │   └── process.py         # EventBridge → Rekognition + Transcribe → SES
│   └── test/
│       └── .gitkeep            # Place test .mp4 files here
├── dashboard/
│   └── cloudwatch-dashboard.json  # Importable CloudWatch dashboard
├── docs/
│   └── PROMPT.md               # Full submission prompt (for reference)
├── test-videos/
│   └── test-upload.sh          # Batch upload + monitoring script
└── README.md
```

## Configuration Reference

| Parameter | Default | Description |
|---|---|---|
| `NotificationEmail` | *(required)* | Email for completion notifications |
| `EnableCelebrityRecognition` | `false` | Enable celebrity detection (adds cost) |
| `ModerationThreshold` | `75` | Min confidence for moderation flags |
| `LogRetentionDays` | `14` | CloudWatch log retention |
| `RawBucketLifecycleDays` | `30` | Days before raw → Intelligent-Tiering |
| `ProcessedBucketRetentionDays` | `365` | Days before processed files deleted |

## Cost Notes

| Service | Cost driver | Typical per-video cost |
|---|---|---|
| MediaConvert | Minutes of output × bitrate | ~$0.007–0.02 per min |
| Rekognition | Video duration | ~$0.05–0.10 per min |
| Transcribe | Audio duration | ~$0.024 per min |
| S3 storage | GB per month | ~$0.023/GB |
| Lambda invocations | Per file | < $0.001 |

**Estimated cost per 5-minute video:** ~$0.30–$0.50

## Troubleshooting

### Video uploaded but no MediaConvert job
- Check S3 event notification is enabled on the raw bucket
- Verify Lambda trigger permission: `aws lambda get-policy --function-name media-intelligence-trigger`
- Check CloudWatch logs for `/aws/lambda/media-intelligence-trigger`

### SES email not arriving
- Account is likely in SES sandbox mode — add your email to the sandbox verified list
- Or switch `NotificationEmail` to an SNS subscription during development

### Rekognition returns no labels
- Try the 720p output for Rekognition (lower resolution = same quality detection, lower cost)
- Videos must be < 8GB for Rekognition processing

## AWS Well-Architected Framework Alignment

| Pillar | How addressed |
|---|---|
| **Operational Excellence** | Event-driven, auto-scaling Lambda; CloudWatch logs + dashboard JSON; structured JSON logging |
| **Security** | SSE-S3 encryption; IAM least-privilege roles; no public bucket access; SES sender restrictions |
| **Reliability** | Idempotency guards; SES → SNS fallback; Lambda reserved concurrency; DLQ via failed invocations |
| **Performance Efficiency** | Adaptive bitrate ladder; Rekognition on 720p (not 1080p); Lambda provisioned concurrency option |
| **Cost Optimization** | Intelligent-Tiering lifecycle; MediaConvert bitrate caps; CloudWatch log retention; reserved Lambda concurrency |
| **Sustainability** | No idle compute (event-driven only); S3 Intelligent-Tiering reduces active storage |

## License

MIT — free to use, modify, and submit to the AWS Prompt Library.
