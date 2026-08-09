"""AWS 자격증명 단일 진입점 — 로컬은 .env의 SARAMQUANT 키, ECS/Lambda는 역할 자격증명."""
import os

import boto3
from dotenv import load_dotenv

load_dotenv()

_DEFAULT_REGION = "ap-northeast-2"


def build_session() -> boto3.Session:
    region = os.getenv("AWS_REGION_NAME", _DEFAULT_REGION)
    access_key = os.getenv("SARAMQUANT_IAM_KEY_ACCESS")
    secret_key = os.getenv("SARAMQUANT_IAM_KEY_SECRET")
    if not os.getenv("AWS_ACCESS_KEY_ID") and access_key and secret_key:
        return boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
    return boto3.Session(region_name=region)
