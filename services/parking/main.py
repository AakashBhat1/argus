"""Development entry point: python main.py (binds to localhost only)."""

import uvicorn


def main() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8100, reload=True)


if __name__ == "__main__":
    main()
