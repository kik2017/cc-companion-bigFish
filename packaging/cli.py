"""cc-companion-cli.exe: the portable build's console program.

    cc-companion-cli launch [--revive]   Claude Code's SessionStart / UserPromptSubmit hook
    cc-companion-cli statusline          Claude Code's status line
    cc-companion-cli mind                the persona's mind, started by the pet

Claude Code runs the first two (Connect to Claude Code writes them into its settings), and
the pet runs the mind; none of them is meant to be started by hand.
"""

import sys


def main() -> int:
    command, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("", [])
    if command == "statusline":
        from companion import statusline

        return statusline.main()
    if command == "launch":
        from companion import launcher

        return launcher.main(rest)
    if command == "mind":
        from mind.__main__ import main as mind_main

        return mind_main()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
