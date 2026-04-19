"""
VideoPipeline-Trigger
Lambda function: triggered by S3 PutObject event on raw-videos/ prefix.
Kicks off a MediaConvert job to produce adaptive bitrate H.264 outputs.
"""

import json
import os
import logging
from urllib.parse import unquote_plus
import boto3

# Configure logging — structured JSON for CloudWatch
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
MEDIACONVERT_ENDPOINT = os.environ.get("MEDIACONVERT_ENDPOINT")
MEDIACONVERT_ROLE_ARN = os.environ.get("MEDIACONVERT_ROLE_ARN")
OUTPUT_BUCKET = os.environ.get("OUTPUT_BUCKET")
MAX_FILE_SIZE_GB = 8

# MediaConvert client — use regional endpoint
mediaconvert = boto3.client("mediaconvert", endpoint_url=MEDIACONVERT_ENDPOINT)
s3 = boto3.client("s3")


def handler(event, context):
    """
    Entry point. Parses S3 event, validates file, kicks off MediaConvert job.
    """
    logger.info({"event": "trigger_start", "request_id": context.request_id})

    # ── Step 1: Extract bucket and key from S3 event ──────────────────────
    try:
        raw_bucket = event["Records"][0]["s3"]["bucket"]["name"]
        encoded_key = event["Records"][0]["s3"]["object"]["key"]
        file_size_bytes = event["Records"][0]["s3"]["object"].get("size", 0)
        filename = unquote_plus(encoded_key)
    except (KeyError, IndexError) as e:
        logger.error({"event": "parse_error", "error": str(e), "raw_event": str(event)[:500]})
        raise ValueError(f"Failed to parse S3 event: {e}")

    logger.info({
        "event": "file_received",
        "bucket": raw_bucket,
        "filename": filename,
        "size_bytes": file_size_bytes
    })

    # ── Step 2: Idempotency guard — check if already processed ────────────
    try:
        metadata = s3.head_object(Bucket=raw_bucket, Key=filename)
        if metadata.get("Metadata", {}).get("x-amz-meta-processed") == "true":
            logger.warning({"event": "already_processed", "filename": filename})
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Already processed", "filename": filename})
            }
    except s3.exceptions.ClientError as e:
        # head_object failed — file might not exist (shouldn't happen if event fired)
        logger.warning({"event": "metadata_check_failed", "error": str(e)})

    # ── Step 3: File size validation — Rekognition limit is 10GB ──────────
    max_bytes = MAX_FILE_SIZE_GB * 1024 * 1024 * 1024
    if file_size_bytes > max_bytes:
        logger.error({
            "event": "file_too_large",
            "filename": filename,
            "size_bytes": file_size_bytes,
            "max_bytes": max_bytes
        })
        raise ValueError(
            f"File {filename} is {file_size_bytes / (1024**3):.2f} GB. "
            f"Maximum supported size is {MAX_FILE_SIZE_GB} GB (Rekognition limit). "
            f"Transcoding will proceed but Rekognition analysis will be skipped."
        )

    # ── Step 4: Build input/output S3 paths ───────────────────────────────
    input_s3_uri = f"s3://{raw_bucket}/{filename}"
    output_base = f"s3://{OUTPUT_BUCKET}/transcoded/{filename.replace(' ', '_')}/"
    filename_base = filename.rsplit(".", 1)[0] if "." in filename else filename

    # ── Step 5: Define adaptive bitrate ladder (H.264/MP4) ─────────────────
    outputs = _build_output_groups(filename_base, output_base)

    job_body = {
        "Role": MEDIACONVERT_ROLE_ARN,
        "Settings": {
            "Inputs": [
                {
                    "FileInput": input_s3_uri,
                    "AudioSelectors": {
                        "Audio Selector 1": {
                            "DefaultSelection": "DEFAULT"
                        }
                    },
                    "VideoSelector": {}
                }
            ],
            "OutputGroups": outputs
        },
        "UserMetadata": {
            "original_filename": filename,
            "pipeline_version": "1.0.0"
        }
    }

    # ── Step 6: Submit MediaConvert job ────────────────────────────────────
    try:
        response = mediaconvert.create_job(**job_body)
        job_id = response["Job"]["Id"]
        logger.info({
            "event": "mediaconvert_job_created",
            "job_id": job_id,
            "filename": filename,
            "input": input_s3_uri
        })
    except mediaconvert.exceptions.ClientError as e:
        logger.error({"event": "mediaconvert_error", "error": str(e)})
        raise RuntimeError(f"Failed to create MediaConvert job: {e}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "MediaConvert job created",
            "job_id": job_id,
            "filename": filename
        })
    }


def _build_output_groups(filename_base: str, output_base: str) -> list:
    """
    Build three H.264/MP4 output groups: 1080p (hd), 720p (sd), 480p (mobile).
    Uses a container group that writes all outputs to the same S3 prefix.
    """
    outputs = []

    # Output naming: {filename_base}_{label}.mp4
    labels_and_specs = [
        ("hd", 1920, 1080, 5000, "high"),
        ("sd", 1280, 720, 2500, "standard"),
        ("mobile", 854, 480, 1000, "medium"),
    ]

    output_group_settings = []
    for label, width, height, bitrate, quality in labels_and_specs:
        output_name = f"{filename_base}_{label}.mp4"
        output_group_settings.append(
            {
                "ContainerSettings": {
                    "Container": "MP4"
                },
                "VideoDescription": {
                    "Width": width,
                    "Height": height,
                    "CodecSettings": {
                        "Codec": "H_264",
                        "H264Settings": {
                            "RateControlMode": "VBR",
                            "Bitrate": bitrate,
                            "MaxBitrate": bitrate,
                            "QualityTuningLevel": "MULTI_PASS_HQ",
                            "CodecProfile": "HIGH",
                            "CodecLevel": "LEVEL_4",
                            "MaxReferenceFrames": 3,
                            "Syntax": "DEFAULT"
                        }
                    },
                    "AfdSignaling": "NONE",
                    "RespondToAfd": "NONE",
                    "Sharpness": 50
                },
                "AudioDescriptions": [
                    {
                        "CodecSettings": {
                            "Codec": "AAC",
                            "AacSettings": {
                                "Bitrate": 128000,
                                "SampleRate": 48000,
                                "CodingMode": "CODING_MODE_2_0",
                                "Specification": "MPEG4"
                            }
                        },
                        "AudioSourceName": "Audio Selector 1"
                    }
                ],
                "NameModifier": f"_{label}"
            }
        )

    outputs.append({
        "Name": "MP4-Adaptive-Bitrte-Ladder",
        "OutputGroupSettings": {
            "Type": "FILE_GROUP_SETTINGS",
            "FileGroupSettings": {
                "Destination": output_base
            }
        },
        "Outputs": output_group_settings
    })

    return outputs
