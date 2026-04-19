# BUIDL Submission — AWS Media Intelligence Pipeline

## Required Fields

**BUIDL Name:**  
AWS Media Intelligence Pipeline

**Vision:**  
Content teams producing video at scale — compliance training, marketing, internal communications — spend hours on manual post-production: transcoding, captioning, and content moderation done by hand. AWS Media Intelligence Pipeline automates all of it. Upload a video file to S3 and receive, within minutes, adaptive bitrate H.264 outputs (1080p, 720p, 480p), WebVTT captions, a timestamped content moderation report, and an email with direct links to every artifact. Replace a 3–4 hour manual workflow with a 5-minute pipeline plus human review of flagged content only.

**Category:**  
No (not an AI Agent — it's an AI-powered infrastructure pipeline)

**GitHub:**  
*(create repo — see setup instructions below)*

**Project Website:**  
*(optional — can use GitHub Pages)*

**Demo Video:**  
*(record after first successful run — see instructions below)*

---

## Recommended GitHub Setup

```bash
cd ~/buidl-video-intelligence
git init
git add .
git commit -m "Initial commit: AWS Media Intelligence Pipeline

- Full Lambda pipeline (trigger + process)
- CloudFormation template
- CloudWatch dashboard JSON
- Test upload script
- README with quick-start"

# Create repo on GitHub first, then:
git remote add origin https://github.com/<your-username>/aws-media-intelligence-pipeline.git
git branch -M main
git push -u origin main
```

---

## Logo Recommendation

The submission requires a 480×480px JPEG or PNG < 2MB. Recommended approach:

1. **Favicon.cc or Canva** — search "AWS + video pipeline" or use an S3 bucket icon overlaid with a play button
2. **Key visual elements to include:**
   - S3 bucket or cloud shape (AWS identity)
   - Video frame / play button (media)
   - Lightning bolt or pipeline arrow (intelligence/automation)
3. **Color:** AWS orange (#FF9900) on dark background, or white on AWS blue (#232F3E)

---

## Demo Video Recording Guide

After deploying the stack and uploading a test video:

1. **Part 1 (0:00–0:30):** Show the before state — a raw video file in the S3 raw bucket, the CloudFormation stack deployed
2. **Part 2 (0:30–1:00):** Upload the test video via AWS CLI or S3 console — show the upload completing
3. **Part 3 (1:00–3:00):** Fast-forward through processing — show Lambda logs streaming, MediaConvert job in console, CloudWatch metrics ticking
4. **Part 4 (3:00–4:00):** Show the completed outputs — S3 bucket with transcoded files, the VTT file, the JSON report opened
5. **Part 5 (4:00–4:30):** Show the email notification in inbox with the moderation flag table
6. **Part 6 (4:30–5:00):** Close with the CloudWatch dashboard lit up — all metrics visible on one screen

**Recommended:** Keep it under 5 minutes. Judges are scanning, not deep-diving. Lead with the wow moment (the email hitting their inbox with all the outputs).

**Upload to:** YouTube, set as unlisted, paste the link in the submission form.

---

## Social Links Suggestions

Since you need 3 social links, here are the most impactful:

1. **X/Twitter** — Post the repo link with a short thread:
   > "I built a serverless video intelligence pipeline on AWS in one CloudFormation template. Upload a video → get H.264 transcodes, WebVTT captions, moderation report + email. Sub 5-min setup, no infrastructure to manage. [link]"
2. **LinkedIn** — Longer-form post about the architecture and what it solves
3. **Hacker News / Reddit (r/aws)** — Drive traffic to the GitHub repo

---

## Deployment Checklist

- [ ] Create GitHub repo and push all files
- [ ] Create 480×480 logo (PNG/JPEG < 2MB)
- [ ] Upload logo to submission form
- [ ] Deploy CloudFormation stack
- [ ] Upload test video, verify full pipeline runs
- [ ] Record demo video and upload to YouTube
- [ ] Paste YouTube link in submission form
- [ ] Fill in GitHub URL + any social links
- [ ] Submit before June 10, 2026

---

## Submission Deadline

**June 10, 2026** — 90-day submission window closes.
