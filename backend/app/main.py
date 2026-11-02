from fastapi import FastAPI

app = FastAPI(
    title="SEOUL BIKE PULSE API",
    description="Seoul Bike Demand Prediction Platform",
    version="0.1.0"
)

@app.get("/")
def root():
    return {
        "message": "SEOUL BIKE PULSE API",
        "status": "running"
    }

@app.get("/api/v1/health")
def health():
    return {
        "status": "ok",
        "service": "bikepulse-api",
        "environment": "dev",
        "model_loaded": False,
        "stage": "bootstrap"
    }
