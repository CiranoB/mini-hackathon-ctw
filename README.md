# mini-hackathon-ctw

## Simple API

This repo now includes a minimal FastAPI app that reads all `.parquet` files from the `vehicle-data` bucket in LocalStack and exposes them at `GET /get_all`.

### Install with UV

```bash
uv sync
```

### Run the API

```bash
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

### Endpoints

```text
GET /health
GET /get_all
```

### Optional environment variables

```text
AWS_ENDPOINT_URL=http://localhost:4566
AWS_REGION=us-east-1
VEHICLE_DATA_BUCKET=vehicle-data
VEHICLE_DATA_PREFIX=parquet/
```