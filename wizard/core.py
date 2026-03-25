"""Shared prompt utilities for the setup wizard."""

import sys

C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_DIM    = "\033[2m"
C_GREEN  = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN   = "\033[36m"
C_RED    = "\033[31m"


def section(title):
    print(f"\n{C_BOLD}{'─' * 52}{C_RESET}")
    print(f"{C_BOLD}  {title}{C_RESET}")
    print(f"{C_BOLD}{'─' * 52}{C_RESET}\n")


def info(msg=""):
    print(f"  {msg}")


def success(msg):
    print(f"  {C_GREEN}✓  {msg}{C_RESET}")


def warn(msg):
    print(f"  {C_YELLOW}⚠  {msg}{C_RESET}")


def pause(msg="Press Enter when done"):
    try:
        input(f"\n  {C_DIM}[ {msg} ]{C_RESET}")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    print()


def ask(prompt, default=None, validator=None):
    """Prompt for a required value with optional default and validation."""
    hint = f" [{default}]" if default is not None else ""
    while True:
        try:
            value = input(f"  {prompt}{hint}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if not value and default is not None:
            value = str(default)

        if not value:
            warn("Required — cannot be empty")
            continue

        if validator:
            error = validator(value)
            if error:
                warn(error)
                continue

        return value


def ask_optional(prompt):
    """Prompt for an optional value — Enter to skip, returns empty string."""
    try:
        return input(f"  {prompt} (Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)


def ask_yn(prompt, default=True):
    """Yes/no prompt. Returns bool."""
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            value = input(f"  {prompt} [{hint}]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        warn("Enter y or n")


def ask_choice(prompt, options):
    """Numbered single-choice menu. options: {key: label}. Returns chosen key."""
    print(f"  {prompt}")
    keys = list(options.keys())
    for i, (key, label) in enumerate(options.items(), 1):
        print(f"    {C_CYAN}[{i}]{C_RESET}  {label}")
    while True:
        try:
            value = input("  Choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if value.isdigit() and 1 <= int(value) <= len(keys):
            return keys[int(value) - 1]
        warn(f"Enter a number between 1 and {len(keys)}")


def ask_multi(prompt, options):
    """Multi-select menu. options: {key: label}. All selected by default.
    Enter a number to toggle, empty input to confirm. Returns list of selected keys.
    """
    keys = list(options.keys())
    selected = set(range(len(keys)))

    while True:
        print(f"\n  {prompt}")
        for i, (key, label) in enumerate(options.items(), 1):
            mark = f"{C_GREEN}x{C_RESET}" if i - 1 in selected else " "
            print(f"    [{mark}] {C_CYAN}{i}{C_RESET}  {label}")
        print(f"  {C_DIM}Enter a number to toggle selection, or Enter to confirm{C_RESET}")
        try:
            value = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if not value:
            return [keys[i] for i in sorted(selected)]

        if value.isdigit() and 1 <= int(value) <= len(keys):
            idx = int(value) - 1
            if idx in selected:
                selected.remove(idx)
            else:
                selected.add(idx)
        else:
            warn(f"Enter a number between 1 and {len(keys)}, or Enter to confirm")
