from __future__ import annotations

import os
import time
from io import BytesIO

import boto3
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query


def _get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _get_athena_client():
    return boto3.client(
        "athena",
        endpoint_url=os.getenv("AWS_ENDPOINT_URL", "http://localhost:4566"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def _run_athena_query(sql: str) -> list[dict]:
    athena = _get_athena_client()
    output_location = os.getenv("ATHENA_OUTPUT_LOCATION", "s3://athena-results/")

    execution = athena.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": output_location},
    )
    query_id = execution["QueryExecutionId"]

    deadline = time.monotonic() + float(os.getenv("ATHENA_TIMEOUT_SECONDS", "30"))
    while True:
        status = athena.get_query_execution(QueryExecutionId=query_id)[
            "QueryExecution"
        ]["Status"]
        state = status["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        if time.monotonic() > deadline:
            raise HTTPException(status_code=504, detail="Athena query timed out")
        time.sleep(0.1)

    if state != "SUCCEEDED":
        reason = status.get("StateChangeReason", "Unknown error")
        raise HTTPException(status_code=400, detail=f"Athena query {state}: {reason}")

    result = athena.get_query_results(QueryExecutionId=query_id)
    rows = result["ResultSet"]["Rows"]
    if not rows:
        return []

    columns = [col.get("VarCharValue") for col in rows[0]["Data"]]
    return [
        {
            columns[i]: cell.get("VarCharValue")
            for i, cell in enumerate(row["Data"])
        }
        for row in rows[1:]
    ]


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


@app.get("/athena")
def athena_query(
    sql: str = Query(
        default="SELECT 1 + 1 AS result",
        description="SQL statement to execute via Athena (DuckDB engine).",
    ),
) -> dict[str, object]:
    rows = _run_athena_query(sql)
    return {"query": sql, "row_count": len(rows), "rows": rows}


@app.get("/models")
def models_by_manufacturer(
    manufacturer_id: int = Query(
        description="Manufacturer ID to list all models for.",
    ),
) -> dict[str, object]:
    bucket = os.getenv("VEHICLE_DATA_BUCKET", "vehicle-data")
    prefix = os.getenv("VEHICLE_DATA_PREFIX", "parquet/")
    sql = (
        "SELECT model_id, manufacturer_id, name, segment "
        f"FROM read_parquet('s3://{bucket}/{prefix}models.parquet') "
        f"WHERE manufacturer_id = {manufacturer_id} "
        "ORDER BY model_id"
    )
    rows = _run_athena_query(sql)
    return {"manufacturer_id": manufacturer_id, "row_count": len(rows), "rows": rows}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
