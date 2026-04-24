import uvicorn


def main():
    """
    Run the YOLO classifier FastAPI service as a standalone process.

    Usage (from the yolo_classifier directory):
        python main.py

    You can override host/port with environment variables if needed by
    adjusting this function or invoking uvicorn directly, e.g.:
        uvicorn app.main:app --host 0.0.0.0 --port 8000
    """
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()

