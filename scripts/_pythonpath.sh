#!/usr/bin/env bash
# Put this checkout's python/ on PYTHONPATH in a form the interpreter reads.
#
# Git Bash does not translate PYTHONPATH when it starts a native Windows
# Python, and Windows splits the variable on ';'. A POSIX path with a ':'
# separator therefore arrives as one unusable entry, and the quickstart's
# `bash scripts/smoke.sh` failed on the Windows runner with "No module named
# 'jester'" for exactly this reason. cygpath fixes the path; the separator
# follows the platform. Source this after `cd` to the repository root.

case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        _jester_python="$(cygpath -w "$PWD/python")"
        export PYTHONPATH="$_jester_python${PYTHONPATH:+;$PYTHONPATH}"
        ;;
    *)
        export PYTHONPATH="$PWD/python${PYTHONPATH:+:$PYTHONPATH}"
        ;;
esac
unset _jester_python
