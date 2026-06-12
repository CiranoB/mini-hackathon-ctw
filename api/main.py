from __future__ import annotations

import os
from io import BytesIO

import boto3
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _list_parquet_keys(bucket: str, prefix: str) -> list[str]:
    response = _get_s3_client().list_objects_v2(Bucket=bucket, Prefix=prefix)
    return sorted(
        item["Key"]
        for item in response.get("Contents", [])
        if item["Key"].endswith(".parquet")
    )


def _read_parquet_rows(bucket: str, key: str) -> list[dict]:
    response = _get_s3_client().get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read()
    table = pq.read_table(BytesIO(payload))
    return table.to_pylist()


app = FastAPI(title="Vehicle Data API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/get_all")
def get_all() -> dict[str, list[dict]]:
    bucket = os.getenv("VEHICLE_DATA_BUCKET", "vehicle-data")
    prefix = os.getenv("VEHICLE_DATA_PREFIX", "parquet/")
    keys = _list_parquet_keys(bucket, prefix)

    if not keys:
        raise HTTPException(status_code=404, detail="No parquet files found")

    return {
        key.removeprefix(prefix).removesuffix(".parquet"): _read_parquet_rows(bucket, key)
        for key in keys
    }


def main() -> None:
    import uvicorn

    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
