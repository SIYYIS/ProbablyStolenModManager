"""Entry point for the standalone Windows WebUI build."""

from main import main

if __name__ == "__main__":
    raise SystemExit(main(default_web=True))
