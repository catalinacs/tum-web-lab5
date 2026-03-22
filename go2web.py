import argparse


def main():
    parser = argparse.ArgumentParser(
        prog="go2web",
        description="A simple web fetcher and search tool",
        add_help=True,
    )
    parser.add_argument("-u", metavar="URL", help="fetch the specified URL")
    parser.add_argument("-s", metavar="SEARCH_TERM", help="search for the given term")

    args = parser.parse_args()

    if args.u:
        print(f"[placeholder] Fetching URL: {args.u}")
    elif args.s:
        print(f"[placeholder] Searching for: {args.s}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()