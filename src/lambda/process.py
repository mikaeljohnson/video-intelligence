"""
VideoPipeline-Process
Lambda function: triggered by EventBridge on MediaConvert job completion.
Runs Rekognition analysis + Transcribe captioning, generates report, sends notification.
"""

import json
import os
import logging
import time
from datetime import datetime, timezone
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
REKOGNITION_ROLE_ARN = os.environ.get("REKOGNITION_ROLE_ARN")
TRANSCRIBE_ROLE_ARN = os.environ.get("TRANSCRIBE_ROLE_ARN")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN")
ENABLE_CELEBRITY = os.environ.get("ENABLE_CELEBRITY_RECOGNITION", "false").lower() == "true"
MODERATION_THRESHOLD = float(os.environ.get("MODERATION_CONFIDENCE_THRESHOLD", "75.0"))

rekognition = boto3.client("rekognition")
transcribe = boto3.client("transcribe")
s3 = boto3.client("s3")
ses = boto3.client("ses")
sns = boto3.client("sns")
logs = boto3.client("logs")


def handler(event, context):
    """
    Entry point. Processes MediaConvert completion event.
    """
    start_time = time.time()
    logger.info({"event": "process_start", "request_id": context.request_id, "event": str(event)[:300]})

    # ── Step 1: Parse EventBridge input ──────────────────────────────────
    try:
        detail = event.get("detail", {})
        job_id = detail.get("jobId")
        job_status = detail.get("status")
        user_metadata = detail.get("userMetadata", {})
        original_filename = user_metadata.get("original_filename", "unknown.mp4")
        filename_base = original_filename.rsplit(".", 1)[0] if "." in original_filename else original_filename
    except Exception as e:
        logger.error({"event": "parse_error", "error": str(e)})
        raise ValueError(f"Failed to parse EventBridge event: {e}")

    logger.info({"event": "job_status_check", "job_id": job_id, "status": job_status})

    if job_status != "COMPLETE":
        logger.warning({"event": "job_not_complete", "job_id": job_id, "status": job_status})
        return {"statusCode": 200, "body": "Job not complete, skipping"}

    # ── Step 2: Locate the 720p transcoded output ( Rekognition input) ───
    # MediaConvert writes to: {bucket}/transcoded/{filename}/{filename}_hd.mp4, _sd.mp4, _mobile.mp4
    transcoded_hd_key = f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_hd.mp4"
    transcoded_sd_key = f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_sd.mp4"

    # Determine which output exists (prefer 720p sd for cost efficiency)
    try:
        s3.head_object(Bucket=OUTPUT_BUCKET, Key=transcoded_sd_key)
        rekognition_video_key = transcoded_sd_key
        rekognition_resolution = "720p (sd)"
        logger.info({"event": "using_720p_output", "key": transcoded_sd_key})
    except s3.exceptions.ClientError:
        rekognition_video_key = transcoded_hd_key
        rekognition_resolution = "1080p (hd)"
        logger.info({"event": "using_1080p_output", "key": transcoded_hd_key})

    # ── Step 3: Start Rekognition jobs in parallel ─────────────────────────
    rekognition_jobs = {}

    rekognition_video_s3 = {"S3Bucket": OUTPUT_BUCKET, "S3ObjectKey": rekognition_video_key}

    # Label detection
    try:
        label_response = rekognition.start_label_detection(
            Video=rekognition_video_s3,
            MinConfidence=50.0,
            JobTag=f"{filename_base}-labels"
        )
        rekognition_jobs["labels"] = label_response["JobId"]
        logger.info({"event": "rekognition_labels_started", "job_id": label_response["JobId"]})
    except rekognition.exceptions.ClientError as e:
        logger.error({"event": "rekognition_labels_failed", "error": str(e)})

    # Content moderation
    try:
        moderation_response = rekognition.start_content_moderation(
            Video=rekognition_video_s3,
            MinConfidence=MODERATION_THRESHOLD,
            JobTag=f"{filename_base}-moderation"
        )
        rekognition_jobs["moderation"] = moderation_response["JobId"]
        logger.info({"event": "rekognition_moderation_started", "job_id": moderation_response["JobId"]})
    except rekognition.exceptions.ClientError as e:
        logger.error({"event": "rekognition_moderation_failed", "error": str(e)})

    # Celebrity recognition (optional)
    if ENABLE_CELEBRITY:
        try:
            celeb_response = rekognition.start_celebrity_recognition(
                Video=rekognition_video_s3,
                JobTag=f"{filename_base}-celebrities"
            )
            rekognition_jobs["celebrities"] = celeb_response["JobId"]
            logger.info({"event": "rekognition_celebs_started", "job_id": celeb_response["JobId"]})
        except rekognition.exceptions.ClientError as e:
            logger.warning({"event": "rekognition_celebs_failed", "error": str(e)})

    # ── Step 4: Start Transcribe job (parallel with Rekognition polling) ──
    transcribe_job_name = f"{filename_base.replace(' ', '_')}-{int(time.time())}"
    transcribe_vtt_key = f"subtitles/{original_filename.replace(' ', '_')}/{filename_base}.vtt"

    try:
        transcribe_response = transcribe.start_transcription_job(
            TranscriptionJobName=transcribe_job_name,
            Media={"MediaFileUri": f"s3://{OUTPUT_BUCKET}/{transcoded_sd_key}"},
            MediaFormat="mp4",
            LanguageCode="en-US",
            OutputBucketName=OUTPUT_BUCKET,
            OutputKey=transcribe_vtt_key,
            Settings={
                "ShowSpeakerLabels": True,
                "MaxSpeakerLabels": 10,
                "ChannelIdentification": False
            }
        )
        transcribe_job_id = transcribe_response["TranscriptionJob"]["TranscriptionJobName"]
        logger.info({"event": "transcribe_started", "job_name": transcribe_job_name})
    except transcribe.exceptions.ClientError as e:
        logger.error({"event": "transcribe_failed", "error": str(e)})
        transcribe_job_id = None

    # ── Step 5: Poll Rekognition jobs ─────────────────────────────────────
    rekognition_results = {}
    polling_timeout = 900  # 15 minutes
    poll_start = time.time()

    while rekognition_jobs and time.time() - poll_start < polling_timeout:
        for job_type, job_id in list(rekognition_jobs.items()):
            try:
                if job_type == "labels":
                    result = rekognition.get_label_detection(JobId=job_id)
                elif job_type == "moderation":
                    result = rekognition.get_content_moderation(JobId=job_id)
                elif job_type == "celebrities":
                    result = rekognition.get_celebrity_recognition(JobId=job_id)

                status = result["JobStatus"]
                if status == "SUCCEEDED":
                    rekognition_results[job_type] = result
                    del rekognition_jobs[job_type]
                    logger.info({"event": f"rekognition_{job_type}_complete", "job_id": job_id})
                elif status == "FAILED":
                    logger.error({"event": f"rekognition_{job_type}_failed", "job_id": job_id, "result": result})
                    del rekognition_jobs[job_type]
            except rekognition.exceptions.ClientError as e:
                logger.warning({"event": "rekognition_poll_error", "job_id": job_id, "error": str(e)})

        if rekognition_jobs:
            time.sleep(10)

    # ── Step 6: Poll Transcribe job ────────────────────────────────────────
    transcribe_result_key = None
    if transcribe_job_id:
        poll_start = time.time()
        while time.time() - poll_start < 600:  # 10 minute timeout
            try:
                status = transcribe.get_transcription_job(TranscriptionJobName=transcribe_job_id)
                job_status = status["TranscriptionJob"]["TranscriptionJobStatus"]
                if job_status == "COMPLETED":
                    # The VTT is already written to S3 by Transcribe — note its key
                    transcribe_result_key = transcribe_vtt_key
                    logger.info({"event": "transcribe_complete", "key": transcribe_vtt_key})
                    break
                elif job_status == "FAILED":
                    logger.error({"event": "transcribe_failed", "result": str(status)})
                    break
            except transcribe.exceptions.ClientError:
                pass
            time.sleep(15)

    # ── Step 7: Build structured report ────────────────────────────────────
    report = _build_report(
        original_filename=original_filename,
        filename_base=filename_base,
        rekognition_results=rekognition_results,
        transcribe_result_key=transcribe_result_key,
        rekognition_resolution=rekognition_resolution,
        start_time=start_time
    )

    # Write report to S3
    report_key = f"reports/{original_filename.replace(' ', '_')}/{filename_base}-report.json"
    try:
        s3.put_object(
            Bucket=OUTPUT_BUCKET,
            Key=report_key,
            Body=json.dumps(report, indent=2),
            ContentType="application/json",
            ServerSideEncryption="AES256"
        )
        logger.info({"event": "report_uploaded", "key": report_key})
    except s3.exceptions.ClientError as e:
        logger.error({"event": "report_upload_failed", "error": str(e)})

    # ── Step 8: Send notification ──────────────────────────────────────────
    _send_notification(report, original_filename, filename_base, report_key)

    # ── Step 9: Mark original file as processed (idempotency) ───────────
    try:
        s3.copy_object(
            Bucket=OUTPUT_BUCKET,
            Key=f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_hd.mp4",
            CopySource={"Bucket": OUTPUT_BUCKET, "Key": f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_hd.mp4"},
            Metadata={"processed": "true"},
            MetadataDirective="REPLACE"
        )
    except s3.exceptions.ClientError:
        pass  # Non-critical

    total_time = time.time() - start_time
    logger.info({"event": "process_complete", "total_time_seconds": round(total_time, 1)})

    return {"statusCode": 200, "body": json.dumps({"message": "Pipeline complete", "total_time_s": total_time})}


def _build_report(original_filename, filename_base, rekognition_results, transcribe_result_key, rekognition_resolution, start_time) -> dict:
    """Construct the structured JSON report."""
    report = {
        "video_name": original_filename,
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "1.0.0",
        "transcoding": {
            "status": "COMPLETED",
            "rekognition_analysis_resolution": rekognition_resolution,
            "outputs": [
                {"label": "hd", "resolution": "1920x1080", "path": f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_hd.mp4"},
                {"label": "sd", "resolution": "1280x720", "path": f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_sd.mp4"},
                {"label": "mobile", "resolution": "854x480", "path": f"transcoded/{original_filename.replace(' ', '_')}/{filename_base}_mobile.mp4"},
            ]
        },
        "transcription": {
            "status": "COMPLETED" if transcribe_result_key else "FAILED",
            "vtt_path": transcribe_result_key,
            "language": "en-US"
        },
        "labels": [],
        "moderation": {"status": "COMPLETED", "flags": []},
        "celebrities": [],
        "cloudwatch_metrics": {"total_processing_time_seconds": round(time.time() - start_time, 1)}
    }

    # Parse label detection results
    if "labels" in rekognition_results:
        labels = []
        timestamp_map = {}
        for entry in rekognition_results["labels"].get("Labels", []):
            label_name = entry["Label"]["Name"]
            confidence = round(entry["Label"]["Confidence"], 1)
            ts_ms = entry["Timestamp"]
            ts_bucket = ts_ms // 5000  # 5-second buckets for summarization

            if ts_bucket not in timestamp_map:
                timestamp_map[ts_bucket] = {"name": label_name, "confidence": confidence, "timestamps": []}
            else:
                if confidence > timestamp_map[ts_bucket]["confidence"]:
                    timestamp_map[ts_bucket]["confidence"] = confidence

            labels.append({"name": label_name, "confidence": confidence, "timestamp_ms": ts_ms})

        report["labels"] = sorted(labels, key=lambda x: x["confidence"], reverse=True)[:50]

    # Parse moderation results
    if "moderation" in rekognition_results:
        flags = []
        for entry in rekognition_results["moderation"].get("ModerationLabels", []):
            label_name = entry["ModerationLabel"]["Name"]
            confidence = round(entry["ModerationLabel"]["Confidence"], 1)
            parent_name = entry["ModerationLabel"].get("ParentName", "")
            severity = "HIGH" if confidence > 90 else ("MEDIUM" if confidence > 75 else "LOW")
            flags.append({
                "label": f"{parent_name}/{label_name}" if parent_name else label_name,
                "confidence": confidence,
                "timestamp_ms": entry["Timestamp"],
                "severity": severity
            })
        report["moderation"]["flags"] = sorted(flags, key=lambda x: x["confidence"], reverse=True)
        report["moderation"]["summary"] = f"{len(flags)} moderation flag(s) detected"

    # Parse celebrity results
    if "celebrities" in rekognition_results:
        celebs = []
        for entry in rekognition_results["celebrities"].get("Celebrities", []):
            celebs.append({
                "name": entry["Celebrity"]["Name"],
                "confidence": round(entry["Celebrity"]["Confidence"], 1),
                "timestamp_ms": entry["Timestamp"]
            })
        report["celebrities"] = sorted(celebs, key=lambda x: x["confidence"], reverse=True)
        report["moderation"]["summary"] += f", {len(celebs)} celebrity/ies detected"

    return report


def _send_notification(report, original_filename, filename_base, report_key):
    """Send email via SES, fall back to SNS on failure."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    bucket_region = region  # Assume same region for simplicity

    # Build S3 console URLs
    def s3_url(key):
        return f"https://s3.console.aws.amazon.com/s3/object/{OUTPUT_BUCKET}/{key}?region={bucket_region}"

    output_links = []
    for output in report["transcoding"]["outputs"]:
        label = output["label"].upper()
        url = s3_url(output["path"])
        output_links.append(f'<li><a href="{url}"><b>{label}</b> ({output["resolution"]})</a></li>')

    vtt_link = f'<li><a href="{s3_url(report["transcription"]["vtt_path"])}"><b>WebVTT Subtitles</b></a></li>' if report["transcription"]["vtt_path"] else ""
    report_link = f'<li><a href="{s3_url(report_key)}"><b>JSON Moderation Report</b></a></li>'

    # Moderation flags table
    flags_html = ""
    if report["moderation"]["flags"]:
        flags_rows = ""
        for flag in report["moderation"]["flags"]:
            ts_sec = flag["timestamp_ms"] // 1000
            flags_rows += f'<tr><td>{ts_sec}s</td><td>{flag["label"]}</td><td style="color:{"red" if flag["severity"]=="HIGH" else "orange"}">{flag["confidence"]}%</td><td>{flag["severity"]}</td></tr>'
        flags_html = f"""
        <h3 style="color:#c0392b;">⚠️ Moderation Flags ({len(report["moderation"]["flags"])})</h3>
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%;">
            <tr style="background:#f5b7b1;"><th>Time</th><th>Flag</th><th>Confidence</th><th>Severity</th></tr>
            {flags_rows}
        </table>
        <p style="color:#7b241c;">Review flagged timestamps before publishing.</p>
        """

    html_body = f"""
    <html>
    <body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px;">
        <div style="background:#1a5276;padding:20px;border-radius:8px 8px 0 0;">
            <h1 style="color:#fff;margin:0;">✅ Video Processing Complete</h1>
            <p style="color:#aed6f1;margin:5px 0 0 0;">{original_filename}</p>
        </div>
        <div style="background:#f8f9fa;padding:20px;border:1px solid #ddd;border-top:none;border-radius:0 0 8px 8px;">
            <p><b>Processed:</b> {report["processed_at"]}</p>
            <p><b>Analysis resolution:</b> {report["transcoding"]["rekognition_analysis_resolution"]}</p>

            <h3>📦 Available Outputs</h3>
            <ul style="list-style:none;padding:0;">{''.join(output_links)}{vtt_link}{report_link}</ul>

            {flags_html}

            <hr style="margin:20px 0;">
            <p style="font-size:12px;color:#7f8c8d;">
                This notification was generated by the AWS Media Intelligence Pipeline v1.0.0.<br>
                Pipeline report: <a href="{s3_url(report_key)}">{report_key}</a>
            </p>
        </div>
    </body>
    </html>
    """

    subject = f"[✓ Ready] {original_filename} — {len(report['transcoding']['outputs'])} output(s) available"
    if report["moderation"]["flags"]:
        subject = f"[⚠ Review] {original_filename} — {len(report['moderation']['flags'])} flag(s) need review"

    # Try SES first
    try:
        ses.send_email(
            Source=NOTIFICATION_EMAIL,
            Destination={"ToAddresses": [NOTIFICATION_EMAIL]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}}
            }
        )
        logger.info({"event": "ses_notification_sent", "to": NOTIFICATION_EMAIL})
    except ses.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if "MessageRejected" in error_code or "Sandbox" in str(e):
            logger.warning({"event": "ses_sandbox_fallback", "error": str(e)})
            # Fall back to SNS
            try:
                sns.publish(
                    TopicArn=SNS_TOPIC_ARN,
                    Subject=subject,
                    Message=json.dumps({
                        "video": original_filename,
                        "report_key": report_key,
                        "outputs": report["transcoding"]["outputs"],
                        "moderation_flags": report["moderation"]["flags"],
                        "s3_urls": {
                            "report": s3_url(report_key),
                            "vtt": s3_url(report["transcription"]["vtt_path"]) if report["transcription"]["vtt_path"] else None
                        }
                    })
                )
                logger.info({"event": "sns_fallback_notification_sent"})
            except sns.exceptions.ClientError as sns_error:
                logger.error({"event": "sns_notification_failed", "error": str(sns_error)})
        else:
            logger.error({"event": "ses_error", "error": str(e)})
