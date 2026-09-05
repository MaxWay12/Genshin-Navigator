from genshin_navigator.cli import main
import sys


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["launcher"]))
