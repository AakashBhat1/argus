import asyncio
import httpx
import uvicorn
import subprocess
import time

async def main():
    print("Starting uvicorn server in background...")
    proc = subprocess.Popen(["uvicorn", "app.main:app", "--port", "8000"])
    
    # Wait for startup
    time.sleep(3)
    
    try:
        async with httpx.AsyncClient() as client:
            print("Fetching /api/v1/metrics...")
            response = await client.get("http://localhost:8000/api/v1/metrics")
            print(f"Status: {response.status_code}")
            print("JSON Metrics:")
            print(response.json())
            
            print("\nFetching /api/v1/metrics/prometheus...")
            response = await client.get("http://localhost:8000/api/v1/metrics/prometheus")
            print(f"Status: {response.status_code}")
            print("Prometheus Metrics:")
            print(response.text)
            
    finally:
        print("Shutting down server...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    asyncio.run(main())
