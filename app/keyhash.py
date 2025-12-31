import argparse
import hashlib


def main() -> None:
    parser = argparse.ArgumentParser(description="Hash an API key for config use")
    parser.add_argument("api_key", help="Raw API key to hash")
    args = parser.parse_args()
    digest = hashlib.sha256(args.api_key.encode("utf-8")).hexdigest()
    print(digest)


if __name__ == "__main__":
    main()
