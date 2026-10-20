#!/bin/bash
#
# -----------------------------------------------------------------------------
# This file may only be modified by the admin responsible for the IEDB build system.
# General contributors must not edit this file.
#
# Build script for the IEDB project
#
# Developers edit and run the tool in the project folder. One build runs at a
# time in a folder (.ngargparser/build.lock). This script:
#   1. fetches dependencies into the project's libs/ with scripts/get_libs.sh
#      (skipped while scripts/get_libs.sh, TOOL_VERSION and what it fetched
#      are unchanged),
#   2. copies the project into build/<TOOL>-<VERSION>/ (symlinks are kept as
#      links, never followed) and checks the copy has what a deploy needs,
#   3. runs scripts/hooks.sh against that copy,
#   4. checks that every symlink stays inside the build and that the tool's
#      help screens start,
#   5. packs the tarball and makes the copied files read-only, because the
#      build dir is output: the next build replaces it.
# With --clean (make clean), it removes build/ instead, under the same lock.
# With --libs-only (make libs), it runs step 1 alone, under the same lock.
# -----------------------------------------------------------------------------

# Parse command line arguments
PROGRESS_MODE=false
CLEAN_MODE=false
LIBS_ONLY=false
for arg in "$@"; do
    case $arg in
        --progress|-p)
            PROGRESS_MODE=true
            shift
            ;;
        --clean)
            CLEAN_MODE=true
            ;;
        --libs-only)
            LIBS_ONLY=true
            ;;
    esac
done

# Progress bar configuration
TOTAL_STEPS=10
CURRENT_STEP=0
PROGRESS_BAR_WIDTH=40
# The bar redraws its line with a carriage return and an escape code, which a
# log (a CI job's, or make build > build.log) shows as raw bytes. So it is
# drawn only when stdout is a terminal and NO_COLOR is unset or empty
# (https://no-color.org); else progress mode prints each step as a line.
PROGRESS_BAR=false
if [ "$PROGRESS_MODE" = true ] && [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    PROGRESS_BAR=true
fi

# While the bar is drawn, from the first step on, stderr goes to
# .ngargparser/build-stderr.tmp instead of the terminal (start_stderr_capture),
# so a command's own error message never runs into the progress bar's line.
# What collects there is printed in order, on a cleared line, before the next
# bar, notice or result line, and when the build ends.
STDERR_FILE=""

flush_stderr() {
    if [ -n "$STDERR_FILE" ] && [ -s "$STDERR_FILE" ]; then
        printf '\r\033[K' >&8
        cat "$STDERR_FILE" >&8
        : > "$STDERR_FILE"
    fi
    return 0
}

# Call at top level only. fd 8 keeps the real stderr. The file is opened for
# appending, so it can be emptied while open.
start_stderr_capture() {
    [ "$PROGRESS_BAR" = true ] || return 0
    STDERR_FILE="$STATE_DIR/build-stderr.tmp"
    if ! : 2>/dev/null > "$STDERR_FILE"; then
        STDERR_FILE=""
        return 0
    fi
    exec 8>&2 2>>"$STDERR_FILE"
}

# Call at top level only (on_exit does).
stop_stderr_capture() {
    [ -n "$STDERR_FILE" ] || return 0
    flush_stderr
    exec 2>&8 8>&-
    rm -f "$STDERR_FILE"
    STDERR_FILE=""
}

# Function to show progress bar
show_progress() {
    if [ "$PROGRESS_MODE" = true ]; then
        flush_stderr
        local message="$1"
        CURRENT_STEP=$((CURRENT_STEP + 1))
        local percent=$((CURRENT_STEP * 100 / TOTAL_STEPS))
        if [ "$PROGRESS_BAR" != true ]; then
            printf '%3d%% - %s\n' "$percent" "$message"
            return 0
        fi
        local filled=$((CURRENT_STEP * PROGRESS_BAR_WIDTH / TOTAL_STEPS))
        local empty=$((PROGRESS_BAR_WIDTH - filled))

        # Build progress bar string
        local bar=""
        for ((i=0; i<filled; i++)); do bar+="█"; done
        for ((i=0; i<empty; i++)); do bar+="░"; done

        # Print progress bar (overwrite previous line)
        printf "\r\033[K[%s] %3d%% - %s" "$bar" "$percent" "$message"

        # Print newline on completion
        if [ "$CURRENT_STEP" -eq "$TOTAL_STEPS" ]; then
            echo ""
        fi
    fi
}

# Function for verbose logging (only prints in non-progress mode)
log_verbose() {
    if [ "$PROGRESS_MODE" != true ]; then
        echo "$@"
    fi
}

# Messages the developer must see in both modes. While the bar is drawn, its
# line is cleared first so the message does not run into it. A notice first
# prints any captured stderr, so the two keep their order; an error goes to
# stderr itself, after what is captured there.
notice() {
    flush_stderr
    if [ "$PROGRESS_BAR" = true ]; then
        printf '\r\033[K'
    fi
    echo "$@"
}

error() {
    if [ "$PROGRESS_BAR" = true ]; then
        printf '\r\033[K' >&2
    fi
    echo "ERROR: $*" >&2
}

# True when the environment variable named $1 is switched on. Unset, empty and
# 0, false, no, off (any case) count as off, as for the cli's NGARGPARSER_*
# switches, so NAME=0 undoes NAME=1.
env_flag() {
    local value
    value=$(printf '%s' "${!1:-}" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')
    case "$value" in
        ""|0|false|no|off) return 1 ;;
    esac
    return 0
}

# Stop on the first error in both modes. Verbose mode (make build-verbose)
# prints each step and the output of get_libs.sh and hooks.sh;
# NGARGPARSER_BUILD_TRACE=1 also traces every command (set -x), in either mode.
set -e
set -o pipefail
if env_flag NGARGPARSER_BUILD_TRACE; then
    set -x
fi

# A file name is bytes, and need not be UTF-8 (a name saved in Latin-1, such
# as cl\351.key). In a UTF-8 locale, awk, sed and grep cannot match such a
# byte ('[^/]*' stops at it, and grep -x finds no such line), so an excluded
# file shipped and a folder moved to the top of the tarball. So every awk,
# sed, grep and sort in this script runs through these functions, in the C
# locale, where each byte is a character. The scripts and the tool the build
# runs keep the caller's locale.
awk() { LC_ALL=C command awk "$@"; }
sed() { LC_ALL=C command sed "$@"; }
grep() { LC_ALL=C command grep "$@"; }
sort() { LC_ALL=C command sort "$@"; }
# So does bash's own read: in a UTF-8 locale, a byte that starts a character
# (\351) takes the byte after it along, and at the end of a name that is the
# line break (or the NUL of read -d ''), so two names read as one. The C
# locale applies to the read alone: the shell's locale is back right after.
read() { LC_ALL=C builtin read "$@"; }

# A step of the build: the progress bar in progress mode, a line in verbose mode.
step() {
    show_progress "$1"
    log_verbose "$1"
}

# Globs must see dotfiles: hidden files are excluded from the build by the
# built-in '.*' rule, but a '!' negation in .distignore can only re-include a
# dotfile if the copy loops actually iterate over it.
shopt -s dotglob

# Resolve paths.
# This script lives in scripts/core/ (framework-owned, sync-managed). build.conf
# sits next to it in scripts/core/ (user-owned; sync never overwrites it).
# hooks.sh and get_libs.sh live one level up in scripts/ (user-owned).
# SRC_DIR keeps its established meaning: the user-owned scripts/ dir, which is
# what the hooks see via the exports below. The exclusion file is .distignore
# at PROJECT_ROOT (see setup_exclusions).
BUILD_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(cd "$BUILD_SH_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
APP_NAME=$(basename "$PROJECT_ROOT")

# Dependency fetch state. .ngargparser/ is the project's runtime-state dir: the
# scaffold's .gitignore keeps it out of git, and it never ships (hard_excluded).
STATE_DIR="$PROJECT_ROOT/.ngargparser"
GET_LIBS_SCRIPT="$SRC_DIR/get_libs.sh"
LIBS_STAMP="$STATE_DIR/libs-stamp"
GET_LIBS_LOG="$STATE_DIR/get_libs.log"
HOOKS_LOG="$STATE_DIR/hooks.log"
LIBS_DIR="$PROJECT_ROOT/libs"
STAMP_FORMAT="ngargparser-libs-stamp 3"
# Written by older builds: still read, and upgraded in place. Format 2 holds
# fingerprints of the paths alone, format 1 none.
STAMP_FORMAT_2="ngargparser-libs-stamp 2"
STAMP_FORMAT_1="ngargparser-libs-stamp 1"
BUILD_LOCK="$STATE_DIR/build.lock"
NL=$'\n'
LIBS_GITIGNORE_MARKER="# Managed by scripts/core/build.sh: entries fetched by scripts/get_libs.sh. Do not edit."

# Function to ensure __init__.py files exist in a directory tree. .git and
# __pycache__ folders are skipped: they hold no Python packages.
ensure_init_files() {
    local target_dir="$1"

    if [ -d "$target_dir" ]; then
        find "$target_dir" \( -name .git -o -name __pycache__ \) -prune -o -type d -print | while IFS= read -r subdir; do
            if [ ! -f "$subdir/__init__.py" ]; then
                log_verbose "  Creating __init__.py in: $subdir"
                echo "# Auto-generated __init__.py file" > "$subdir/__init__.py"
            fi
        done
    fi
}

# rm -rf that also removes read-only directories (some archives and tool caches
# unpack that way, and the build makes its own output read-only), and folders
# no one may read (mode 000): u+rwX gives each folder u+rwx before chmod reads
# it, so such a folder left in build/ never stops the next build or make clean.
rm_tree() {
    { [ -e "$1" ] || [ -L "$1" ]; } || return 0
    if [ -d "$1" ] && [ ! -L "$1" ]; then
        chmod -R u+rwX "$1"
    fi
    rm -rf "$1"
}

# get_libs.sh and hooks.sh run in the background, their pid in SCRIPT_PID,
# while the build waits for them with wait_script. A trapped signal (TERM, HUP,
# INT) ends a wait at once, where a script in the foreground would first run
# to its end; on_exit then stops the script with stop_script. Start the script
# with '... &' and set SCRIPT_PID=$! right after. Returns the script's exit
# code.
SCRIPT_PID=""
wait_script() {
    local rc=0
    wait "$SCRIPT_PID" || rc=$?
    SCRIPT_PID=""
    return "$rc"
}

# Every process as "<pid> <ppid>", one per line. Some slim container images
# (python:3.11-slim) have no ps, and busybox ps takes no -A: on Linux, /proc
# gives the same table. In a /proc/<pid>/stat line the parent's pid is the
# second field after the command name, which is in parentheses and may hold
# spaces or a ')', so the name ends at the last ') '. Prints nothing when
# neither works.
process_table() {
    if command -v ps >/dev/null 2>&1 && ps -A -o pid= -o ppid= 2>/dev/null; then
        return 0
    fi
    if [ -r /proc/self/stat ]; then
        { cat /proc/[0-9]*/stat 2>/dev/null || true; } |
            sed -n 's/^\([0123456789]*\) .*) [^ ]* \([0123456789]*\) .*/\1 \2/p'
    fi
    return 0
}

# $1 and every process below it, one pid per line. Only $1 where the process
# table cannot be read.
process_tree() {
    process_table | awk -v root="$1" '
        { parent[$1] = $2 }
        END {
            print root
            found[root] = 1
            do {
                more = 0
                for (p in parent)
                    if (!(p in found) && (parent[p] in found)) { found[p] = 1; print p; more = 1 }
            } while (more)
        }'
}

# Sends TERM to the script being waited for and to everything it started (a
# git clone, a download), then waits for it to end. A script own_group started
# leads a process group of its own (its pid is the group's id), and the whole
# group gets the TERM too: a job the script started with '&' and that outlived
# it is no longer below it in the process table, but still in its group. For
# any other pid, no such group exists and that kill does nothing.
stop_script() {
    [ -n "$SCRIPT_PID" ] || return 0
    local pids
    pids=$(process_tree "$SCRIPT_PID")
    kill -TERM -- "-$SCRIPT_PID" 2>/dev/null
    # shellcheck disable=SC2086 # one pid per word
    kill -TERM $pids 2>/dev/null
    wait "$SCRIPT_PID" 2>/dev/null
    SCRIPT_PID=""
    return 0
}

# When process $1 started, in clock ticks since boot (field 22 of
# /proc/<pid>/stat), or nothing where /proc cannot tell (another system, a
# process that is gone). The command name (field 2) is in parentheses and
# may hold spaces or a ')', so the fields after the last ') ' are 3, 4, ...:
# field 22 is the 20th of them. A pid the system gives to a new process gets
# that process's start time, so the pair tells processes apart.
process_start_time() {
    local stat fields=()
    stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 0
    read -r -a fields <<< "${stat##*") "}"
    if [ ${#fields[@]} -ge 20 ]; then
        printf '%s\n' "${fields[19]}"
    fi
    return 0
}

# The id Linux gives this boot of the computer, or nothing on another system.
boot_id() {
    cat /proc/sys/kernel/random/boot_id 2>/dev/null || true
}

# The process group get_libs.sh or hooks.sh runs in, recorded in
# .ngargparser/script-group while it runs: line 1 the group's id, line 2 when
# its first process started (process_start_time), line 3 the boot id (both
# empty where /proc cannot tell). A build killed with kill -9 (or by the
# out-of-memory killer) cannot stop the script, which runs in a group of its
# own (own_group): it went on writing into the fetch's temp folder or into
# the next build's copy. The next build reads this file and stops that group
# first (stop_left_over_script). Written by own_group, removed once the
# script and its jobs have ended (clear_script_group).
SCRIPT_GROUP_FILE="$STATE_DIR/script-group"
record_script_group() {
    { printf '%s\n%s\n%s\n' "$1" "$(process_start_time "$1")" "$(boot_id)" > "$SCRIPT_GROUP_FILE"; } 2>/dev/null || true
}

# Runs the command "$@" (get_libs.sh or hooks.sh) in a process group of its
# own, so stop_script can stop all of it. Ctrl-C signals the build's whole
# process group: the script's own shell dies of it, but a job the script
# started with '&' ignores it and would run on after the build, out of
# stop_script's reach. In a group of its own, the script gets no Ctrl-C, and
# stops with the build. With setsid, the command starts a session of its own,
# keeping its pid (it can still read a terminal on its stdin, but cannot open
# /dev/tty); without it (macOS), it runs as a job of this subshell with job
# control on, and this subshell passes a TERM on to the job's group. Either
# way the group is recorded (record_script_group). Call in a '( ... ) &'
# subshell, or as the command of '... &', as its last command. Standard
# input stays as the caller gave it.
own_group() {
    if command -v setsid >/dev/null 2>&1; then
        # This subshell's pid, which setsid keeps and makes the group's id.
        # bash 3.2 has no BASHPID: the parent of a shell this subshell execs
        # in a command substitution is this subshell.
        record_script_group "${BASHPID:-$(exec sh -c 'echo "$PPID"')}"
        exec setsid "$@"
    fi
    # A job in a group of its own that reads the terminal is stopped
    # (SIGTTIN), and the build would wait for it for ever: a hook whose stdin
    # is the terminal runs in the build's group, as before, and that group is
    # not recorded.
    if [ -t 0 ]; then
        exec "$@"
    fi
    set -m
    "$@" <&0 &
    OWN_GROUP_JOB=$!
    # The job's group, for stop_left_jobs: it is not the pid the build waits for.
    record_script_group "$OWN_GROUP_JOB"
    # This subshell stays in the build's process group, so a Ctrl-C reaches
    # it too: it must live on until the build stops it, as the job's parent.
    trap '' INT
    trap 'kill -TERM -- "-$OWN_GROUP_JOB" 2>/dev/null' TERM HUP
    wait "$OWN_GROUP_JOB"
}

# The process group get_libs.sh or hooks.sh ran in, after wait_script: $1 is
# the pid the build waited for, the group's id with setsid. Without setsid,
# own_group recorded the job's group in .ngargparser/script-group. Call
# clear_script_group right before starting the script, and again once
# stop_left_jobs has run.
clear_script_group() {
    rm -f "$SCRIPT_GROUP_FILE" 2>/dev/null || true
}

script_group() {
    local group
    group=$(sed -n 1p "$SCRIPT_GROUP_FILE" 2>/dev/null) || group=""
    case "$group" in
        ""|*[!0123456789]*) group=$1 ;;
    esac
    printf '%s\n' "$group"
}

# True when a process that is not a zombie is in the process group $1. kill
# -0 alone also finds a zombie, which a container's first process may leave
# uncollected; ps, or /proc where there is no ps, tells them apart. In a
# /proc/<pid>/stat line, the state, the parent's pid and the group follow the
# command name, which ends at the last ') '.
group_running() {
    local table
    kill -0 -- "-$1" 2>/dev/null || return 1
    if command -v ps >/dev/null 2>&1 && table=$(ps -A -o pgid= -o stat= 2>/dev/null); then
        :
    elif [ -r /proc/self/stat ]; then
        table=$({ cat /proc/[0-9]*/stat 2>/dev/null || true; } |
            sed -n 's/^[0123456789]* .*) \([^ ]*\) [0123456789]* \([0123456789]*\) .*/\2 \1/p')
    else
        return 0
    fi
    printf '%s\n' "$table" | awk -v group="$1" '$1 == group && $2 !~ /^Z/ { found = 1 } END { exit !found }'
}

# A job get_libs.sh or hooks.sh started with '&' and left running when it
# exited would run on after the build, writing into libs/ or the build copy.
# It is still in the script's process group (own_group), so it is stopped
# here, TERM first, KILL after 5 seconds, with a notice naming the script $2.
# A job that left the group (setsid, a daemon) is out of reach, and so is one
# a hook started while its stdin was a terminal on a system without setsid,
# since that hook runs in the build's own group.
stop_left_jobs() {
    group_running "$1" || return 0
    end_group "$1"
    notice "Notice: $2 left background jobs running; the build stopped them."
    return 0
}

# Stops every process in the group $1: TERM first, KILL after 5 seconds,
# then waits up to a second for the group to be gone.
end_group() {
    local group="$1" tries=0
    kill -TERM -- "-$group" 2>/dev/null || true
    while group_running "$group" && [ "$tries" -lt 50 ]; do
        sleep 0.1 2>/dev/null || sleep 1
        tries=$((tries + 1))
    done
    # Gone by now, as a rule, and then this kill finds nothing.
    kill -KILL -- "-$group" 2>/dev/null || true
    tries=0
    while group_running "$group" && [ "$tries" -lt 10 ]; do
        sleep 0.1 2>/dev/null || sleep 1
        tries=$((tries + 1))
    done
    return 0
}

# The get_libs.sh or hooks.sh a build killed with kill -9 left running (its
# group is in .ngargparser/script-group): stopped before this build removes
# or makes anything, with a notice. Only while that group is the one
# recorded: on another boot, or when the group's first process runs but
# started at another time, the number belongs to another process now, and
# nothing is stopped. While any process is in a group, Linux gives its
# number to no new process, so a group whose first process is gone is still
# the script's. Call under the build lock.
stop_left_over_script() {
    local group start boot now
    { [ -e "$SCRIPT_GROUP_FILE" ] || [ -L "$SCRIPT_GROUP_FILE" ]; } || return 0
    group=$(sed -n 1p "$SCRIPT_GROUP_FILE" 2>/dev/null) || group=""
    start=$(sed -n 2p "$SCRIPT_GROUP_FILE" 2>/dev/null) || start=""
    boot=$(sed -n 3p "$SCRIPT_GROUP_FILE" 2>/dev/null) || boot=""
    clear_script_group
    case "$group" in
        ""|0|1|*[!0123456789]*) return 0 ;;
    esac
    if [ -n "$boot" ]; then
        now=$(boot_id)
        [ -z "$now" ] || [ "$now" = "$boot" ] || return 0
    fi
    if [ -n "$start" ] && pid_alive "$group"; then
        now=$(process_start_time "$group")
        [ -z "$now" ] || [ "$now" = "$start" ] || return 0
    fi
    group_running "$group" || return 0
    end_group "$group"
    notice "Notice: a build that was stopped with kill -9 (or by the out-of-memory killer) left scripts/get_libs.sh or scripts/hooks.sh running; this build stopped it first."
    return 0
}

# ---------------------------------------------------------------------------
# Build lock: one build at a time per tool folder, because two builds share
# libs/, the fetch's temp folders and build/. With flock, the lock is held on
# .ngargparser/build.lock through fd 9 and ends with the process, so it never
# goes stale; the scripts and other long commands the build runs get fd 9
# closed (9>&-, close_lock_fd), so a process that outlives the build cannot
# keep it. Without flock (macOS), .ngargparser/build.lock is a folder holding
# the build's pid, taken over when that process is gone. The second build
# stops at once.
# ---------------------------------------------------------------------------
BUILD_LOCK_DIR=""  # set while this build holds a folder lock

# The flock is held, or a folder lock whose pid is running: $1 is the pid of
# the build or sync that holds it, or empty when that is not known.
lock_busy() {
    error "Another build or a cli sync is running in this folder (pid ${1:-unknown}). Wait for it to finish."
    exit 1
}

# A lock without flock that may be left over. $1 is "" for a folder that
# holds no pid: a build that has just made it, or one killed before it wrote
# its pid. Else the lock could not be taken over: $1 is the pid, no longer
# running, or "none" for the empty file a build with flock leaves.
lock_left_over() {
    local pid=" (pid $1)"
    if [ -z "$1" ]; then
        error "Another build or a cli sync may be running in this folder (its pid is not known). Wait for it to finish, or remove .ngargparser/build.lock if none is running."
        exit 1
    fi
    [ "$1" != none ] || pid=""
    error "A build or a cli sync that is no longer running$pid left .ngargparser/build.lock, and this build could not remove it. Remove .ngargparser/build.lock (or ask its owner to), then build again."
    exit 1
}

# Closes fd 9, the flock, in a command substitution or other subshell, so a
# process the build runs there (Python, uv) cannot keep the lock after the
# build is killed. Does nothing in the build's own shell, where fd 9 is the
# lock.
close_lock_fd() {
    if [ "${BASH_SUBSHELL:-0}" -gt 0 ]; then
        exec 9>&-
    fi
}

# Opens .ngargparser/build.lock as fd 9 for flock. In a tool folder several
# people share, the file may be another user's and only readable for the
# rest: flock locks a read-only fd too, so a read-write open that fails is
# tried read-only. A new file is made with mode 0666 whatever the umask, so
# the next user can write their pid into it. Call at top level only.
open_lock_file() {
    local reason
    if [ ! -e "$BUILD_LOCK" ]; then
        (umask 000 && : >> "$BUILD_LOCK") 2>/dev/null || true
    fi
    { exec 9<>"$BUILD_LOCK"; } 2>/dev/null && return 0
    { exec 9<"$BUILD_LOCK"; } 2>/dev/null && return 0
    # The shell's own words, such as "Permission denied": from opening the file
    # when it is there, and from creating it when it is not.
    if [ -e "$BUILD_LOCK" ]; then
        reason=$({ exec 3<"$BUILD_LOCK"; } 2>&1) || true
    else
        reason=$({ : >> "$BUILD_LOCK"; } 2>&1) || true
    fi
    reason=${reason##*: }
    if [ -e "$BUILD_LOCK" ]; then
        lock_unusable open "$reason"
    fi
    lock_unusable create "$reason"
}

# .ngargparser/build.lock cannot be opened ($1 "open") or made ($1 "create")
# for the reason $2, in cli sync's words (_LockUnusable in cli.py): the folder
# is made writable for its owner (chmod u+rwx) or for a group that shares the
# tool (chmod g+rwx), never for everyone.
lock_unusable() {
    local reason="${2:-unknown error}" fix
    if [ "$1" = open ]; then
        error "Cannot open .ngargparser/build.lock ($reason): ask its owner to run chmod a+rw .ngargparser/build.lock. Nothing was changed."
        exit 1
    fi
    if [ -O "$STATE_DIR" ]; then
        fix=".ngargparser is read-only for you; run chmod u+rwx .ngargparser"
    else
        fix="ask the owner of .ngargparser to run chmod g+rwx .ngargparser, which lets a group you share write there"
    fi
    error "Cannot create .ngargparser/build.lock ($reason): $fix. Nothing was changed."
    exit 1
}

# Who holds the folder lock at $1: the pid in the lock folder (empty when it
# holds none), or, for the file a build with flock leaves behind, the pid in
# it ("none" when it is empty, which is never in use).
lock_holder_at() {
    local holder
    if [ -d "$1" ]; then
        holder=$(sed -n 1p "$1/pid" 2>/dev/null) || holder=""
    else
        holder=$(sed -n 1p "$1" 2>/dev/null) || holder=""
        [ -n "$holder" ] || holder=none
    fi
    printf '%s\n' "$holder"
}

# Who holds the folder lock at $1 (lock_holder_at), once it holds a pid: a
# lock without one ("" for a folder, "none" for a file) is read again every
# 0.05 s for up to a second, as the build or sync that has just made it
# writes its pid next. Prints what it holds then (still "" or "none" when no
# pid came), or "gone" when the lock went meanwhile. As cli sync's
# _settled_lock_holder: a build and a sync can find the same lock.
settled_lock_holder() {
    local holder tries=0
    while :; do
        if [ ! -e "$1" ] && [ ! -L "$1" ]; then
            echo gone
            return 0
        fi
        holder=$(lock_holder_at "$1")
        if { [ -n "$holder" ] && [ "$holder" != none ]; } || [ "$tries" -ge 20 ]; then
            printf '%s\n' "$holder"
            return 0
        fi
        sleep 0.05 2>/dev/null || tries=20
        tries=$((tries + 1))
    done
}

# The folder a build or sync holds while it takes a stale lock over
# (take_over_stale_lock); cli sync uses the same one.
LOCK_TAKEOVER="$BUILD_LOCK.takeover"

# True when LOCK_TAKEOVER is more than a minute old: a run that was killed
# while it held it left it. find -mmin +1 counts in whole minutes (it matches
# only after two), so stat gives the seconds where it can.
takeover_left_over() {
    local made
    case "$STAT_TREE" in
        bsd) made=$(stat -f %m "$LOCK_TAKEOVER" 2>/dev/null) || made="" ;;
        *) made=$(stat -c %Y "$LOCK_TAKEOVER" 2>/dev/null) || made="" ;;
    esac
    case "$made" in
        ""|*[!0123456789]*) [ -n "$(find "$LOCK_TAKEOVER" -maxdepth 0 -mmin +1 2>/dev/null)" ] ;;
        *) [ $(($(date +%s) - made)) -gt 60 ] ;;
    esac
}

# Takes away the lock that $1 left (a pid no longer running, or "none"), so
# this build can make its own; the mkdir that follows decides which build or
# sync goes on (the other finds that lock busy, settled_lock_holder). Two that
# both found the lock stale take turns: each first makes LOCK_TAKEOVER, and
# only the one that made it looks again and takes the lock away, and only
# while it is still the one $1 left. One that finds LOCK_TAKEOVER there waits
# up to 2 s for it to go, then tries its mkdir; one more than a minute old was
# left by a run that was killed while it held it, and is removed (and this
# build makes it again to take the lock over). So no run
# takes away a lock another run has just made (a check and then a delete let
# both delete it, and both went on). The lock goes with one rename, so it is
# never seen half deleted. Returns 1 when it cannot be taken away (a
# read-only .ngargparser/). The same protocol as cli sync's
# _take_over_stale_lock, with the same folder and waits.
take_over_stale_lock() {
    local tries=0 aside rc=0 reason
    if ! reason=$(LC_ALL=C command mkdir "$LOCK_TAKEOVER" 2>&1); then
        # Made by another run, which may be done with it already: the mkdir
        # that follows decides. Any other failure (a read-only .ngargparser/)
        # means the lock cannot be taken away.
        case "$reason" in
            *"File exists"*) ;;
            *) return 1 ;;
        esac
        while [ "$tries" -lt 40 ]; do
            [ -e "$LOCK_TAKEOVER" ] || return 0
            if takeover_left_over; then
                # Removed, and taken now, so the stale lock still goes. One
                # that cannot be removed (a read-only .ngargparser/) would be
                # found again and again: the lock cannot be taken away.
                if ! rmdir "$LOCK_TAKEOVER" 2>/dev/null &&
                    { [ -e "$LOCK_TAKEOVER" ] || [ -L "$LOCK_TAKEOVER" ]; }; then
                    return 1
                fi
                take_over_stale_lock "$1"
                return
            fi
            sleep 0.05 2>/dev/null || break
            tries=$((tries + 1))
        done
        return 0
    fi
    if { [ -e "$BUILD_LOCK" ] || [ -L "$BUILD_LOCK" ]; } && [ "$(lock_holder_at "$BUILD_LOCK")" = "$1" ]; then
        if aside=$(mktemp -d "$STATE_DIR/build.lock.stale-XXXXXX" 2>/dev/null); then
            # Removed by hand meanwhile is no failure: the mkdir decides.
            if ! mv "$BUILD_LOCK" "$aside/build.lock" 2>/dev/null &&
                { [ -e "$BUILD_LOCK" ] || [ -L "$BUILD_LOCK" ]; }; then
                rc=1
            fi
            rm_tree "$aside" 2>/dev/null || true
        else
            rc=1
        fi
    fi
    rmdir "$LOCK_TAKEOVER" 2>/dev/null || true
    return "$rc"
}

pid_alive() {
    case "$1" in
        ""|*[!0123456789]*) return 1 ;;
    esac
    kill -0 "$1" 2>/dev/null && return 0
    # kill -0 also fails for another user's process; ps tells them apart.
    command -v ps >/dev/null 2>&1 && ps -p "$1" >/dev/null 2>&1
}

# Call at top level only.
take_build_lock() {
    local holder attempt reason rc=0
    if { [ -e "$STATE_DIR" ] || [ -L "$STATE_DIR" ]; } && [ ! -d "$STATE_DIR" ]; then
        # No chmod makes a folder of it.
        error "Cannot create .ngargparser/build.lock: .ngargparser is a file, not a folder. Move it away (mv .ngargparser .ngargparser.old), then run the command again. Nothing was changed."
        exit 1
    fi
    mkdir -p "$STATE_DIR"
    # A folder lock left by a build without flock is handled as a folder lock.
    if command -v flock >/dev/null 2>&1 && [ ! -d "$BUILD_LOCK" ]; then
        open_lock_file
        # flock exits 1 when another process holds the lock, and with another
        # code when the file system cannot lock (some network file systems).
        flock -n 9 2>/dev/null || rc=$?
        if [ "$rc" = 0 ]; then
            # The pid is for the message another build prints; a file only
            # its owner may write keeps the old one.
            { printf '%s\n' "$$" > "$BUILD_LOCK"; } 2>/dev/null || true
            return 0
        fi
        if [ "$rc" = 1 ]; then
            # A pid that is not running is an old one the holder could not
            # replace (a file only its owner may write): not named.
            holder=$(sed -n 1p "$BUILD_LOCK" 2>/dev/null) || holder=""
            pid_alive "$holder" || holder=""
            lock_busy "$holder"
        fi
        # No build can hold a lock through this file here, so the folder
        # lock takes its place.
        exec 9>&-
        rm -f "$BUILD_LOCK" 2>/dev/null || true
    fi
    for attempt in 1 2; do
        if reason=$(mkdir "$BUILD_LOCK" 2>&1); then
            printf '%s\n' "$$" > "$BUILD_LOCK/pid"
            BUILD_LOCK_DIR="$BUILD_LOCK"
            return 0
        fi
        # Nothing there, and no folder made: .ngargparser/ is not writable.
        reason=${reason##*: }
        if [ "$reason" != "File exists" ] && [ ! -e "$BUILD_LOCK" ] && [ ! -L "$BUILD_LOCK" ]; then
            lock_unusable create "$reason"
        fi
        holder=$(settled_lock_holder "$BUILD_LOCK")
        # The lock went while this build waited for its pid: make it again.
        [ "$holder" != gone ] || continue
        # A running pid holds the lock. One that is no longer running is a
        # build that was killed: its lock is taken over. An empty pid after
        # the wait is a build killed before it wrote its pid, or one slow to.
        if pid_alive "$holder"; then
            lock_busy "$holder"
        fi
        if [ "$attempt" = 1 ] && [ -n "$holder" ] && take_over_stale_lock "$holder"; then
            continue
        fi
        lock_left_over "$holder"
    done
    # The lock went and came back on both tries: another build or sync runs.
    lock_busy ""
}

release_build_lock() {
    if [ -n "$BUILD_LOCK_DIR" ] && [ "$(sed -n 1p "$BUILD_LOCK_DIR/pid" 2>/dev/null)" = "$$" ]; then
        rm_tree "$BUILD_LOCK_DIR"
    fi
    BUILD_LOCK_DIR=""
    return 0
}

# ---------------------------------------------------------------------------
# distignore engine.
# The exclusion file (.distignore at the project root) has EXACT .gitignore
# semantics, evaluated by `git check-ignore` against a throwaway repo whose
# .gitignore is composed as:
#   1. '.*'                      built-in baseline: hidden files do not ship
#                                (lowest precedence: '!' rules can re-include)
#   2. the user's exclusion file, verbatim (DEFAULT_DISTIGNORE when the
#      tool has none)
# The forced paths below always ship, whatever the verdict; the folders on the
# way to them are copied too, but their other contents keep their verdict, so
# a rule such as '/deploy/' drops everything in deploy/ except install.sh.
# The project-root folders hard_excluded names (build/, .git/, the tool's
# virtualenvs and .ngargparser/) are hard-pruned from the candidate list, so
# no rule (not even '!') can bring them back. A .git below the top level is
# left out after the rules, whatever they say (exclude_nested_git). Entries fetched by
# scripts/get_libs.sh are not filtered: they ship as get_libs.sh left them.
# ---------------------------------------------------------------------------
# The tarball contract: the deploy orchestrator requires README and
# deploy/install.sh at the tarball top level, and uv on the deploy host reads
# .python-version for the Python to install. Top-level paths only: a nested
# README or deploy/ follows the rules like any other file. One per line,
# folders with a trailing '/'.
forced_paths() {
    printf '%s\n' README deploy/ deploy/install.sh .python-version
}

# The top-level names that never ship, one per line: the build output, the git
# repository, the tool's virtualenv, and the build's own state. uv sync on the
# deploy host makes .venv itself and refuses one that is not a valid Python
# environment there, and a whitelist .distignore ('*', then '!*/') would
# otherwise bring them back, since git keeps .venv out only through the
# .gitignore uv writes inside it. venv/, env/ and .virtualenv/, the other
# names ./configure looks for a virtualenv under (VENV_CANDIDATE_NAMES), are
# left out when they hold one (a pyvenv.cfg), so a folder of that name that is
# no virtualenv follows the rules. So is a conda environment (conda_envs).
hard_excluded() {
    local name
    {
        printf '%s\n' build .git .venv .ngargparser
        for name in venv env .virtualenv; do
            if [ -f "$PROJECT_ROOT/$name/pyvenv.cfg" ]; then
                printf '%s\n' "$name"
            fi
        done
        conda_envs
    } | awk '!seen[$0]++'
    return 0
}

# The top-level folders that hold a conda environment (conda create -p ./env),
# one per line: those with a conda-meta/ folder. Like a virtualenv, one is
# gigabytes of packages made for this machine, and the deploy host makes its
# own .venv. A link is copied as a link, so it is left to the rules.
conda_envs() {
    local path name
    for path in "$PROJECT_ROOT"/*/; do
        name=${path%/}
        name=${name##*/}
        case "$name" in *"$NL"*) continue ;; esac
        if [ ! -L "$PROJECT_ROOT/$name" ] && [ -d "$PROJECT_ROOT/$name/conda-meta" ]; then
            printf '%s\n' "$name"
        fi
    done
    return 0
}

# A notice for each conda environment the rules would ship: the build leaves
# it out (hard_excluded).
report_conda_envs() {
    local envs name
    envs=$(conda_envs)
    [ -n "$envs" ] || return 0
    printf '%s\n' "$envs" | sed 's|$|/|' | rules_exclude > "$TMPWORK/conda-excluded"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        ! grep -qxF -- "$name" "$TMPWORK/conda-excluded" || continue
        notice "Notice: $name/ holds a conda environment (conda-meta/), which never ships, whatever .distignore says: the deploy host makes its own .venv with uv sync."
    done <<< "$envs"
    return 0
}

# True when the PROJECT_ROOT-relative path $1 always ships.
is_forced() {
    in_list "$1" "$(forced_paths)"
}

# The rules a tool without .distignore gets: those of the .distignore that
# `cli generate` writes (ngargparser/templates/distignore), so a lost file
# does not ship scripts/, tests/ and the Makefile.
DEFAULT_DISTIGNORE='/Makefile
/scripts/
/README.md
/setup_*_env.sh
/tests/
__pycache__/
*.egg-info/
*.bak'

# git check-ignore in the throwaway repo $1, with the arguments after it. The
# settings that change its verdicts are pinned, so the build machine's git
# config cannot change what ships: core.excludesFile (a global ignore file
# would add rules) and core.ignorecase (true, set in a developer's global
# config or by git init on a case-insensitive file system, makes '*.TXT'
# exclude a.txt too). core.quotePath=off keeps names as they are in the
# output. Every git call against a rules repo after git init goes through
# here.
rules_check_ignore() {
    local repo="$1"
    shift
    git -C "$repo" -c core.quotePath=off -c core.excludesFile=/dev/null -c core.ignorecase=false \
        check-ignore "$@"
}

# Creates the throwaway repo $1 whose .gitignore holds the composed rules.
init_rules_repo() {
    git -c init.defaultBranch=main init -q "$1"
    # A user's init.templateDir may seed info/exclude; builds must not depend
    # on developer-machine git config.
    : > "$1/.git/info/exclude" 2>/dev/null || true

    local distignore=""
    if [ -f "$PROJECT_ROOT/.distignore" ]; then
        distignore="$PROJECT_ROOT/.distignore"
    fi

    # A UTF-8 byte order mark (some Windows editors add one) is dropped, as git
    # drops it from .gitignore; kept, it would become part of the first rule.
    # The carriage return is passed as a byte: BSD sed reads \r in a pattern as r.
    local bom cr
    bom=$(printf '\357\273\277')
    cr=$(printf '\r')
    {
        printf '.*\n'
        if [ -n "$distignore" ]; then
            # Line for line, so line N+1 here is line N of .distignore.
            sed -e "1s/^$bom//" -e "s/$cr\$//" "$distignore"
            # Terminate the last line even when the file lacks a trailing newline
            # (a blank line here is inert: .gitignore ignores blank lines).
            printf '\n'
        else
            printf '%s\n' "$DEFAULT_DISTIGNORE"
        fi
    } > "$1/.gitignore"
}

# Of the PROJECT_ROOT-relative paths on stdin (one per line, a trailing '/'
# for a folder), prints those the composed rules exclude, without the '/'.
# Judged before anything is removed, in a throwaway repo holding the composed
# rules and the folders on the way to those paths: a rule's verdict depends
# only on the path and whether it is a folder. Forced paths are judged too;
# callers skip them.
rules_exclude() {
    local tmp path status=0
    tmp=$(mktemp -d "$TMPWORK/rules.XXXXXX")
    init_rules_repo "$tmp/repo"
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        case "$path" in
            */) mkdir -p "$tmp/repo/$path" ;;
            */*) mkdir -p "$tmp/repo/${path%/*}" ;;
        esac
        printf '%s\0' "${path%/}"
    done > "$tmp/paths0"
    rules_check_ignore "$tmp/repo" -z --stdin < "$tmp/paths0" > "$tmp/ignored0" || status=$?
    if [ "$status" -le 1 ]; then
        tr '\0' '\n' < "$tmp/ignored0"
    fi
    rm -rf "$tmp"
    return 0
}

setup_exclusions() {
    IGNORE_REPO="$TMPWORK/repo"
    EXCLUDED_LIST="$TMPWORK/excluded"
    local candidates="$TMPWORK/candidates"

    init_rules_repo "$IGNORE_REPO"

    # The top-level entries that never ship, as find tests, and the names with
    # a line break, which one path per line cannot hold (check_tool_paths).
    HARD_EXCLUDED=$(hard_excluded)
    local prune=() name skip=()
    while IFS= read -r name; do
        [ ${#prune[@]} -eq 0 ] || prune+=(-o)
        prune+=(-path "./$name")
    done <<< "$HARD_EXCLUDED"
    prune+=(-o -name "*$NL*")
    # The folders the build may not read that the rules exclude
    # (check_tool_paths): listed, so they are judged, but not searched.
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        [ ${#skip[@]} -eq 0 ] || skip+=(-o)
        skip+=(-path "./$(printf '%s' "$name" | sed 's/[][*?\\]/\\&/g')")
    done <<< "$UNREADABLE_SKIPPED"
    # When there is none, a test no path gets to: names with a line break
    # are pruned before it.
    [ ${#skip[@]} -gt 0 ] || skip=(-name "*$NL*")

    # Candidate paths: everything in the project, PROJECT_ROOT-relative, but
    # the hard-excluded entries. Nested .git dirs are listed but not descended
    # into, so the '.*' baseline excludes them whole. The folders (each before
    # what is in it) and the other paths are kept apart for copy_plan.
    local dirs="$TMPWORK/dirs" files="$TMPWORK/files"
    (
        cd "$PROJECT_ROOT" && {
            find . -mindepth 1 \( "${prune[@]}" \) -prune -o \( "${skip[@]}" \) -prune -print -o \
                -type d -name .git -prune -print -o -type d -print \
                | sed 's|^\./||' > "$dirs"
            find . -mindepth 1 \( "${prune[@]}" \) -prune -o \( "${skip[@]}" \) -prune -o \
                -type d -name .git -prune -o ! -type d -print \
                | sed 's|^\./||' > "$files"
        }
    )
    cat "$dirs" "$files" > "$candidates"

    # The project's dirs also exist (empty) inside the throwaway repo, so git
    # judges a dir as a dir, as in a real repo: dir-only patterns ('name/')
    # match it, and 'name/*' matches only what is inside it, not the dir
    # itself. Nested .git dirs are not created; '.*' excludes them by name.
    grep -v '\(^\|/\)\.git$' "$dirs" > "$TMPWORK/mkdirs" || true
    if [ -s "$TMPWORK/mkdirs" ]; then
        tr '\n' '\0' < "$TMPWORK/mkdirs" | (cd "$IGNORE_REPO" && xargs -0 mkdir -p)
    fi

    # Batch evaluation. Exit 0 = some paths ignored, 1 = none ignored (not an
    # error), >1 = fatal. Paths go in and come out NUL-separated: without -z,
    # git C-quotes names holding '"', '\' or a tab (core.quotePath does not stop
    # that), and the quoted form never matches the path. Excluded dirs get their
    # trailing '/' back, the form is_excluded expects.
    local status=0
    tr '\n' '\0' < "$candidates" > "$TMPWORK/candidates0"
    rules_check_ignore "$IGNORE_REPO" -z --stdin < "$TMPWORK/candidates0" > "$TMPWORK/ignored0" || status=$?
    if [ "$status" -gt 1 ]; then
        echo "ERROR: git check-ignore failed (exit $status) while evaluating distignore rules" >&2
        exit 1
    fi
    tr '\0' '\n' < "$TMPWORK/ignored0" > "$TMPWORK/ignored"
    awk 'NR == FNR { dir[$0] = 1; next } { print (($0 in dir) ? $0 "/" : $0) }' \
        "$dirs" "$TMPWORK/ignored" > "$TMPWORK/excluded-by-rules"
    # Forced paths, and the folders on the way to them, are never excluded.
    forced_paths > "$TMPWORK/forced"
    awk 'NR == FNR { forced[$0] = 1; next } !($0 in forced)' \
        "$TMPWORK/forced" "$TMPWORK/excluded-by-rules" > "$EXCLUDED_LIST"
}

# One notice per .distignore rule that would exclude a forced file: the build
# ships the file anyway. The rule is the one git names as deciding (for a file
# in an excluded folder, the folder's rule). The built-in '.*' (line 1) is not
# the user's, so a hidden forced file it matches gets no notice.
report_forced_rules() {
    local path hits="$TMPWORK/forced-hits"
    : > "$hits"
    while IFS= read -r path; do
        case "$path" in */) continue ;; esac
        if grep -qxF -- "$path" "$TMPWORK/excluded-by-rules"; then
            printf '%s\0' "$path" >> "$hits"
        fi
    done <<< "$(forced_paths)"
    [ -s "$hits" ] || return 0
    rules_check_ignore "$IGNORE_REPO" -z -v --stdin < "$hits" > "$TMPWORK/forced-rules0" || true
    # -z -v prints source, line, pattern and path per path, each NUL-terminated.
    tr '\0' '\n' < "$TMPWORK/forced-rules0" | awk '
        NR % 4 == 2 { line = $0 }
        NR % 4 == 3 { pattern = $0 }
        NR % 4 == 0 && line != 1 {
            if (!(line in rule)) { order[++n] = line; rule[line] = pattern; count[line] = 0 }
            paths[line, ++count[line]] = $0
        }
        END {
            for (i = 1; i <= n; i++) {
                line = order[i]
                list = paths[line, 1]
                for (j = 2; j <= count[line]; j++)
                    list = list ((j == count[line]) ? " and " : ", ") paths[line, j]
                print "Notice: .distignore rule '\''" rule[line] "'\'' has no effect on " list \
                    ": the build always ships " ((count[line] == 1) ? "it." : "them.")
            }
        }' | while IFS= read -r path; do
        notice "$path"
    done
    return 0
}

# Entries fetched by scripts/get_libs.sh ship as it left them, so a
# .distignore rule that matches something in one does nothing. One notice per
# such rule, naming the first path it matches in sorted order (the top of an
# excluded subtree) and how many more. The built-in '.*' (line 1) is not the
# user's, and .git and __pycache__ (and .github and .gitlab at the top of an
# entry) are stripped from the build copy anyway, so they are left out.
report_fetched_rules() {
    [ -f "$PROJECT_ROOT/.distignore" ] || return 0
    local fetched hits="$TMPWORK/fetched-hits" line
    fetched=$(stamp_names)
    [ -n "$fetched" ] || return 0
    FETCHED="$fetched" awk '
        BEGIN {
            n = split(ENVIRON["FETCHED"], names, "\n")
            for (i = 1; i <= n; i++) if (names[i] != "") fetched[names[i]] = 1
        }
        { excluded[$0] = 1; paths[NR] = $0 }
        END {
            for (i = 1; i <= NR; i++) {
                path = paths[i]
                bare = path
                sub(/\/$/, "", bare)
                if (bare !~ /^libs\//) continue
                top = substr(bare, 6)
                sub(/\/.*$/, "", top)
                if (!(top in fetched)) continue
                # The entry itself is a top, also when a rule excludes libs/.
                parent = bare
                if (sub(/\/[^\/]*$/, "", parent) && parent != "libs" && ((parent "/") in excluded)) continue
                name = bare
                sub(/^.*\//, "", name)
                if (name == ".git" || name == "__pycache__") continue
                if ((name == ".github" || name == ".gitlab") && bare == "libs/" top "/" name) continue
                print bare
            }
        }' "$TMPWORK/excluded-by-rules" | sort | tr '\n' '\0' > "$hits"
    [ -s "$hits" ] || return 0
    rules_check_ignore "$IGNORE_REPO" -z -v --stdin < "$hits" > "$TMPWORK/fetched-rules0" || true
    # -z -v prints source, line, pattern and path per path, each NUL-terminated.
    tr '\0' '\n' < "$TMPWORK/fetched-rules0" | awk '
        NR % 4 == 2 { line = $0 }
        NR % 4 == 3 { pattern = $0 }
        NR % 4 == 0 && line != 1 {
            if (!(line in rule)) { order[++n] = line; rule[line] = pattern; first[line] = $0; count[line] = 0 }
            count[line]++
        }
        END {
            for (i = 1; i <= n; i++) {
                line = order[i]
                more = (count[line] > 1) ? " (and " (count[line] - 1) " more)" : ""
                print "Notice: .distignore rule '\''" rule[line] "'\'' has no effect on " first[line] more \
                    ": what scripts/get_libs.sh fetched ships as it left it. Delete what should not ship in scripts/get_libs.sh."
            }
        }' | while IFS= read -r line; do
        notice "$line"
    done
    return 0
}

# Lists what .distignore keeps out of the build: the top of each excluded
# subtree, not every file in it. Hidden names (the built-in '.*' rule) and
# entries fetched by scripts/get_libs.sh (they ship as fetched) are left out.
# Progress mode (make build) shows the first 20, verbose mode all of them.
report_exclusions() {
    local roots count shown rules
    roots=$(FETCHED="$(stamp_names)" awk '
        BEGIN {
            n = split(ENVIRON["FETCHED"], names, "\n")
            for (i = 1; i <= n; i++) if (names[i] != "") fetched[names[i]] = 1
        }
        { excluded[$0] = 1; paths[NR] = $0 }
        END {
            for (i = 1; i <= NR; i++) {
                path = paths[i]
                bare = path
                sub(/\/$/, "", bare)
                parent = bare
                if (sub(/\/[^\/]*$/, "", parent) && ((parent "/") in excluded)) continue
                name = bare
                sub(/^.*\//, "", name)
                if (name ~ /^\./) continue
                if (bare ~ /^libs\//) {
                    top = substr(bare, 6)
                    sub(/\/.*$/, "", top)
                    if (top in fetched) continue
                }
                print path
            }
        }' "$EXCLUDED_LIST" | LC_ALL=C sort)
    [ -n "$roots" ] || return 0
    count=$(printf '%s\n' "$roots" | wc -l | tr -d '[:space:]')
    # Without a .distignore the build applies its default rules (see init_rules_repo).
    rules=".distignore"
    [ -f "$PROJECT_ROOT/.distignore" ] || rules="the default rules"
    if [ "$count" -eq 1 ]; then
        notice "Excluded by $rules: 1 path"
    else
        notice "Excluded by $rules: $count paths"
    fi
    shown=$count
    if [ "$PROGRESS_MODE" = true ] && [ "$count" -gt 20 ]; then
        shown=20
    fi
    # sed reads all its input, so no early exit can break the pipe (pipefail).
    printf '%s\n' "$roots" | sed -n "1,${shown}s/^/  /p"
    if [ "$shown" -lt "$count" ]; then
        echo "  ... and $((count - shown)) more (make build-verbose lists them all)"
    fi
    return 0
}

# The temporary files a cli command writes next to a file and renames over
# it: <name>.ngargparser-restore (cli sync's undo), and .<name>.<8 random
# characters>.tmp or .ngargparser-sync (its other writes, made with Python's
# tempfile.mkstemp). One that is still there was left by a command that was
# stopped (kill -9, a power cut). They never ship, whatever .distignore says,
# and a notice names them: a .ngargparser-restore file may hold the only copy
# of a file as it was before the sync. What scripts/get_libs.sh fetched ships
# as it is, so it is left out of the search. Call after report_exclusions,
# which does not list them.
exclude_cli_leftovers() {
    local leftovers="$TMPWORK/cli-leftovers" path
    FETCHED="$(stamp_names)" awk '
        BEGIN {
            n = split(ENVIRON["FETCHED"], names, "\n")
            for (i = 1; i <= n; i++) if (names[i] != "") fetched[names[i]] = 1
            c = "[abcdefghijklmnopqrstuvwxyz0123456789_]"
            temporary = "^[.].+[.]" c c c c c c c c "[.](tmp|ngargparser-sync)$"
        }
        {
            if ($0 ~ /^libs\//) {
                top = substr($0, 6)
                sub(/\/.*$/, "", top)
                if (top in fetched) next
            }
            name = $0
            sub(/^.*\//, "", name)
            if (name ~ /[.]ngargparser-restore$/ || name ~ temporary) print
        }' "$TMPWORK/files" | LC_ALL=C sort > "$leftovers"
    [ -s "$leftovers" ] || return 0
    cat "$leftovers" >> "$EXCLUDED_LIST"
    notice "Notice: a cli command that was stopped (such as cli sync) left these temporary files in the tool folder; they do not ship. Check them, then delete them:"
    while IFS= read -r path; do
        notice "  $path"
    done < "$leftovers"
    return 0
}

# A .git below the top of the tool folder (a clone in src/, a submodule's
# pointer file) never ships, whatever .distignore says: a clone's .git/config
# can hold a token (https://oauth2:<token>@...), and a deploy needs none of
# it. The built-in '.*' rule keeps it out, but a '!' rule can bring it back
# ('!/src/**', or '*' then '!*/'), and then the whole .git shipped. So each
# .git the rules would ship is excluded here, after them, and a notice names
# it. What scripts/get_libs.sh fetched is left out of the search: the build
# strips .git from it anyway (strip_vcs_and_caches). Call after
# setup_exclusions, before copy_plan.
exclude_nested_git() {
    local found="$TMPWORK/nested-git" path
    FETCHED="$(stamp_names)" awk '
        BEGIN {
            n = split(ENVIRON["FETCHED"], names, "\n")
            for (i = 1; i <= n; i++) if (names[i] != "") fetched[names[i]] = 1
        }
        FILENAME == ARGV[1] { excluded[$0] = 1; next }
        {
            name = $0
            sub(/^.*\//, "", name)
            if (name != ".git") next
            if ($0 ~ /^libs\//) {
                top = substr($0, 6)
                sub(/\/.*$/, "", top)
                if (top in fetched) next
            }
            # Shipped unless it, or a folder above it, is excluded.
            if (($0 in excluded) || (($0 "/") in excluded)) next
            up = $0
            while (sub(/\/[^\/]*$/, "", up)) if ((up "/") in excluded) next
            # Folders carry a trailing slash in the excluded list.
            print (FILENAME == ARGV[2]) ? $0 "/" : $0
        }' "$EXCLUDED_LIST" "$TMPWORK/dirs" "$TMPWORK/files" | sort > "$found"
    [ -s "$found" ] || return 0
    cat "$found" >> "$EXCLUDED_LIST"
    notice "Notice: .distignore would ship these .git paths, which never ship (a clone's .git/config can hold a token); the build left them out:"
    while IFS= read -r path; do
        notice "  ${path%/}"
    done < "$found"
    return 0
}

# $1 = PROJECT_ROOT-relative path; directories MUST carry a trailing '/'.
is_excluded() {
    grep -qxF -- "$1" "$EXCLUDED_LIST"
}

# Names with a line break, and folders the build may not read, found before
# anything is removed. The build's lists hold one path per line, so a name
# with a line break read as two paths ($'README\ntests' made tests/ ship),
# and the copy stopped at a folder it could not read, whatever the rules
# said. Each name with a line break the rules would ship, and each such
# folder that would ship, stops the build here with one error. The others
# never ship: the lists leave out a name with a line break, and the folder it
# is in is walked (NEWLINE_WALK), not copied whole; an unreadable folder the
# rules exclude is not searched (UNREADABLE_SKIPPED). What
# scripts/get_libs.sh fetched is copied whole, so a line break in it is no
# trouble. Call at top level only.
NEWLINE_WALK=""
UNREADABLE_SKIPPED=""
check_tool_paths() {
    local tmp="$TMPWORK/paths-check" prune=() name path top status=0 count i j
    local found=() ignored=() newline=() unreadable=() fetched is_ignored
    mkdir -p "$tmp"
    NEWLINE_WALK="$tmp/walk"
    : > "$NEWLINE_WALK"
    while IFS= read -r name; do
        [ ${#prune[@]} -eq 0 ] || prune+=(-o)
        prune+=(-path "./$name")
    done <<< "$(hard_excluded)"
    # The first name with a line break on each path. find fails on a folder
    # it cannot read: only then are the folders tested one by one.
    (cd "$PROJECT_ROOT" && find . -mindepth 1 \( "${prune[@]}" \) -prune -o -type d -name .git -prune -o \
        -name "*$NL*" -prune -print0) > "$tmp/newline0" 2>/dev/null || status=$?
    while IFS= read -r -d '' path; do
        found+=("${path#./}")
    done < "$tmp/newline0"
    count=${#found[@]}
    if [ "$status" -ne 0 ]; then
        (cd "$PROJECT_ROOT" && find . -mindepth 1 \( "${prune[@]}" \) -prune -o -type d -name .git -prune -o \
            -name "*$NL*" -prune -o -type d -print0) > "$tmp/dirs0" 2>/dev/null || true
        while IFS= read -r -d '' path; do
            path=${path#./}
            if [ ! -r "$PROJECT_ROOT/$path" ] || [ ! -x "$PROJECT_ROOT/$path" ]; then
                found+=("$path")
            fi
        done < "$tmp/dirs0"
    fi
    [ ${#found[@]} -gt 0 ] || return 0
    # The verdicts, in a throwaway repo holding the composed rules and the
    # folders on the way (rules_exclude explains why that is enough).
    init_rules_repo "$tmp/repo"
    : > "$tmp/judge0"
    for path in "${found[@]}"; do
        case "$path" in */*) mkdir -p "$tmp/repo/${path%/*}" ;; esac
        if [ -d "$PROJECT_ROOT/$path" ] && [ ! -L "$PROJECT_ROOT/$path" ]; then
            mkdir -p "$tmp/repo/$path"
        fi
        printf '%s\0' "$path" >> "$tmp/judge0"
    done
    status=0
    rules_check_ignore "$tmp/repo" -z --stdin < "$tmp/judge0" > "$tmp/ignored0" || status=$?
    if [ "$status" -gt 1 ]; then
        error "git check-ignore failed (exit $status) while evaluating distignore rules"
        exit 1
    fi
    while IFS= read -r -d '' path; do
        ignored+=("$path")
    done < "$tmp/ignored0"
    fetched=$(stamp_names)
    for ((i = 0; i < ${#found[@]}; i++)); do
        path=${found[$i]}
        top=""
        case "$path" in libs/*) top=${path#libs/} && top=${top%%/*} ;; esac
        if [ -n "$top" ] && in_list "$top" "$fetched"; then
            # Copied whole, as fetched: a folder it cannot read stops the copy.
            [ "$i" -lt "$count" ] || unreadable+=("$(printf '%q' "$path")")
            continue
        fi
        is_ignored=false
        for ((j = 0; j < ${#ignored[@]}; j++)); do
            if [ "${ignored[$j]}" = "$path" ]; then
                is_ignored=true
                break
            fi
        done
        if [ "$i" -lt "$count" ]; then
            if [ "$is_ignored" = true ]; then
                case "$path" in */*) printf '%s\n' "${path%/*}" >> "$NEWLINE_WALK" ;; esac
            else
                newline+=("$(printf '%q' "$path")")
            fi
        elif [ "$is_ignored" = true ] && ! is_forced "$path/"; then
            UNREADABLE_SKIPPED+="$path$NL"
        else
            unreadable+=("$(printf '%q' "$path")")
        fi
    done
    if [ ${#newline[@]} -gt 0 ]; then
        error "These names in the tool folder hold a line break, which the build cannot copy: ${newline[*]}. Rename them, or exclude them in .distignore. Nothing was changed."
        exit 1
    fi
    if [ ${#unreadable[@]} -gt 0 ]; then
        error "The build may not read these folders, which would ship: ${unreadable[*]}. Make them readable (chmod -R u+rX <folder>), or exclude them in .distignore. Nothing was changed."
        exit 1
    fi
    return 0
}

# Output a run wrote into the tool folder (predict -o out/res) ships when no
# rule excludes it. When the tool folder is in a git checkout, a notice names
# each path, at any depth, the build copied that git neither tracks nor
# ignores (a folder git tracks nothing in by its name), so the developer sees
# it. The first 10 are named. What ships does not change. Call after
# copy_planned.
report_untracked() {
    local path top last="" fetched candidates=() shipped=() count=0
    git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
    # --no-empty-directory leaves out a folder git ignores all of, such as a
    # libs/ that holds only what get_libs.sh fetched.
    (cd "$PROJECT_ROOT" && git ls-files -z --others --exclude-standard --directory --no-empty-directory) \
        > "$TMPWORK/untracked0" 2>/dev/null || return 0
    while IFS= read -r -d '' path; do
        path=${path%/}
        case "$path" in
            # '.': git tracks nothing in the tool folder (a parent repo's).
            ""|.) continue ;;
            # The build always makes libs/: name the entries in it instead.
            libs)
                (cd "$PROJECT_ROOT" && git ls-files -z --others --exclude-standard -- libs) \
                    > "$TMPWORK/untracked-libs0" 2>/dev/null || continue
                while IFS= read -r -d '' path; do
                    top=${path#libs/}
                    path=libs/${top%%/*}
                    [ "$path" = "$last" ] || candidates+=("$path")
                    last=$path
                done < "$TMPWORK/untracked-libs0"
                ;;
            *) candidates+=("$path") ;;
        esac
    done < "$TMPWORK/untracked0"
    # The fetched entries ship as get_libs.sh left them, whatever git says.
    fetched="$(stamp_names)"
    for path in "${candidates[@]}"; do
        case "$path" in
            libs/*)
                top=${path#libs/}
                if in_list "${top%%/*}" "$fetched"; then continue; fi
                ;;
        esac
        if [ -e "$BUILD_DIR/$path" ] || [ -L "$BUILD_DIR/$path" ]; then
            count=$((count + 1))
            [ "$count" -gt 10 ] || shipped+=("$path")
        fi
    done
    [ "$count" -gt 0 ] || return 0
    notice "Notice: these ship in the tarball, but git does not track them (output written into the tool folder?): commit them, delete them, or exclude them in .distignore:"
    for path in "${shipped[@]}"; do
        notice "  $(printf '%q' "$path")"
    done
    [ "$count" -le 10 ] || notice "  ... and $((count - 10)) more"
    return 0
}

# What the build copies, worked out once from the candidate lists and the
# verdicts, so the copy takes a few cp calls however many files a folder with
# an exclusion holds. A folder with nothing excluded inside is copied whole,
# empty folders and all. A folder with an exclusion inside is walked: each
# entry in it is judged on its own, and the folder itself is made only when
# something is copied into it, so a folder emptied by exclusion is dropped.
# The top level is walked; so is libs/, merged into the build's own libs/,
# where the entries scripts/get_libs.sh fetched are copied whole, whatever the
# rules say (they ship as get_libs.sh left them). Prints, for each walked
# folder that gets something, "D<folder>" (PROJECT_ROOT-relative, empty for
# the top), then one line per entry copied into it: "d<path>" for a whole
# folder, "l<path>" for a fetched entry, "f<path>" for anything else (a file,
# or a symlink, which is copied as a link).
copy_plan() {
    FETCHED="$(stamp_names)" awk '
        BEGIN {
            n = split(ENVIRON["FETCHED"], names, "\n")
            for (i = 1; i <= n; i++) if (names[i] != "") fetched[names[i]] = 1
        }
        function parent(p) { return sub(/\/[^\/]*$/, "", p) ? p : "" }
        function add(folder, line) {
            if (!(folder in count)) { count[folder] = 0; order[++folders] = folder }
            entry[folder, ++count[folder]] = line
        }
        # The folders that hold a name with a line break the rules exclude:
        # the lists leave it out, so they and the folders above are walked.
        FILENAME == ARGV[1] {
            p = $0
            inside[p] = 1
            while (sub(/\/[^\/]*$/, "", p)) inside[p] = 1
            next
        }
        # The excluded paths: every folder above one has an exclusion inside.
        FILENAME == ARGV[2] {
            excluded[$0] = 1
            p = $0
            sub(/\/$/, "", p)
            while (sub(/\/[^\/]*$/, "", p)) inside[p] = 1
            next
        }
        # The folders, each before what is in it. state: walk, copy or out.
        FILENAME == ARGV[3] {
            up = parent($0)
            if (up != "" && state[up] != "walk") next
            if ($0 == "libs") { state[$0] = "walk"; next }
            if (up == "libs" && (substr($0, 6) in fetched)) { state[$0] = "copy"; add(up, "l" $0); next }
            if (($0 "/") in excluded) { state[$0] = "out"; next }
            if ($0 in inside) { state[$0] = "walk"; next }
            state[$0] = "copy"
            add(up, "d" $0)
            next
        }
        {
            up = parent($0)
            if (up != "" && state[up] != "walk") next
            if (up == "libs" && (substr($0, 6) in fetched)) { add(up, "l" $0); next }
            if (!($0 in excluded)) add(up, "f" $0)
        }
        END {
            for (i = 1; i <= folders; i++) {
                print "D" order[i]
                for (j = 1; j <= count[order[i]]; j++) print entry[order[i], j]
            }
        }' "${NEWLINE_WALK:-/dev/null}" "$EXCLUDED_LIST" "$TMPWORK/dirs" "$TMPWORK/files"
}

# The entries copy_planned collected for one folder, copied with one cp into
# the folder $1. 'cp -RPp' copies a symlink as a link (never what it points
# at) and keeps modes and timestamps, on GNU and BSD alike.
COPY_BATCH=()
copy_batch() {
    [ ${#COPY_BATCH[@]} -gt 0 ] || return 0
    local rc=0
    cp -RPp "${COPY_BATCH[@]}" "$1" || rc=$?
    if [ "$rc" -ne 0 ]; then
        # The lists copy_plan reads hold one path per line, so a name with a
        # line break in it reads as two paths that do not exist.
        if [ -n "$(cd "$PROJECT_ROOT" && find . -name "*$NL*" -print 2>/dev/null | sed -n 1p)" ]; then
            error "The tool folder has a file or folder whose name holds a line break, which the build cannot copy: rename it (in bash, find . -name \$'*\\n*' lists them)."
        fi
        exit 1
    fi
    COPY_BATCH=()
}

# Copies what copy_plan lists into the build copy: one cp per walked folder,
# or several when its list is long, as the command line has a length limit.
# A walked folder is made with mkdir -p before its entries are copied. Call
# at top level only.
copy_planned() {
    local line path dest="" size=0
    while IFS= read -r line; do
        path=${line#?}
        case "$line" in
            D*)
                copy_batch "$dest"
                dest="$BUILD_DIR/$path"
                mkdir -p "$dest"
                size=0
                continue
                ;;
            d*) log_verbose "Copying directory: $path" ;;
            l*) log_verbose "Copying fetched dependency: $path" ;;
            *) log_verbose "Copying file: $path" ;;
        esac
        if [ "$size" -gt 65536 ]; then
            copy_batch "$dest"
            size=0
        fi
        COPY_BATCH+=("$PROJECT_ROOT/$path")
        size=$((size + ${#PROJECT_ROOT} + ${#path} + 2))
    done < "$TMPWORK/copy-plan"
    copy_batch "$dest"
}

# ---------------------------------------------------------------------------
# Dependency fetch.
# scripts/get_libs.sh runs in an empty temp dir; each top-level entry it leaves
# there replaces libs/<entry> in the project. The stamp file records the hash
# of scripts/get_libs.sh, the TOOL_VERSION it ran with (get_libs.sh may fetch
# by version) and the names it produced:
#   line 1  format marker
#   line 2  hash <git hash-object of scripts/get_libs.sh> version <TOOL_VERSION>
#           ('hash pending' mid-move)
#   line 3+ <fingerprint> <name>, one per fetched entry ('-' mid-move); format 1
#           stamps hold the name alone
# The fetch is skipped while line 2 matches and every name is still there,
# unchanged: the same fingerprint, a checksum of each path in the entry with,
# for a file, its size and modification time (entry_fingerprint). A chmod, or
# a copy of the tool folder that keeps modification times (cp -a, cp -p,
# rsync -a, tar), is no change. An edit that keeps a file's size and its
# modification time, to the second, is not seen.
# ---------------------------------------------------------------------------

# True when $1 is a line of the newline-separated list $2.
in_list() {
    grep -qxF -- "$1" <<< "$2"
}

# True when the stamp's lines hold a fingerprint before the name.
stamp_has_fingerprints() {
    case "$(sed -n 1p "$LIBS_STAMP" 2>/dev/null)" in
        "$STAMP_FORMAT"|"$STAMP_FORMAT_2") return 0 ;;
    esac
    return 1
}

stamp_names() {
    [ -f "$LIBS_STAMP" ] || return 0
    if stamp_has_fingerprints; then
        sed -n '3,$s/^[^ ]* //p' "$LIBS_STAMP"
    else
        sed -n '3,$p' "$LIBS_STAMP"
    fi
    return 0
}

# The fingerprint the stamp records for the fetched entry $1, or nothing: a
# format 1 stamp has none, a stamp written mid-move holds '-'.
stamp_fingerprint() {
    [ -f "$LIBS_STAMP" ] && stamp_has_fingerprints || return 0
    NAME="$1" awk 'NR > 2 && substr($0, index($0, " ") + 1) == ENVIRON["NAME"] { f = substr($0, 1, index($0, " ") - 1) }
        END { if (f != "" && f != "-") print f }' "$LIBS_STAMP"
    return 0
}

# Moves libs/$1 to .ngargparser/removed-libs/<name>-<date>-<time> and says
# why: $2 is "changed" when it changed since the fetch, "unknown" when no
# fingerprint was recorded to tell. $3 is "replaced" when get_libs.sh fetched
# it again and the new copy takes its place ($2 is then "changed"), else
# get_libs.sh no longer fetches it.
keep_removed_lib() {
    local when dest n=1
    when=$(date +%Y%m%d-%H%M%S)
    dest="$STATE_DIR/removed-libs/$1-$when"
    while [ -e "$dest" ] || [ -L "$dest" ]; do
        n=$((n + 1))
        dest="$STATE_DIR/removed-libs/$1-$when-$n"
    done
    mkdir -p "$STATE_DIR/removed-libs"
    # Moving a folder into another one rewrites its '..', which needs write
    # permission on the folder itself.
    if [ -d "$LIBS_DIR/$1" ] && [ ! -L "$LIBS_DIR/$1" ]; then
        chmod u+w "$LIBS_DIR/$1"
    fi
    mv "$LIBS_DIR/$1" "$dest"
    if [ "${3:-}" = replaced ]; then
        notice "libs/$1 changed since it was fetched, so it was moved to ${dest#"$PROJECT_ROOT"/} before the new fetch replaced it."
    elif [ "$2" = changed ]; then
        notice "libs/$1 is no longer fetched by scripts/get_libs.sh. It changed since it was fetched, so it was moved to ${dest#"$PROJECT_ROOT"/} instead of deleted."
    else
        notice "libs/$1 is no longer fetched by scripts/get_libs.sh. It was moved to ${dest#"$PROJECT_ROOT"/} instead of deleted: the build has no record to tell whether it changed since it was fetched."
    fi
    return 0
}

# Whether find has -cnewer (GNU, BSD and macOS find do). A file's status
# change time moves on every write, chmod or replace, also when a tool sets
# the modification time back (cp -p, tar), so it is the better test for a
# write during get_libs.sh's run (check_libs_untouched) and for a format 2
# stamp; -newer (modification time) is the fallback.
if find "$PROJECT_ROOT" -maxdepth 0 -cnewer "$PROJECT_ROOT" >/dev/null 2>&1; then
    NEWER=-cnewer
else
    NEWER=-newer
fi

# How libs_stat_tree reads sizes and times: GNU find prints them itself;
# elsewhere stat does, in BSD form (macOS) or in GNU and busybox form.
if find "$PROJECT_ROOT" -maxdepth 0 -printf '' >/dev/null 2>&1; then
    STAT_TREE=gnu-find
elif stat -f '%z' "$PROJECT_ROOT" >/dev/null 2>&1; then
    STAT_TREE=bsd
else
    STAT_TREE=stat
fi

# Every path under libs/ (with $1, under libs/$1 and the entry itself), one per
# line, sorted, folders with a trailing '/'. .git and __pycache__ are left out
# at every depth: the build strips them from the copy, and running the tool
# from the project folder writes __pycache__.
libs_tree() {
    [ -d "$LIBS_DIR" ] || return 0
    local start=. depth=1
    if [ -n "${1:-}" ]; then
        start="./$1"
        depth=0
    fi
    (
        cd "$LIBS_DIR" && {
            find "$start" -mindepth "$depth" \( -name .git -o -name __pycache__ \) -prune -o -type d -print | sed 's|$|/|'
            find "$start" -mindepth "$depth" \( -name .git -o -name __pycache__ \) -prune -o ! -type d -print
        }
    ) | sed 's|^\./||' | LC_ALL=C sort
    return 0
}

# Paths under libs/ (with $1, under libs/$1) other than folders that changed
# after the file $2 was written, one per line, sorted.
libs_changed() {
    [ -d "$LIBS_DIR" ] || return 0
    local start=. depth=1
    if [ -n "$1" ]; then
        start="./$1"
        depth=0
    fi
    (cd "$LIBS_DIR" && find "$start" -mindepth "$depth" \( -name .git -o -name __pycache__ \) -prune -o \
        ! -type d "$NEWER" "$2" -print) | sed 's|^\./||' | LC_ALL=C sort
    return 0
}

# libs/$1, the entry itself and each path in it, one per line, sorted, as
# "<type> <size> <time> <path>": "f" with the size and the modification time
# in whole seconds for a file, "l" with the size (the length of its target)
# for a link, "d" for a folder and "o" for anything else, those two with '-'
# for both. A folder's size and time change in a copy made on another file
# system, and not every copy keeps a link's time. .git and __pycache__ are
# left out at every depth, as in libs_tree.
libs_stat_tree() {
    (
        cd "$LIBS_DIR" || exit 0
        case "$STAT_TREE" in
            gnu-find) find "./$1" \( -name .git -o -name __pycache__ \) -prune -o -printf '%y %s %T@ %p\n' ;;
            bsd) find "./$1" \( -name .git -o -name __pycache__ \) -prune -o -exec stat -f '%Sp %z %m %N' {} + ;;
            *) find "./$1" \( -name .git -o -name __pycache__ \) -prune -o -exec stat -c '%A %s %Y %n' {} + ;;
        esac
    ) | awk '{
        type = substr($1, 1, 1)
        size = $2
        time = $3
        sub(/\..*$/, "", time)
        path = $0
        sub(/^[^ ]* [^ ]* [^ ]* /, "", path)
        if (type == "f" || type == "-") print "f " size " " time " " path
        else if (type == "l") print "l " size " - " path
        else if (type == "d") print "d - - " path
        else print "o - - " path
    }' | LC_ALL=C sort
    return 0
}

# The fingerprint of the fetched entry libs/$1: a checksum of libs_stat_tree
# and, for a link, its target. It reads no file, so it stays fast on large
# libs.
entry_fingerprint() {
    {
        libs_stat_tree "$1"
        if [ -L "$LIBS_DIR/$1" ]; then
            readlink "$LIBS_DIR/$1"
        fi
    } | cksum | awk '{ print $1 "-" $2 }'
}

# The fingerprint a format 2 stamp holds: a checksum of the paths alone. The
# builds that wrote one caught changes inside a file with libs_changed.
entry_paths_fingerprint() {
    {
        libs_tree "$1"
        if [ -L "$LIBS_DIR/$1" ]; then
            readlink "$LIBS_DIR/$1"
        fi
    } | cksum | awk '{ print $1 "-" $2 }'
}

# Whether the fetched entry libs/$1 changed since the stamp was written:
# prints "changed", "same", or "unknown" when the stamp holds no fingerprint
# for it (a format 1 stamp, one written mid-move, or no stamp). A format 2
# stamp is checked as the builds that wrote it checked it: its paths, and
# each file whose status changed since it was written.
entry_state() {
    local fingerprint
    fingerprint=$(stamp_fingerprint "$1")
    if [ -z "$fingerprint" ]; then
        echo unknown
    elif [ "$(sed -n 1p "$LIBS_STAMP")" = "$STAMP_FORMAT_2" ]; then
        if [ "$(entry_paths_fingerprint "$1")" != "$fingerprint" ] ||
            [ -n "$(libs_changed "$1" "$LIBS_STAMP" | sed -n 1p)" ]; then
            echo changed
        else
            echo same
        fi
    elif [ "$(entry_fingerprint "$1")" != "$fingerprint" ]; then
        echo changed
    else
        echo same
    fi
    return 0
}

# Names of the libs/ entries this build fetched, so it may replace them. The
# stamp is the record; when it is lost (a fresh clone with libs/ copied in, or
# .ngargparser/ deleted), the managed libs/.gitignore lists the same names. Its
# marker line shows the build wrote it, so hand-made entries stay protected.
owned_names() {
    if [ -f "$LIBS_STAMP" ]; then
        stamp_names
        return 0
    fi
    local gitignore="$LIBS_DIR/.gitignore"
    if [ -f "$gitignore" ] && [ "$(sed -n 1p "$gitignore")" = "$LIBS_GITIGNORE_MARKER" ]; then
        # Skip the marker and the self-ignore line, then undo gitignore_pattern:
        # drop the anchoring '/' and the glob escapes.
        sed -e '1d' -e '\|^/\.gitignore$|d' -e 's|^/||' -e 's/\\\(.\)/\1/g' "$gitignore"
    fi
    return 0
}

# $1 the hash line's value ('pending' mid-move), $2 the names, one per line.
# Each name's fingerprint is taken now, so write the stamp after the entries
# are in place.
write_stamp() {
    local name
    {
        echo "$STAMP_FORMAT"
        echo "hash $1"
        while IFS= read -r name; do
            [ -n "$name" ] || continue
            if [ "$1" = pending ]; then
                printf -- '- %s\n' "$name"
            else
                printf '%s %s\n' "$(entry_fingerprint "$name")" "$name"
            fi
        done <<< "$2"
    } > "$LIBS_STAMP.tmp"
    mv -f "$LIBS_STAMP.tmp" "$LIBS_STAMP"
}

# Explicit returns only: this runs as an `if` condition, where set -e is off.
# A fetched entry edited since the fetch (a file added, removed or changed)
# is fetched again, so what ships is what get_libs.sh fetched.
libs_up_to_date() {
    [ -f "$LIBS_STAMP" ] || return 1
    local name changed=no
    case "$(sed -n 1p "$LIBS_STAMP")" in
        "$STAMP_FORMAT"|"$STAMP_FORMAT_2"|"$STAMP_FORMAT_1") ;;
        *) return 1 ;;
    esac
    [ "$(sed -n 2p "$LIBS_STAMP")" = "hash $1" ] || return 1
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        { [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; } || return 1
        # A format 1 stamp has no fingerprints: trusted, then upgraded.
        if [ "$(entry_state "$name")" = changed ]; then
            notice "libs/$name changed since it was fetched; fetching again"
            changed=yes
        fi
    done <<< "$(stamp_names)"
    [ "$changed" = no ] || return 1
    return 0
}

# One anchored .gitignore pattern for a top-level libs/ entry, glob characters
# escaped, and spaces too: git drops a trailing space that is not escaped.
gitignore_pattern() {
    printf '/%s\n' "$(printf '%s' "$1" | sed 's/[][\\*? ]/\\&/g')"
}

# Keep fetched entries out of git without touching the project's own .gitignore:
# libs/ can also hold committed code, so only the fetched names are listed. The
# file ignores itself, like .pytest_cache/.gitignore and uv's .venv/.gitignore.
write_libs_gitignore() {
    local names="$1" gitignore="$LIBS_DIR/.gitignore" name
    if [ -f "$gitignore" ] && [ "$(sed -n 1p "$gitignore")" != "$LIBS_GITIGNORE_MARKER" ]; then
        # The project owns this file. Leave it, and only point out fetched
        # names git would still pick up.
        if git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            while IFS= read -r name; do
                [ -n "$name" ] || continue
                if ! git -C "$PROJECT_ROOT" check-ignore -q "libs/$name"; then
                    notice "Warning: libs/$name was fetched by scripts/get_libs.sh but git does not ignore it (libs/.gitignore is yours, so it was left alone)."
                fi
            done <<< "$names"
        fi
        return 0
    fi
    if [ -z "$names" ]; then
        rm -f "$gitignore"
        return 0
    fi
    {
        echo "$LIBS_GITIGNORE_MARKER"
        echo "/.gitignore"
        while IFS= read -r name; do
            if [ -n "$name" ]; then
                gitignore_pattern "$name"
            fi
        done <<< "$names"
    } > "$gitignore"
}

# Compares the listings $1 (before) and $2 (after) of libs_tree, with $3 and
# $4 those of libs_changed before and after. Prints one line per difference,
# "<kind> <path>" without a folder's '/': N new, C changed, D deleted. A new or
# deleted folder stands for everything in it; a path that changed between file
# and folder is changed. Paths already changed before ($3) are left out.
libs_differences() {
    {
        sed 's/^/B /' "$1"
        sed 's/^/A /' "$2"
        sed 's/^/b /' "$3"
        sed 's/^/a /' "$4"
    } | awk '
        function bare(p) { sub(/\/$/, "", p); return p }
        # True when a folder above p is in the set named by which.
        function under(p, which,    up) {
            up = bare(p)
            while (sub(/\/[^\/]*$/, "", up)) {
                if (which == "new" && ((up "/") in added)) return 1
                if (which == "del" && ((up "/") in removed)) return 1
            }
            return 0
        }
        { tag = substr($0, 1, 1); p = substr($0, 3) }
        tag == "B" { before[p] = 1; blist[++nb] = p; next }
        tag == "A" { after[p] = 1; alist[++na] = p; next }
        tag == "b" { changed_before[p] = 1; next }
        tag == "a" { changed[p] = 1; next }
        END {
            for (i = 1; i <= na; i++) if (!(alist[i] in before)) { added[alist[i]] = 1; added_bare[bare(alist[i])] = 1 }
            for (i = 1; i <= nb; i++) if (!(blist[i] in after)) { removed[blist[i]] = 1; removed_bare[bare(blist[i])] = 1 }
            for (i = 1; i <= na; i++) {
                p = alist[i]
                if (p in added) {
                    if (under(p, "new")) continue
                    print ((bare(p) in removed_bare) ? "C " : "N ") bare(p)
                } else if ((p in changed) && !(p in changed_before)) {
                    print "C " p
                }
            }
            for (i = 1; i <= nb; i++) {
                p = blist[i]
                if ((p in removed) && !under(p, "del") && !(bare(p) in added_bare)) print "D " bare(p)
            }
        }'
}

# get_libs.sh must write into its working dir, never into libs/ itself: an
# entry written there is not recorded, kept out of git or ever cleaned up.
# Compares libs/ at every depth with the listings taken before the run: $1
# "yes" when libs/ existed, $2 the prefix of the listing files ($2.before,
# $2.changed-before), $3 the start marker, $4 get_libs.sh's exit code. Paths
# already changed after the marker before the run are left out, so a file
# saved just before the build is not blamed. New paths are removed again,
# which puts libs/ back as it was; changed or deleted ones cannot be restored
# and are listed as left. A change made within the same clock tick as the
# marker can be missed. Call at top level only.
check_libs_untouched() {
    local existed="$1" lists="$2" mark="$3" rc="$4"
    local report="" line kind path count
    libs_tree > "$lists.after"
    libs_changed "" "$mark" > "$lists.changed"
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        kind=${line%% *}
        path=${line#* }
        case "$kind" in
            N)
                rm_tree "$LIBS_DIR/$path"
                report+="  libs/$path: new, removed again"$'\n'
                ;;
            C) report+="  libs/$path: changed, left as it is now"$'\n' ;;
            D) report+="  libs/$path: deleted, not restored"$'\n' ;;
        esac
    done <<< "$(libs_differences "$lists.before" "$lists.after" "$lists.changed-before" "$lists.changed")"
    # A libs/ the script created is removed again once it is empty.
    if [ "$existed" != yes ] && [ -d "$LIBS_DIR" ] && [ ! -L "$LIBS_DIR" ] && [ -z "$(ls -A "$LIBS_DIR")" ]; then
        rmdir "$LIBS_DIR"
        [ -n "$report" ] || report="  libs/: new, removed again"$'\n'
    fi
    [ -n "$report" ] || return 0

    error "scripts/get_libs.sh wrote into libs/ directly. Write into the current directory instead: the build moves each top-level entry left there into libs/ and records it."
    # The first 20; sed reads all its input, so no early exit can break the pipe.
    printf '%s' "$report" | sed -n '1,20p' >&2
    count=$(printf '%s' "$report" | wc -l | tr -d '[:space:]')
    if [ "$count" -gt 20 ]; then
        echo "  ... and $((count - 20)) more" >&2
    fi
    if [ "$rc" -ne 0 ]; then
        echo "scripts/get_libs.sh also failed (exit $rc)." >&2
        show_get_libs_log
    fi
    exit 1
}

# In progress mode get_libs.sh's output goes to its log: after a failure, show
# the log's last 20 lines, or say that it is empty.
show_get_libs_log() {
    [ "$PROGRESS_MODE" = true ] || return 0
    if grep -q '[^[:space:]]' "$GET_LIBS_LOG" 2>/dev/null; then
        echo "--- last lines of .ngargparser/get_libs.log ---" >&2
        tail -n 20 "$GET_LIBS_LOG" >&2
    else
        echo "(get_libs.sh printed nothing)" >&2
    fi
    return 0
}

# The start marker and the libs/ listings kept next to the fetch's temp dir.
rm_libs_lists() {
    rm -f "$LIBS_TMP.start" "$LIBS_TMP.before" "$LIBS_TMP.changed-before" "$LIBS_TMP.after" "$LIBS_TMP.changed"
}

# Call at top level only (never as `fetch_libs || ...`), so set -e stays active.
fetch_libs() {
    if [ ! -f "$GET_LIBS_SCRIPT" ]; then
        show_progress "No scripts/get_libs.sh; nothing to fetch"
        log_verbose "No scripts/get_libs.sh; nothing to fetch."
        # What an earlier get_libs.sh fetched still ships (the stamp lists it),
        # so say so instead of leaving it to look like the tool's own code.
        local name
        while IFS= read -r name; do
            [ -n "$name" ] || continue
            if [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; then
                notice "libs/$name was fetched by a get_libs.sh that no longer exists; it still ships. Delete it, or restore scripts/get_libs.sh."
            fi
        done <<< "$(stamp_names)"
        return 0
    fi

    local hash
    hash="$(git hash-object --no-filters "$GET_LIBS_SCRIPT") version $TOOL_VERSION"
    if libs_up_to_date "$hash"; then
        # A stamp in an older format gets the current fingerprints now.
        if [ "$(sed -n 1p "$LIBS_STAMP")" != "$STAMP_FORMAT" ]; then
            write_stamp "$hash" "$(stamp_names)"
        fi
        show_progress "Libs up to date"
        log_verbose "Libs up to date (scripts/get_libs.sh, TOOL_VERSION and the fetched entries unchanged); skipping the fetch."
        return 0
    fi

    show_progress "Getting libs (log: .ngargparser/get_libs.log)"
    log_verbose "Getting libs with scripts/get_libs.sh..."
    mkdir -p "$STATE_DIR"
    local leftover
    for leftover in "$STATE_DIR"/libs-fetch.*; do  # from a build that was killed
        rm_tree "$leftover"
    done
    LIBS_TMP=$(mktemp -d "$STATE_DIR/libs-fetch.XXXXXX")
    export PROJECT_ROOT APP_NAME TOOL_NAME TOOL_VERSION SRC_DIR LIBS_DIR

    # What libs/ holds before the run, at every depth, to catch writes
    # straight into it. Listings, not file contents, so large libs stay fast.
    local libs_existed=no
    [ -d "$LIBS_DIR" ] && libs_existed=yes
    : > "$LIBS_TMP.start"
    libs_tree > "$LIBS_TMP.before"
    libs_changed "" "$LIBS_TMP.start" > "$LIBS_TMP.changed-before"

    # stdin from /dev/null and no git prompts: a credential prompt fails the
    # fetch instead of hanging the build. In the background, so a signal to
    # the build stops it at once (wait_script), and in a process group of its
    # own (own_group), so what it starts stops with the build, also after a
    # Ctrl-C.
    local rc=0 script_pid
    clear_script_group
    if [ "$PROGRESS_MODE" = true ]; then
        (cd "$LIBS_TMP" && export GIT_TERMINAL_PROMPT=0 && own_group bash "$GET_LIBS_SCRIPT") </dev/null >"$GET_LIBS_LOG" 2>&1 8>&- 9>&- &
    else
        (cd "$LIBS_TMP" && export GIT_TERMINAL_PROMPT=0 && own_group bash "$GET_LIBS_SCRIPT") </dev/null 8>&- 9>&- &
    fi
    SCRIPT_PID=$!
    script_pid=$SCRIPT_PID
    wait_script || rc=$?
    # Before libs/ is checked, so a job left running writes nothing more.
    stop_left_jobs "$(script_group "$script_pid")" scripts/get_libs.sh
    clear_script_group
    check_libs_untouched "$libs_existed" "$LIBS_TMP" "$LIBS_TMP.start" "$rc"
    rm_libs_lists
    if [ "$rc" -ne 0 ]; then
        error "scripts/get_libs.sh failed (exit $rc); libs/ was not changed."
        show_get_libs_log
        exit 1
    fi

    local produced="" entry name
    for entry in "$LIBS_TMP"/*; do
        { [ -e "$entry" ] || [ -L "$entry" ]; } || continue
        name=${entry##*/}
        case "$name" in
            # 'git clone <url> .' makes the working folder itself a repository:
            # moved into libs/, it would make libs/ one, inside the tool's own.
            .git)
                error "scripts/get_libs.sh left a .git folder at the top of its working folder (git clone ... .): clone into a named folder instead, e.g. git clone <url> mylib."
                exit 1
                ;;
            .gitignore|*$'\n'*)
                error "scripts/get_libs.sh may not create '$name' at the top level of its working directory."
                exit 1
                ;;
        esac
        produced="${produced:+$produced$NL}$name"
    done

    # Never replace something in libs/ that get_libs.sh does not own.
    local old
    old=$(owned_names)
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if { [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; } && ! in_list "$name" "$old"; then
            if [ -n "$old" ] || [ -f "$LIBS_STAMP" ]; then
                error "libs/$name already exists and was not fetched by scripts/get_libs.sh. Remove or rename it, then build again."
            else
                # No record at all: the entry may be hand-made or an orphaned fetch.
                error "libs/$name already exists and was not fetched by scripts/get_libs.sh. If you made it yourself, rename it or stop get_libs.sh from creating it. If get_libs.sh fetched it in an earlier build (the record in .ngargparser/ is gone), delete libs/$name and build again."
            fi
            exit 1
        fi
    done <<< "$produced"

    # Project-folder runs need the same package layout the build gives libs/.
    find "$LIBS_TMP" -type d -exec chmod u+w {} +
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if [ -d "$LIBS_TMP/$name" ] && [ ! -L "$LIBS_TMP/$name" ]; then
            ensure_init_files "$LIBS_TMP/$name"
        fi
    done <<< "$produced"

    if [ -n "$produced" ]; then
        mkdir -p "$LIBS_DIR"
    fi
    # The entries an earlier run fetched that are still in libs/: dropped,
    # when this run did not fetch them, else replaced by the new fetch. Each
    # is judged before the stamp is rewritten, because changes are measured
    # against it. One changed since the fetch may hold edits that git does
    # not keep (libs/.gitignore lists it), so it is moved to
    # .ngargparser/removed-libs/ instead of deleted; so is a dropped one with
    # no fingerprint to tell. A replaced one without a fingerprint (no stamp,
    # an older stamp, a move cut short) is replaced, as before.
    local dropped="" edited="" state
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        { [ -e "$LIBS_DIR/$name" ] || [ -L "$LIBS_DIR/$name" ]; } || continue
        if ! in_list "$name" "$produced"; then
            dropped="${dropped:+$dropped$NL}$name"
        fi
        state=$(entry_state "$name")
        if [ "$state" != same ]; then
            edited="${edited:+$edited$NL}$state $name"
        fi
    done <<< "$old"
    # Record every name that may end up half-moved, so an interrupted run is
    # redone instead of trusted.
    write_stamp pending "$(printf '%s\n%s\n' "$old" "$produced" | sed '/^$/d' | sort -u)"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if in_list "changed $name" "$edited"; then
            keep_removed_lib "$name" changed
        elif in_list "unknown $name" "$edited"; then
            keep_removed_lib "$name" unknown
        else
            notice "libs/$name is no longer fetched by scripts/get_libs.sh; removing it."
            rm_tree "$LIBS_DIR/$name"
        fi
    done <<< "$dropped"
    while IFS= read -r name; do
        [ -n "$name" ] || continue
        if in_list "changed $name" "$edited"; then
            keep_removed_lib "$name" changed replaced
        else
            rm_tree "$LIBS_DIR/$name"
        fi
        mv "$LIBS_TMP/$name" "$LIBS_DIR/$name"
    done <<< "$produced"
    write_libs_gitignore "$produced"
    write_stamp "$hash" "$produced"

    rm_tree "$LIBS_TMP"
    LIBS_TMP=""
    return 0
}

# Remove VCS metadata and bytecode caches from a staged libs/ entry. Real
# directories only: a symlink here may point outside the build.
strip_vcs_and_caches() {
    local dir="$1"
    rm -rf "$dir/.github" "$dir/.gitlab"
    find "$dir" \( -name .git -o -name __pycache__ \) -prune -exec rm -rf {} +
}

# For the hint in check_links: sets folder_link to $2 (build-relative) when it
# is the first bad link that points at a folder and comes from the project
# itself, where .distignore applies (not from get_libs.sh or a hook).
note_folder_link() {
    local top
    [ -z "$folder_link" ] && [ -d "$1" ] && [ -L "$PROJECT_ROOT/$2" ] || return 0
    case "$2" in
        libs/*)
            top=${2#libs/}
            in_list "${top%%/*}" "$(stamp_names)" && return 0
            ;;
    esac
    folder_link=$2
    return 0
}

# For check_links and check_required: when the link $1 (build-relative)
# dangles in the build copy only because what it points at was left out of
# it, prints that path, relative to the tool folder, then a line "rules" when
# .distignore or the hidden-file rule excludes it, or "never" when it is in a
# folder that never ships (hard_excluded). When the link points outside the
# tool folder, so the build copy cannot have what it points at, prints that
# path, then "outside". Prints nothing for any other link: one that dangles
# in the tool folder too, one a hook made, or one in or into a fetched entry
# that stays in the tool folder (those ship as fetched).
excluded_target() {
    local src="$PROJECT_ROOT/$1" target dir abs root rel top
    { [ -L "$src" ] && [ -e "$src" ]; } || return 0
    target=$(readlink "$src")
    case "$target" in /*) return 0 ;; esac
    dir=$(cd -P "$(dirname -- "$src")" && cd -P "$(dirname -- "$target")" 2>/dev/null && pwd -P) || return 0
    abs="$dir/$(basename -- "$target")"
    root=$(cd -P "$PROJECT_ROOT" && pwd -P)
    case "$abs" in
        "$root"/*) rel=${abs#"$root"/} ;;
        *)
            printf '%s\noutside\n' "$abs"
            return 0
            ;;
    esac
    for top in "$1" "$rel"; do
        case "$top" in
            libs/*)
                top=${top#libs/}
                in_list "${top%%/*}" "$(stamp_names)" && return 0
                ;;
        esac
    done
    if in_list "${rel%%/*}" "$HARD_EXCLUDED"; then
        printf '%s\nnever\n' "$rel"
    elif { [ -d "$abs" ] && [ ! -L "$abs" ] && is_excluded "$rel/"; } || is_excluded "$rel"; then
        printf '%s\nrules\n' "$rel"
    fi
    return 0
}

# Every symlink in the build must be relative and resolve to something inside
# the build: the tarball stores links as links, so anything else is broken once
# extracted on another host. Call at top level only.
check_links() {
    local root bad="" link target resolved rel folder_link="" left_out
    root=$(cd "$BUILD_DIR" && pwd -P)
    while IFS= read -r -d '' link; do
        target=$(readlink "$link")
        rel=${link#"$BUILD_DIR"/}
        case "$target" in
            /*)
                bad+="  $rel -> $target (absolute)"$'\n'
                note_folder_link "$link" "$rel"
                continue
                ;;
        esac
        if [ ! -e "$link" ]; then
            left_out=$(excluded_target "$rel")
            case "$left_out" in
                *$'\n'rules)
                    bad+="  $rel -> $target (points at ${left_out%$'\n'*}, which .distignore (or the hidden-file rule) excludes)"$'\n'
                    ;;
                *$'\n'never)
                    bad+="  $rel -> $target (points at ${left_out%$'\n'*}, which the build never ships)"$'\n'
                    ;;
                *$'\n'outside)
                    bad+="  $rel -> $target (points outside the tool folder)"$'\n'
                    note_folder_link "$PROJECT_ROOT/$rel" "$rel"
                    ;;
                *) bad+="  $rel -> $target (dangling or a loop)"$'\n' ;;
            esac
            continue
        fi
        if [ -d "$link" ]; then
            resolved=$(cd -P "$link" && pwd -P)
        else
            resolved=$(cd -P "$(dirname -- "$link")" && cd -P "$(dirname -- "$target")" && pwd -P)/$(basename -- "$target")
        fi
        case "$resolved/" in
            "$root"/*) ;;
            *)
                bad+="  $rel -> $target (points outside the build)"$'\n'
                note_folder_link "$link" "$rel"
                ;;
        esac
    done < <(find "$BUILD_DIR" -type l -print0)

    if [ -n "$bad" ]; then
        error "Symlinks in the build must be relative and stay inside it. Fix these in the tool folder, scripts/get_libs.sh or scripts/hooks.sh, or keep the tool folder's links out with .distignore:"
        printf '%s' "$bad" | head -n 20 >&2
        # git treats a link as a file, so a folder-only rule ('name/') never
        # matches a link to a folder.
        if [ -n "$folder_link" ]; then
            echo "$folder_link is a link to a folder, which git treats as a file: a .distignore rule must name it without a trailing slash (/$folder_link, not /$folder_link/)." >&2
        fi
        exit 1
    fi
    return 0
}

# The startup check: the entry script's help screens, run from the build dir
# in an environment made as the deploy makes it, without the dev group
# (startup_python), the same commands `cli sync` uses to test-drive a
# project: --help, then the --help of predict, preprocess and postprocess,
# then of each other subcommand (and alias) the usage line of the first one
# lists, as {predict,preprocess,postprocess,list-alleles} (subcommands()).
# APP_ROOT points at the build so its libs/ is the one imported (the env var
# wins over the project .env that python-dotenv would otherwise find).
# A small Python driver gives each command a timeout without needing GNU
# `timeout`, which macOS does not have: the seconds in its fourth argument
# (STARTUP_TIMEOUT). The first command that times out ends the check, as the
# others would most likely wait as long. The commands run as on a deploy host:
# no PYTHONPATH, PYTHONHOME or PYTHONSTARTUP from the shell that runs the
# build, and no user site-packages, so a module .distignore drops cannot be
# found somewhere else. An entry that is not a Python script runs by itself,
# by the rule configure.py and app_env.py apply (entry_runs_with_python): a
# first #! line decides, else a name that ends in .py is one. Every help
# screen, whatever the entry, finds the environment the check runs in as
# .venv in the build copy, as it would find the deploy's .venv: VIRTUAL_ENV
# names it, whatever the shell that runs the build set, and its bin/ is first
# on the PATH, as cli sync's test drive gives the tool's .venv. The link is
# made for the help screens only, and removed again.
# Each help screen runs in a session of its own, with stdin from /dev/null,
# and when it ends (or times out, or the build is stopped) its whole process
# group is killed: a program it started in the background would go on writing
# into the build copy while it is packed. Its stdout and stderr go to files,
# not pipes, which such a program could hold open. The driver stops (killing
# the help screen's group) on TERM, HUP or INT, and when the shell that
# started it is gone, as when pkill -f build.sh stops the build and its
# subshells together. A failure whose last line is the same as an earlier
# one's is one line. When a module is not found that the tool's .venv (its fifth
# argument) has, from a package other than the tool (its sixth argument, the
# [project] name), a last line says to add the package: the deploy leaves
# out what only the dev group installs.
# Read with read, as LOCK_DRIVER below is.
IFS= read -r -d '' STARTUP_DRIVER <<'PY' || true
import os, re, signal, subprocess, sys, tempfile, time
py, script, root, timeout, tool_python, tool_name = sys.argv[1:7]
env = dict(os.environ, APP_ROOT=root, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
    env.pop(name, None)
path = os.path.join(root, script)
try:
    with open(path, "rb") as f:
        line = f.readline(4096)
except OSError:
    line = b""
with_python = b"python" in line if line.startswith(b"#!") else script.endswith(".py")
command = [py, script] if with_python else [path]
link = None
if not with_python:
    if not os.access(path, os.X_OK):
        print(script + " is not a Python script and not executable: run 'chmod +x " + script + "'"
              " and commit the mode (git update-index --chmod=+x " + script + ")")
        sys.exit(1)
    # Else the system looks for an interpreter whose name ends in that carriage
    # return, and says only "No such file or directory".
    if line.startswith(b"#!") and line.rstrip(b"\n").endswith(b"\r"):
        fix = "perl -pi -e 's/\\r$//' " if sys.platform == "darwin" else "sed -i \"s/\\r$//\" "
        print(script + "'s #! line ends in a carriage return (Windows line endings), so it cannot run: convert it"
              " with '" + fix + script + "' (and add '" + script + " text eol=lf' to .gitattributes)")
        sys.exit(1)
venv = os.path.join(root, ".venv")
if not os.path.lexists(venv):
    link = venv
    os.symlink(os.path.dirname(os.path.dirname(py)), link)
env["VIRTUAL_ENV"] = venv
env["PATH"] = os.path.join(venv, "bin") + os.pathsep + env.get("PATH", "")


def group_running(group):
    # True while a process that is not a zombie is in the process group.
    try:
        os.killpg(group, 0)
    except OSError:
        return False
    try:
        names = os.listdir("/proc")
    except OSError:
        return True
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/" + name + "/stat", "rb") as f:
                fields = f.read().rsplit(b") ", 1)[1].split()
            if fields[0] != b"Z" and int(fields[2]) == group:
                return True
        except (OSError, IndexError, ValueError):
            continue
    return False


def stop_group(proc):
    # Kills the help screen and everything left in its group, reaps it, and
    # waits up to a second for the rest to end.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    proc.wait()
    for _ in range(100):
        if not group_running(proc.pid):
            return
        time.sleep(0.01)


def stopped(signum, frame):
    sys.exit(128 + signum)


# INT too: as a job the build runs in the background, this driver starts with
# INT ignored, and then a Ctrl-C would leave it to the build to stop it.
for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(number, stopped)
# The shell that started this driver: when it is gone, so is the build.
parent = os.getppid()


def check_parent():
    if os.getppid() != parent:
        sys.exit(1)


def wait_for(proc):
    # Waits for the help screen until the timeout (subprocess.TimeoutExpired),
    # checking ten times a second that the shell that started this driver runs.
    deadline = time.monotonic() + float(timeout)
    while True:
        try:
            return proc.wait(timeout=max(0.0, min(0.1, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            check_parent()
            if time.monotonic() >= deadline:
                raise


def help_screen(args):
    # The exit code, stderr and stdout of one help screen; None for the code when it timed out.
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(command + args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                env=env, start_new_session=True)
        try:
            wait_for(proc)
        except subprocess.TimeoutExpired:
            stop_group(proc)
            return None, "", ""
        except BaseException:
            stop_group(proc)
            raise
        stop_group(proc)
        err.seek(0)
        out.seek(0)
        return proc.returncode, err.read().decode("utf-8", "replace"), out.read().decode("utf-8", "replace")


FRAMEWORK = ["predict", "preprocess", "postprocess"]


def subcommands(help_text):
    # The subcommands whose --help runs after the top-level one: the framework's,
    # then each other name (a subcommand the tool adds with self.subparser.add_parser,
    # or an alias) in the order the top-level --help lists them. They are the first
    # {a,b,...} group, split at ',', in its usage (from "usage:" to the first blank
    # line, without color codes) that holds a framework subcommand.
    text = re.sub(r"\x1b\[[0-9;]*m", "", help_text)
    start = text.find("usage:")
    usage = re.split(r"\n[ \t]*\n", text[start:], maxsplit=1)[0] if start >= 0 else ""
    for group in re.findall(r"\{([^{}]*)\}", usage):
        names = [name.strip() for name in group.split(",")]
        if set(names) & set(FRAMEWORK):
            return FRAMEWORK + [name for name in names if name and name not in FRAMEWORK]
    return FRAMEWORK


FIND_PACKAGE = """
import importlib.util, re, sys
name, tool = sys.argv[1:3]
try:
    from importlib.metadata import distributions
    found = importlib.util.find_spec(name) is not None
except Exception:
    found = False
for dist in distributions() if found else ():
    try:
        files = {str(f).split("/")[0] for f in dist.files or ()}
        top = (dist.read_text("top_level.txt") or "").split()
        package = dist.metadata["Name"] or ""
    except Exception:
        continue
    if name in top or name in files or name + ".py" in files:
        if re.sub(r"[-_.]+", "-", package).lower() != re.sub(r"[-_.]+", "-", tool).lower():
            print(package)
        break
"""


def dev_only_package(module):
    # The package that brings the module into the tool's .venv, or "".
    if os.path.abspath(tool_python) == os.path.abspath(py) or not os.access(tool_python, os.X_OK):
        return ""
    with tempfile.TemporaryDirectory() as cwd:
        try:
            found = subprocess.run([tool_python, "-c", FIND_PACKAGE, module, tool_name], cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   universal_newlines=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return ""
    return found.stdout.strip() if found.returncode == 0 else ""


failed = 0
shown = set()
missing = []
screens = [["--help"]]
try:
    while screens:
        check_parent()
        args = screens.pop(0)
        label = " ".join([script] + args)
        try:
            code, stderr, stdout = help_screen(args)
        except OSError as exc:
            code, stderr, stdout = exc, "", ""
        if args == ["--help"]:
            screens = [[name, "--help"] for name in subcommands(stdout if code == 0 else "")]
        if isinstance(code, OSError):
            print(label + ": " + str(code))
            failed += 1
            continue
        if code is None:
            print(label + ": timed out after " + timeout + "s: set NGARGPARSER_STARTUP_CHECK_TIMEOUT to the seconds"
                  " each help screen may take, or NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check")
            failed += 1
            break
        if code != 0:
            lines = [line for line in stderr.splitlines() if line.strip()]
            summary = lines[-1].strip() if lines else "exit code %d" % code
            # The end of stderr, indented, when it holds more than that one line
            # (a traceback names the file), and only the first time.
            if len(lines) > 1 and summary in shown:
                print(label + ": " + summary + " (same error as above)")
            else:
                print(label + ": " + summary)
                if len(lines) > 1:
                    print("\n".join("    " + line.rstrip() for line in stderr.rstrip().splitlines()[-15:]))
                    shown.add(summary)
            module = re.match(r"ModuleNotFoundError: No module named '([^'.]+)", summary)
            if module and module.group(1) not in missing:
                missing.append(module.group(1))
            failed += 1
finally:
    if link:
        os.remove(link)
for module in missing:
    package = dev_only_package(module)
    if package:
        print(module + " is only in the dev group (or not in [project] dependencies), which a deploy leaves out:"
              " uv add " + package)
sys.exit(1 if failed else 0)
PY

# [tool.ngargparser] entry in pyproject.toml: the tool's entry script, relative
# to the tool folder. Prints nothing when it is not set, ENTRY_NOT_A_STRING
# when its value is not a plain quoted string, and ENTRY_EMPTY when it is the
# empty string, which would otherwise read as no entry. Read as TOML
# by the lock driver when a Python that reads TOML is found, so an inline
# table (tool.ngargparser = { entry = "..." }, or ngargparser = { ... } under
# [tool]) counts too. Else, or when that Python cannot read the file (uv reads
# newer TOML), line by line: an entry = "..." or entry = '...' line in the
# [tool.ngargparser] table.
pyproject_entry() {
    close_lock_fd
    [ -f "$PROJECT_ROOT/pyproject.toml" ] || return 0
    local verdict
    verdict=$(toml_python_run entry)
    case "$verdict" in
        "entry "*)
            printf '%s\n' "${verdict#entry }"
            return 0
            ;;
        none) return 0 ;;
        not-a-string)
            echo ENTRY_NOT_A_STRING
            return 0
            ;;
        empty)
            echo ENTRY_EMPTY
            return 0
            ;;
    esac
    awk -v sq="'" '
        { sub(/\r$/, "") }
        /^[[:space:]]*\[/ {
            table = $0
            sub(/[[:space:]]*#.*$/, "", table)
            gsub(/[[:space:]]/, "", table)
            in_table = (table == "[tool.ngargparser]")
            next
        }
        in_table && /^[[:space:]]*entry[[:space:]]*=/ {
            value = $0
            sub(/^[[:space:]]*entry[[:space:]]*=[[:space:]]*/, "", value)
            if (value ~ /^"[^"\\]*"[[:space:]]*(#.*)?$/ || value ~ ("^" sq "[^" sq "]*" sq "[[:space:]]*(#.*)?$")) {
                quote = substr(value, 1, 1)
                value = substr(value, 2)
                value = substr(value, 1, index(value, quote) - 1)
                print ((value == "") ? "ENTRY_EMPTY" : value)
            } else {
                print "ENTRY_NOT_A_STRING"
            }
            exit
        }' "$PROJECT_ROOT/pyproject.toml"
    return 0
}

# The [project] name in the pyproject.toml $1, when it is a plain quoted
# package name, or nothing.
project_name() {
    local name
    name=$(awk -v sq="'" '
        { sub(/\r$/, "") }
        /^[[:space:]]*\[/ {
            table = $0
            sub(/[[:space:]]*#.*$/, "", table)
            gsub(/[[:space:]]/, "", table)
            in_project = (table == "[project]")
            next
        }
        in_project && /^[[:space:]]*name[[:space:]]*=/ {
            value = $0
            sub(/^[[:space:]]*name[[:space:]]*=[[:space:]]*/, "", value)
            quote = substr(value, 1, 1)
            if (quote == "\"" || quote == sq) {
                value = substr(value, 2)
                if (index(value, quote)) print substr(value, 1, index(value, quote) - 1)
            }
            exit
        }' "$1" 2>/dev/null) || name=""
    case "$name" in
        ""|*[!ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-]*) return 0 ;;
    esac
    printf '%s\n' "$name"
}

# The uv settings from the environment that would change what the build's
# uv commands do, unset in the subshell that runs them. UV_LOCKED and
# UV_FROZEN pick a lock mode: the build passes its own (UV_LOCKED with
# --frozen is an error, and UV_FROZEN makes 'uv lock --check' pass a uv.lock
# that is out of date). UV_PROJECT and UV_WORKING_DIR point uv at another
# folder, and UV_PROJECT_ENVIRONMENT at another environment (the startup
# check sets its own). UV_RESOLUTION, UV_PRERELEASE, UV_FORK_STRATEGY,
# UV_EXCLUDE_NEWER and UV_NO_SOURCES change how uv resolves, so uv.lock no
# longer matches although pyproject.toml did not change, and the deploy host
# does not have them. Index, cache, Python and offline settings stay, since
# a build machine may need them. Call in a subshell only.
unset_uv_overrides() {
    unset UV_LOCKED UV_FROZEN UV_PROJECT UV_WORKING_DIR UV_PROJECT_ENVIRONMENT
    unset UV_RESOLUTION UV_PRERELEASE UV_FORK_STRATEGY UV_EXCLUDE_NEWER UV_NO_SOURCES
}

# Runs 'uv sync --frozen --no-dev' with the arguments "$@" in the build copy,
# into .ngargparser/startup-venv, its output in .../startup-sync, and returns
# its exit code. The tool itself is reinstalled (--reinstall-package): the
# build copy is made anew each build with the old modification times, so uv
# would not see that something it needs was left out of it. For a tool
# without a [build-system], uv installs no package of the tool, and the flag
# does nothing. In the background, as wait_script explains.
startup_sync() {
    local name rc=0 args=(sync --frozen --no-dev "$@")
    name=$(project_name "$BUILD_DIR/pyproject.toml")
    if [ -n "$name" ]; then
        args+=(--reinstall-package "$name")
    fi
    (cd "$BUILD_DIR" && unset_uv_overrides && UV_PROJECT_ENVIRONMENT="$STATE_DIR/startup-venv" uv "${args[@]}") \
        </dev/null >"$TMPWORK/startup-sync" 2>&1 8>&- 9>&- &
    SCRIPT_PID=$!
    wait_script || rc=$?
    return "$rc"
}

# True when the last startup_sync failed only for want of the network or of
# uv's cache, or for the build's own state, not for a reason the deploy's uv
# sync meets too: a package or a Python that is not in the cache offline (or
# that uv may not download), a connection, DNS or timeout failure, a server
# error, a cache uv cannot open, or a startup-venv that is no environment.
# These are uv 0.11's words. uv wraps its messages to the terminal's width, so
# they are matched on its output joined into one line, without the '│' of its
# tree of causes.
startup_sync_offline_trouble() {
    local text
    text=$(tr '\n' ' ' < "$TMPWORK/startup-sync" | sed -e 's/│/ /g' -e 's/[[:space:]][[:space:]]*/ /g')
    grep -q -F \
        -e 'Network connectivity is disabled' -e 'network was disabled' -e 'found in the cache' \
        -e 'but uv is set to offline mode' -e 'Python downloads are set to' \
        -e 'error sending request for url' -e 'client error (Connect)' -e 'dns error' -e 'tcp connect error' \
        -e 'Connection refused' -e 'Connection reset' -e 'network timeout' -e 'operation timed out' \
        -e 'Request failed after' -e 'HTTP status server error' -e 'Failed to initialize cache' \
        -e 'cannot be used because it is not a valid Python environment' <<< "$text"
}

# True when the last startup_sync failed because uv may not write
# .ngargparser/startup-venv (or make it): a venv another user of a shared tool
# folder made, or a folder made read-only. A problem of this machine only:
# the deploy makes a .venv of its own.
startup_venv_unwritable() {
    local text
    text=$(tr '\n' ' ' < "$TMPWORK/startup-sync")
    [[ "$text" == *"Permission denied"* ]] && [[ "$text" == *"/.ngargparser/startup-venv"* ]]
}

# True when uv could not build the tool, or a package it installs from a
# folder, for a reason of the package's own, not for want of the network or
# of uv's cache: the deploy's uv sync fails on it too.
startup_sync_build_failed() {
    grep -q 'Failed to build `' "$TMPWORK/startup-sync" && ! startup_sync_offline_trouble
}

# uv's lines from its error on, without blank lines, indented: the first 6
# and the last 20 when there are more than 30.
startup_sync_error() {
    local lines count
    lines=$(sed '/^[[:space:]]*$/d' "$TMPWORK/startup-sync" | awk '
        !started && /^[[:space:]]*(×|x |error:)/ { started = 1 }
        { all[++n] = $0; if (started) kept[++k] = $0 }
        END { if (k) for (i = 1; i <= k; i++) print kept[i]; else for (i = 1; i <= n; i++) print all[i] }')
    count=$(printf '%s\n' "$lines" | wc -l | tr -d '[:space:]')
    if [ "$count" -gt 30 ]; then
        {
            printf '%s\n' "$lines" | sed -n '1,6p'
            echo "..."
            printf '%s\n' "$lines" | tail -n 20
        } | sed 's/^/    /'
    else
        printf '%s\n' "$lines" | sed 's/^/    /'
    fi
    return 0
}

# The top of each tree in the build copy that is not in the listing $1 (made
# with find "$BUILD_DIR" -print | LC_ALL=C sort), one per line, as full paths.
new_in_build_copy() {
    find "$BUILD_DIR" -print | LC_ALL=C sort > "$1.after"
    # Sorted, a folder comes before what is in it.
    LC_ALL=C comm -13 "$1" "$1.after" | awk '
        {
            up = $0
            top = 1
            while (sub(/\/[^\/]*$/, "", up)) if (up in made) { top = 0; break }
            made[$0] = 1
            if (top) print
        }'
}

# Sets STARTUP_PYTHON to the Python the startup check runs the tool with. The
# deploy installs the tool with 'uv sync --frozen --no-dev', which leaves out
# the dev group, so a module only the dev group brings (pygments, through
# pytest) is there in the tool's .venv but not on the deploy host. The check
# therefore runs in .ngargparser/startup-venv, synced the same way, offline
# from uv's cache. It is synced from the build copy, as the deploy syncs the
# extracted tarball: a tool with a [build-system] is installed from it, so
# its editable install points at the build copy, and a module .distignore
# left out cannot be imported from the tool folder instead. A tool that does
# not install from the build copy (a package folder or the readme left out)
# fails the build with uv's lines, as it would fail the deploy. The venv is
# kept between builds, so the sync is quick when nothing changed. What the
# sync writes into the build copy (a setuptools egg-info) is removed again,
# so it does not ship. A kept venv already holds the packages, and uv does
# not check them against uv.lock again: after uv.lock changed (a hash edited
# by hand, which the deploy's uv sync refuses), the sync reinstalls them all
# from uv's cache, which checks each one's hash. The lock it last synced is
# in .ngargparser/startup-venv.uv-lock. In CI (CI set), a sync that cannot run
# offline is tried once online. Without uv or uv.lock, or when the sync
# cannot run for want of the network or of uv's cache (a package missing from
# it, startup_sync_offline_trouble), it is the tool's .venv, with a notice,
# and without a .venv the build stops (tool_venv_fallback). In CI, a sync
# that cannot run so stops the build: the .venv holds the dev group too.
# A startup-venv uv may not write stops the build as a problem of this
# machine (startup_venv_unwritable). Any other uv error stops the build: the
# deploy's uv sync fails on it too. The uv settings that would change the
# sync's flags or its folder are left out (unset_uv_overrides).
# Call at top level only.
STARTUP_PYTHON=""
# The tool's .venv when .ngargparser/startup-venv cannot be made, for the
# reason $1 (it ends the notice, after a colon or in brackets as $2 says),
# with uv's lines $3. Without a .venv, no Python is left to run the check
# with, and the build stops: a build without the check would ship a tool no
# one saw start. In CI that error says to run uv sync in the job.
tool_venv_fallback() {
    local fallback="Notice: the startup check uses the tool's .venv, so packages only its dev group installs were not left out"
    if [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
        if [ "$2" = colon ]; then
            notice "$fallback: $1:${NL}$3"
        else
            notice "$fallback ($1)."
        fi
        return 0
    fi
    if env_flag CI; then
        error "The startup check cannot run: CI is set and the tool folder has no .venv/bin/python. Run 'uv sync' before 'make build' in the CI job, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check."
    else
        error "The startup check has no Python to run the tool with: the tool folder has no .venv/bin/python, and $1. Run 'uv sync', or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check."
    fi
    [ -z "$3" ] || printf '%s\n' "$3" >&2
    exit 1
}

startup_python() {
    local venv="$STATE_DIR/startup-venv" rc=0 reason path lock_hash reinstall=()
    local synced="$STATE_DIR/startup-venv.uv-lock"
    STARTUP_PYTHON="$PROJECT_ROOT/.venv/bin/python"
    if ! command -v uv >/dev/null 2>&1; then
        tool_venv_fallback "uv is not on the PATH"
        return 0
    fi
    if [ ! -f "$PROJECT_ROOT/uv.lock" ]; then
        tool_venv_fallback "the tool has no uv.lock"
        return 0
    fi
    lock_hash=$(git hash-object --no-filters "$BUILD_DIR/uv.lock" 2>/dev/null) || lock_hash=""
    if [ -x "$venv/bin/python" ] && { [ -z "$lock_hash" ] || [ "$(sed -n 1p "$synced" 2>/dev/null)" != "$lock_hash" ]; }; then
        reinstall=(--reinstall)
    fi
    find "$BUILD_DIR" -print | LC_ALL=C sort > "$TMPWORK/sync-before"
    startup_sync "${reinstall[@]}" --offline || rc=$?
    local tried="'uv sync --frozen --no-dev --offline' could not make .ngargparser/startup-venv (exit $rc)"
    # A CI runner's uv cache is often cold, and its jobs may download: one try
    # online before the .venv.
    if { { [ "$rc" -ne 0 ] && startup_sync_offline_trouble; } || { [ "$rc" -eq 0 ] && [ ! -x "$venv/bin/python" ]; }; } &&
        env_flag CI; then
        rc=0
        startup_sync "${reinstall[@]}" || rc=$?
        tried="$tried, and neither could 'uv sync --frozen --no-dev' (exit $rc)"
    fi
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        log_verbose "Removed ${path#"$BUILD_DIR"/} from the build copy: uv sync wrote it there for the startup check."
        rm_tree "$path"
    done <<< "$(new_in_build_copy "$TMPWORK/sync-before")"
    if [ "$rc" -ne 0 ] && startup_sync_build_failed; then
        error "The tool does not install from the build copy, so deploy/install.sh would fail on the deploy host too ('uv sync --frozen --no-dev' exit $rc):"
        startup_sync_error >&2
        echo "Building the tool's package needs every file pyproject.toml names, such as its readme and its packages: check that .distignore and scripts/hooks.sh leave them in the build copy." >&2
        exit 1
    fi
    if [ "$rc" -eq 0 ] && [ -x "$venv/bin/python" ]; then
        STARTUP_PYTHON="$venv/bin/python"
        if [ -n "$lock_hash" ]; then
            printf '%s\n' "$lock_hash" > "$synced"
        fi
        log_verbose "Startup check environment: .ngargparser/startup-venv (uv sync --frozen --no-dev)."
        return 0
    fi
    if [ "$rc" -ne 0 ] && startup_venv_unwritable; then
        error "uv cannot write .ngargparser/startup-venv, where the startup check makes its environment (Permission denied). This is a problem of this machine, not of the tool or the deploy: remove .ngargparser/startup-venv (or ask its owner to), then build again."
        startup_sync_error >&2
        exit 1
    fi
    if [ "$rc" -ne 0 ] && ! startup_sync_offline_trouble; then
        error "'uv sync --frozen --no-dev' fails in the build copy (exit $rc), so deploy/install.sh would fail on the deploy host too:"
        startup_sync_error >&2
        exit 1
    fi
    # uv's first lines of substance, without its progress lines.
    reason=$(sed -e '/^[[:space:]]*$/d' -e '/^Using /d' -e '/^Resolved /d' "$TMPWORK/startup-sync" |
        sed -n '1,3s/^/    /p')
    # The tool's .venv also holds the dev group, so a CI build checked with it
    # would pass a tool that imports what only the dev group installs.
    if env_flag CI; then
        error "The startup check cannot run: CI is set and $tried. Give the CI job network access or uv's cache, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check. uv said:"
        printf '%s\n' "$reason" >&2
        exit 1
    fi
    tool_venv_fallback "$tried" colon "$reason"
    return 0
}

# entry = "" would name no file: the build stops instead of taking it for no
# entry and picking a src/run_*.py.
entry_empty() {
    error "entry = \"\" under [tool.ngargparser] in pyproject.toml is empty: name the entry script, or delete the line."
    exit 1
}

# Call at top level only.
check_startup() {
    local python="$PROJECT_ROOT/.venv/bin/python"
    local scripts=() script output rc=0 entry
    entry=$(pyproject_entry)
    if [ -n "$entry" ]; then
        # The entry pyproject.toml names. Its file must ship, so a missing one
        # fails the build even when the check itself is skipped.
        if [ "$entry" = ENTRY_NOT_A_STRING ]; then
            error "entry under [tool.ngargparser] in pyproject.toml must be a quoted path, such as entry = \"src/run_<name>.py\"."
            exit 1
        fi
        [ "$entry" != ENTRY_EMPTY ] || entry_empty
        entry=${entry#./}
        case "/$entry/" in
            //*|*/../*)
                error "entry = \"$entry\" under [tool.ngargparser] in pyproject.toml must be a path inside the tool folder, such as src/run_<name>.py."
                exit 1
                ;;
        esac
        if [ ! -f "$BUILD_DIR/$entry" ]; then
            if [ ! -f "$PROJECT_ROOT/$entry" ]; then
                error "entry = \"$entry\" under [tool.ngargparser] in pyproject.toml names a file that does not exist."
            elif is_excluded "$entry"; then
                error "$entry is excluded by .distignore, so the tarball would have no entry script. Remove the rule that excludes it."
            else
                error "$entry, the entry under [tool.ngargparser] in pyproject.toml, is missing from the build copy (scripts/hooks.sh removed it)."
            fi
            exit 1
        fi
        script=$entry
    else
        for script in "$BUILD_DIR"/src/run_*.py; do
            if [ -f "$script" ]; then
                scripts+=("$script")
            fi
        done
        if [ ${#scripts[@]} -eq 0 ]; then
            # A tarball without the project's only entry script cannot run, so a
            # .distignore rule that drops it fails the build instead of skipping.
            local project_scripts=()
            for script in "$PROJECT_ROOT"/src/run_*.py; do
                if [ -f "$script" ]; then
                    project_scripts+=("$script")
                fi
            done
            if [ ${#project_scripts[@]} -eq 1 ]; then
                script="src/$(basename "${project_scripts[0]}")"
                if is_excluded "$script"; then
                    error "$script is excluded by .distignore, so the tarball would have no entry script. Remove the rule that excludes it."
                    exit 1
                fi
            fi
        fi
        script=""
        if [ ${#scripts[@]} -eq 1 ]; then
            script="src/$(basename "${scripts[0]}")"
        fi
    fi
    if env_flag NGARGPARSER_SKIP_STARTUP_CHECK; then
        notice "Startup check skipped: NGARGPARSER_SKIP_STARTUP_CHECK is set."
        return 0
    fi
    if [ -z "$script" ]; then
        if [ ${#scripts[@]} -gt 1 ]; then
            # Several scripts: only pyproject.toml can say which one runs, and
            # a CI build must not pass without the check.
            if env_flag CI; then
                error "The startup check cannot run: CI is set and the build has ${#scripts[@]} src/run_*.py scripts. Set entry = \"src/run_<name>.py\" under [tool.ngargparser] in pyproject.toml to name the entry script, or set NGARGPARSER_SKIP_STARTUP_CHECK=1 to build without the check."
                exit 1
            fi
            notice "Startup check skipped: expected exactly one src/run_*.py in the build, found ${#scripts[@]}. Set entry = \"src/run_<name>.py\" under [tool.ngargparser] in pyproject.toml to name the entry script."
            return 0
        fi
        notice "Startup check skipped: expected exactly one src/run_*.py in the build, found ${#scripts[@]}."
        return 0
    fi
    # Without a .venv, uv makes the check's environment (startup_python); a
    # build with neither stops, in CI or not (tool_venv_fallback).
    startup_python
    python=$STARTUP_PYTHON
    # What the build copy holds before, so a file the help screens write into
    # it (a log a module opens at import) can be removed: it would ship. And
    # the state of each path, so a file they change or remove fails the build.
    find "$BUILD_DIR" -print | LC_ALL=C sort > "$TMPWORK/startup-before"
    build_copy_state > "$TMPWORK/startup-state"
    # In the background, so a signal to the build stops the help screens at
    # once (wait_script) instead of after their timeout.
    (cd "$BUILD_DIR" && "$python" -I -c "$STARTUP_DRIVER" "$python" "$script" "$BUILD_DIR" "$STARTUP_TIMEOUT" \
        "$PROJECT_ROOT/.venv/bin/python" "$(project_name "$PROJECT_ROOT/pyproject.toml")") \
        </dev/null >"$TMPWORK/startup-output" 8>&- 9>&- &
    SCRIPT_PID=$!
    wait_script || rc=$?
    output=$(cat "$TMPWORK/startup-output")
    if [ "$rc" -ne 0 ]; then
        error "The tool does not start from the build dir:"
        printf '%s\n' "$output" >&2
        exit 1
    fi
    # A file a deploy needs is named with the reason first.
    check_required startup
    check_startup_changes "$TMPWORK/startup-state"
    log_verbose "Startup check passed."
    remove_startup_files
    return 0
}

# The build copy's paths, one per line, as "<mode> <size> <modification
# time> <status change time> <inode> <path>", the path as ./<path>. A folder
# has '-' for the size, the times and the inode: they change when something
# is made in it. The status change time moves on every write, chmod or
# replace, also when the modification time is set back. GNU find gives the
# times to the nanosecond, stat in whole seconds.
build_copy_state() {
    (
        cd "$BUILD_DIR" || exit 0
        case "$STAT_TREE" in
            gnu-find) find . -printf '%M %s %T@ %C@ %i %p\n' || true ;;
            bsd) find . -exec stat -f '%Sp %z %m %c %i %N' {} + || true ;;
            *) find . -exec stat -c '%A %s %Y %Z %i %n' {} + || true ;;
        esac
    ) | awk '{
        path = $0
        sub(/^[^ ]* [^ ]* [^ ]* [^ ]* [^ ]* /, "", path)
        if (path == ".") next
        if (substr($1, 1, 1) == "d") print $1 " - - - - " path
        else print $1 " " $2 " " $3 " " $4 " " $5 " " path
    }'
    return 0
}

# Fails the build when the help screens changed or removed a path the build
# copy held before them: $1 is build_copy_state's listing from then. Such a
# file would ship as they left it. What they made is removed afterwards
# (remove_startup_files). Call at top level only.
check_startup_changes() {
    local changed count
    build_copy_state > "$1.after"
    changed=$(awk '
        function path_of(line) { sub(/^[^ ]* [^ ]* [^ ]* [^ ]* [^ ]* /, "", line); return line }
        FILENAME == ARGV[1] { p = path_of($0); order[++n] = p; before[p] = $0; next }
        { after[path_of($0)] = $0 }
        END {
            for (i = 1; i <= n; i++) {
                p = order[i]
                up = p
                sub(/\/[^\/]*$/, "", up)
                if (!(p in after)) {
                    # The top of a removed tree only.
                    if (up == "." || (up in after)) print "  " substr(p, 3) ": removed"
                } else if (after[p] != before[p]) {
                    print "  " substr(p, 3) ": changed"
                }
            }
        }' "$1" "$1.after" | sort)
    [ -n "$changed" ] || return 0
    error "The startup check (the tool's help screens) changed or removed files in the build copy, so they would ship that way. Stop the tool from changing its own files when it starts:"
    # sed reads all its input, so no early exit can break the pipe (pipefail).
    printf '%s\n' "$changed" | sed -n '1,20p' >&2
    count=$(printf '%s\n' "$changed" | wc -l | tr -d '[:space:]')
    if [ "$count" -gt 20 ]; then
        echo "  ... and $((count - 20)) more" >&2
    fi
    exit 1
}

# Removes what the startup check created in the build copy, the top of each
# new tree only, and names it. Call at top level only.
remove_startup_files() {
    local made path
    made=$(new_in_build_copy "$TMPWORK/startup-before")
    [ -n "$made" ] || return 0
    notice "Notice: the startup check (the tool's help screens) created these in the build copy; the build removed them, so they do not ship:"
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        notice "  ${path#"$BUILD_DIR"/}"
        rm_tree "$path"
    done <<< "$made"
    return 0
}

# .ngargparser-build marks a folder as a build copy. It ships in the tarball,
# so a deployed copy carries it too, and `cli sync` refuses to run in a folder
# that has it. Written right after the build copy is created, so even a copy
# left by a killed build is marked, and again after the hooks, so a hook cannot
# drop or change it. Removed first: a hook may have left a link in its place.
write_build_marker() {
    rm_tree "$BUILD_DIR/.ngargparser-build"
    printf 'TOOL_NAME=%s\nTOOL_VERSION=%s\n' "$TOOL_NAME" "$TOOL_VERSION" > "$BUILD_DIR/.ngargparser-build"
}

# What a deploy needs in the tarball, one "<path>|<why>" line each. uv.lock
# and paths.py only when the project has one. A trailing '/' marks a folder.
# The framework files in src/core/ are named one by one, so dropping a single
# one is caught; they are the ones `cli generate` writes and `cli sync`
# restores, except __init__.py, which only generate writes.
required_paths() {
    local file
    echo "README|the deploy publishes it"
    echo "deploy/install.sh|the deploy runs it"
    echo "pyproject.toml|deploy/install.sh runs uv sync, which reads it"
    if [ -f "$PROJECT_ROOT/uv.lock" ]; then
        echo "uv.lock|deploy/install.sh runs uv sync --frozen, which installs the versions it pins"
    fi
    echo "configure|deploy/install.sh runs it"
    if [ -f "$PROJECT_ROOT/paths.py" ]; then
        echo "paths.py|./configure on the deploy host reads the dependencies from it"
    fi
    for file in NGArgumentParser.py core_validators.py result_writer.py app_env.py set_pythonpath.py configure.py dep_names.py; do
        echo "src/core/$file|framework code the tool runs on (cli sync restores it)"
    done
    echo "src/core/__init__.py|without it, another package named core can take the place of src/core (an empty file will do)"
}

# One "  <path>: <why>" line for each required path the folder $1 lacks.
missing_required() {
    local line path
    while IFS= read -r line; do
        path=${line%%|*}
        case "$path" in
            */) [ -d "$1/$path" ] && continue ;;
            *) [ -f "$1/$path" ] && continue ;;
        esac
        printf '  %s: %s\n' "$path" "${line#*|}"
    done <<< "$(required_paths)"
    return 0
}

# One "  <path>: <why>" line for each required path the project has that
# .distignore excludes. Forced paths always ship, so they are never excluded.
excluded_required() {
    local line path ignored
    ignored=$(
        while IFS= read -r line; do
            path=${line%%|*}
            is_forced "$path" && continue
            case "$path" in
                */) [ -d "$PROJECT_ROOT/$path" ] || continue ;;
                *) [ -f "$PROJECT_ROOT/$path" ] || continue ;;
            esac
            printf '%s\n' "$path"
        done <<< "$(required_paths)" | rules_exclude
    )
    [ -n "$ignored" ] || return 0
    while IFS= read -r line; do
        path=${line%%|*}
        if in_list "${path%/}" "$ignored"; then
            printf '  %s: %s\n' "$path" "${line#*|}"
        fi
    done <<< "$(required_paths)"
    return 0
}

# The entry pyproject.toml names (else the only src/run_*.py), checked before
# anything is removed: it must
# be a quoted path to a file inside the tool that .distignore does not
# exclude. An entry under libs/ is left to the startup check, since
# get_libs.sh may fetch it and fetched entries are not filtered. The startup
# check checks it again in the build copy, after the hooks. Call at top level
# only.
check_entry() {
    local entry script scripts=()
    entry=$(pyproject_entry)
    if [ -z "$entry" ]; then
        # No entry named: the project's only src/run_*.py is the entry script,
        # and a .distignore rule that drops it is caught here too.
        for script in "$PROJECT_ROOT"/src/run_*.py; do
            if [ -f "$script" ]; then
                scripts+=("src/$(basename "$script")")
            fi
        done
        if [ ${#scripts[@]} -eq 1 ] && [ -n "$(printf '%s\n' "${scripts[0]}" | rules_exclude)" ]; then
            error "${scripts[0]} is excluded by .distignore, so the tarball would have no entry script. Remove the rule that excludes it."
            exit 1
        fi
        return 0
    fi
    if [ "$entry" = ENTRY_NOT_A_STRING ]; then
        error "entry under [tool.ngargparser] in pyproject.toml must be a quoted path, such as entry = \"src/run_<name>.py\"."
        exit 1
    fi
    [ "$entry" != ENTRY_EMPTY ] || entry_empty
    entry=${entry#./}
    case "/$entry/" in
        //*|*/../*)
            error "entry = \"$entry\" under [tool.ngargparser] in pyproject.toml must be a path inside the tool folder, such as src/run_<name>.py."
            exit 1
            ;;
        /libs/*) return 0 ;;
    esac
    if [ -d "$PROJECT_ROOT/$entry" ]; then
        error "entry = \"$entry\" under [tool.ngargparser] in pyproject.toml names a folder, not a file."
        exit 1
    fi
    if [ ! -f "$PROJECT_ROOT/$entry" ]; then
        error "entry = \"$entry\" under [tool.ngargparser] in pyproject.toml names a file that does not exist."
        exit 1
    fi
    if [ -n "$(printf '%s\n' "$entry" | rules_exclude)" ]; then
        error "$entry is excluded by .distignore, so the tarball would have no entry script. Remove the rule that excludes it."
        exit 1
    fi
    return 0
}

# Fails the build when the build copy lacks a required path. $1 is "copy"
# right after the copy, when only .distignore can have dropped it (the
# preflight already checks that, so this is a second guard), "hooks" after
# scripts/hooks.sh ran, and "startup" after the startup check, whose help
# screens run the tool's own code in the build copy. Before packing, so no
# tarball is made without them. A required path that is a link to what the
# build copy lacks (README -> README.md, with README.md excluded) was copied
# as a link, so no rule names it: it gets a line of its own, with the cause.
# Call at top level only.
check_required() {
    local missing others="" line path target left_out
    missing=$(missing_required "$BUILD_DIR")
    [ -n "$missing" ] || return 0
    while IFS= read -r line; do
        path=${line#  }
        path=${path%%: *}
        if [ ! -L "$BUILD_DIR/$path" ]; then
            others+="$line"$'\n'
            continue
        fi
        target=$(readlink "$BUILD_DIR/$path")
        left_out=$(excluded_target "$path")
        case "$left_out" in
            *$'\n'rules)
                error "$path is a link to $target, which .distignore (or the hidden-file rule) excludes, so the build copy has no $path (${line#*: }): replace the link with the file it points at, or remove the rule that excludes ${left_out%$'\n'*}."
                ;;
            *$'\n'never)
                error "$path is a link to $target, which the build never ships, so the build copy has no $path (${line#*: }): replace the link with the file it points at."
                ;;
            *$'\n'outside)
                error "$path is a link to $target, outside the tool folder, so the build copy has no $path (${line#*: }): replace the link with the file it points at."
                ;;
            *)
                error "$path is a link to $target, which is not in the build copy, so the build copy has no $path (${line#*: }): replace the link with the file it points at."
                ;;
        esac
    done <<< "$missing"
    if [ -n "$others" ]; then
        if [ "$1" = copy ]; then
            error "The build copy lacks files a deploy needs, because .distignore excludes them. Remove the rules that exclude them:"
        elif [ "$1" = startup ]; then
            error "The startup check (the tool's help screens) removed files a deploy needs from the build copy:"
        else
            error "scripts/hooks.sh removed files a deploy needs from the build copy:"
        fi
        printf '%s' "$others" >&2
    fi
    exit 1
}

# The lock comparison: the dependency lists in pyproject.toml against the ones
# uv.lock records for the project ([package.metadata] requires-dist and
# requires-dev of the root package), with names, extras and versions
# normalized as uv writes them. Markers are not compared (uv rewrites them).
# Needs no uv, no network and no package cache. Prints "ok", "stale" and one
# line per difference, "unparsable" and the reason when uv.lock does not parse,
# or "skip" when it cannot tell (a file it cannot read, dynamic dependencies, a
# lock layout it does not know). With the argument "pyproject", it only reads
# pyproject.toml and prints "ok", or "unparsable" and the reason.
# Read with read, not $(cat <<...): bash 3.2 (macOS) misparses a quote inside a
# here-document within $(...). read returns 1 at the end of its input.
IFS= read -r -d '' LOCK_DRIVER <<'PY' || true
import re, sys
from collections import Counter, namedtuple
try:
    import tomllib
except ImportError:
    import tomli as tomllib

Req = namedtuple("Req", "name extras spec direct shown")
VERSION = re.compile(r"""v?(?:(?P<epoch>[0-9]+)!)?(?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?P<pre_l>alpha|a|beta|b|preview|pre|c|rc)[-_.]?(?P<pre_n>[0-9]+)?)?
    (?P<post>-(?P<post_n1>[0-9]+)|[-_.]?(?:post|rev|r)[-_.]?(?P<post_n2>[0-9]+)?)?
    (?P<dev>[-_.]?dev[-_.]?(?P<dev_n>[0-9]+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?""", re.X | re.I)
PRE = {"alpha": "a", "beta": "b", "c": "rc", "pre": "rc", "preview": "rc"}
OPS = ("===", "~=", "==", "!=", "<=", ">=", "<", ">")
REQ = re.compile(r"\s*([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[([^\]]*)\])?\s*(.*)", re.S)
SOURCE_KEYS = ("url", "git", "path", "editable", "directory")
EXTRA = re.compile(r"""extra\s*==\s*['"]([^'"]+)['"]""")


class Unknown(Exception):
    """Something this comparison cannot read: leave the verdict to uv."""


def norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def norm_version(text):
    # PEP 440 normal form, as uv writes it into uv.lock (1.0.0-alpha1 -> 1.0.0a1).
    star = text.endswith(".*")
    m = VERSION.fullmatch(text[:-2] if star else text)
    if not m:
        return text.lower()
    out = "%d!" % int(m["epoch"]) if m["epoch"] and int(m["epoch"]) else ""
    out += ".".join(str(int(part)) for part in m["release"].split("."))
    if m["pre"]:
        out += PRE.get(m["pre_l"].lower(), m["pre_l"].lower()) + str(int(m["pre_n"] or 0))
    if m["post"]:
        out += ".post" + str(int(m["post_n1"] or m["post_n2"] or 0))
    if m["dev"]:
        out += ".dev" + str(int(m["dev_n"] or 0))
    if m["local"]:
        out += "+" + ".".join(str(int(p)) if p.isdigit() else p for p in re.split(r"[-_.]", m["local"].lower()))
    return out + (".*" if star else "")


def norm_spec(spec):
    # uv drops spaces and sorts the clauses, so compare them as a set.
    clauses = set()
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        op = next((op for op in OPS if clause.startswith(op)), None)
        if op is None:
            raise Unknown(clause)
        version = clause[len(op):].strip()
        clauses.add(op + (version if op == "===" else norm_version(version)))
    return tuple(sorted(clauses))


def norm_extras(extras):
    return tuple(sorted({norm(e.strip()) for e in extras if e.strip()}))


def from_pyproject(text):
    if not isinstance(text, str):
        raise Unknown(text)
    m = REQ.fullmatch(text)
    if not m:
        raise Unknown(text)
    name, extras, rest = m.groups()
    rest = rest.strip()
    direct = rest.startswith("@")
    spec = "" if direct else rest.split(";", 1)[0].strip()
    if spec.startswith("(") and spec.endswith(")"):
        spec = spec[1:-1]
    return Req(norm(name), norm_extras(extras.split(",") if extras else []), norm_spec(spec), direct, text.strip())


def from_lock(entry):
    extras = entry.get("extras", [])
    shown = entry["name"] + ("[%s]" % ",".join(extras) if extras else "") + entry.get("specifier", "")
    if entry.get("marker"):
        shown += "; " + entry["marker"]
    direct = any(key in entry for key in SOURCE_KEYS)
    return Req(norm(entry["name"]), norm_extras(extras), norm_spec(entry.get("specifier", "")), direct, shown)


def compare(label, ours, theirs, loose, out):
    # A name with a uv source or a direct URL has no specifier in uv.lock.
    loose = loose | {r.name for r in ours + theirs if r.direct}

    def key(r):
        return (r.name, r.extras, () if r.name in loose else r.spec)

    mine, locked = Counter(map(key, ours)), Counter(map(key, theirs))
    only_mine, only_locked = mine - locked, locked - mine
    for name in sorted({k[0] for k in only_mine + only_locked}):
        a = [r.shown for r in ours if r.name == name and key(r) in only_mine]
        b = [r.shown for r in theirs if r.name == name and key(r) in only_locked]
        if not b:
            out.append("  added to %s: %s" % (label, " and ".join(a)))
        elif not a:
            out.append("  removed from %s: %s" % (label, " and ".join(b)))
        else:
            out.append("  changed in %s: %s (uv.lock has %s)" % (label, " and ".join(a), " and ".join(b)))


def groups_of(pyproject):
    raw = pyproject.get("dependency-groups", {})
    by_norm = {norm(k): k for k in raw}

    def expand(name, seen):
        for item in raw[by_norm[name]]:
            if isinstance(item, dict) and "include-group" in item:
                target = norm(item["include-group"])
                if target not in by_norm:
                    raise Unknown(item)
                if target not in seen:
                    yield from expand(target, seen | {target})
            else:
                yield item

    groups = {name: list(expand(name, {name})) for name in by_norm}
    legacy = pyproject.get("tool", {}).get("uv", {}).get("dev-dependencies")
    if legacy:
        groups["dev"] = groups.get("dev", []) + list(legacy)
    return groups


def main(root):
    with open(root + "/pyproject.toml", "rb") as f:
        pyproject = tomllib.load(f)
    with open(root + "/uv.lock", "rb") as f:
        lock = tomllib.load(f)
    project = pyproject.get("project")
    if not isinstance(project, dict) or "name" not in project:
        raise Unknown("no [project] name")
    if {"dependencies", "optional-dependencies"} & set(project.get("dynamic", [])):
        raise Unknown("dynamic dependencies")
    roots = [p for p in lock.get("package", []) if "." in (p.get("source", {}).get(k) for k in ("virtual", "editable", "directory"))]
    if len(roots) != 1:
        raise Unknown("no root package")
    package = roots[0]
    metadata = package.get("metadata")
    if metadata is None:
        # uv leaves the table out when there is nothing to record, but an
        # older lock may lack it while it resolves dependencies.
        if package.get("dependencies") or package.get("optional-dependencies") or package.get("dev-dependencies"):
            raise Unknown("no metadata")
        metadata = {}
    loose = {norm(name) for name in pyproject.get("tool", {}).get("uv", {}).get("sources", {})}
    out = []
    if norm(package["name"]) != norm(project["name"]):
        out.append("  project name: pyproject.toml has %s, uv.lock has %s" % (project["name"], package["name"]))

    # Optional dependencies sit in uv.lock's requires-dist with an extra marker.
    ours = {"dependencies": [from_pyproject(t) for t in project.get("dependencies", [])]}
    for extra, reqs in project.get("optional-dependencies", {}).items():
        ours.setdefault("optional-dependencies.%s" % norm(extra), []).extend(map(from_pyproject, reqs))
    theirs = {}
    for entry in metadata.get("requires-dist", []):
        m = EXTRA.search(entry.get("marker", ""))
        label = "optional-dependencies.%s" % norm(m.group(1)) if m else "dependencies"
        theirs.setdefault(label, []).append(from_lock(entry))
    for label in sorted(set(ours) | set(theirs)):
        compare(label, ours.get(label, []), theirs.get(label, []), loose, out)

    # Dependency groups, when uv.lock records them.
    if "requires-dev" in metadata or not package.get("dev-dependencies"):
        mine = {name: [from_pyproject(t) for t in reqs] for name, reqs in groups_of(pyproject).items()}
        locked = {norm(name): [from_lock(e) for e in reqs] for name, reqs in metadata.get("requires-dev", {}).items()}
        for name in sorted(set(mine) | set(locked)):
            compare("dependency-groups.%s" % name, mine.get(name, []), locked.get(name, []), loose, out)
    return out


def read_toml(data):
    # The TOML document in the bytes data and None, or None and why it does
    # not parse: a merge conflict marker's line, or where the reader stopped.
    try:
        return tomllib.loads(data.decode("utf-8")), None
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text"
    except tomllib.TOMLDecodeError as exc:
        for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
            if re.match(r"(<<<<<<<|=======|>>>>>>>)( |$)", line):
                return None, "a merge conflict marker at line %d" % number
        message = str(exc).splitlines()[0] if str(exc) else "not TOML"
        m = re.fullmatch(r"(.*) \(at (line \d+, column \d+|end of document)\)", message)
        if m:
            where = "the end of the file" if m.group(2) == "end of document" else m.group(2)
            message = "%s: %s" % (where, m.group(1))
        return None, message


def unparsable(root):
    # Why uv cannot read uv.lock either, or None: it is not TOML (a merge
    # conflict, a cut-off file), or it has no version field (an empty file).
    try:
        with open(root + "/uv.lock", "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data.strip():
        return "the file is empty"
    lock, reason = read_toml(data)
    if reason:
        return reason
    if "version" not in lock:
        return "it has no version field"
    return None


def pyproject_unparsable(root):
    # Why pyproject.toml does not parse, or None. A UTF-8 byte order mark is
    # skipped, as uv skips it.
    try:
        with open(root + "/pyproject.toml", "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return read_toml(data)[1]


def entry(root):
    # "entry <value>" for the entry under [tool.ngargparser], however the TOML
    # spells that table, "none" when it is not set, "not-a-string" when its
    # value is no string on one line, "empty" when it is "", or None when
    # pyproject.toml does not parse here (the build then reads it line by
    # line).
    try:
        with open(root + "/pyproject.toml", "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    doc = read_toml(data)[0]
    if doc is None:
        return None
    table = doc.get("tool")
    table = table.get("ngargparser") if isinstance(table, dict) else None
    if not isinstance(table, dict) or "entry" not in table:
        return "none"
    value = table["entry"]
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        return "not-a-string"
    if value == "":
        return "empty"
    return "entry " + value


if sys.argv[2:] == ["entry"]:
    result = entry(sys.argv[1])
    if result:
        print(result)
    sys.exit(0)
if sys.argv[2:] == ["pyproject"]:
    reason = pyproject_unparsable(sys.argv[1])
    print("unparsable\n" + reason if reason else "ok")
    sys.exit(0)
reason = unparsable(sys.argv[1])
if reason:
    print("unparsable")
    print(reason)
    sys.exit(0)
try:
    differences = main(sys.argv[1])
except Exception:
    print("skip")
else:
    print("stale" if differences else "ok")
    for line in differences:
        print(line)
PY

# Runs the lock driver, with the arguments "$@" after the tool folder, with
# the first Python that reads TOML (tomllib, Python 3.11+, or tomli): the
# project's .venv, python3, else one uv finds offline. Prints nothing when
# there is none.
toml_python_run() {
    close_lock_fd
    local py probe='try:
    import tomllib
except ImportError:
    import tomli'
    for py in "$PROJECT_ROOT/.venv/bin/python" python3; do
        if command -v "$py" >/dev/null 2>&1 && "$py" -I -c "$probe" </dev/null >/dev/null 2>&1; then
            "$py" -I -c "$LOCK_DRIVER" "$PROJECT_ROOT" "$@" </dev/null 2>/dev/null || true
            return 0
        fi
    done
    if command -v uv >/dev/null 2>&1; then
        UV_PYTHON_DOWNLOADS=never uv run --no-project --offline --python '>=3.11' \
            python -I -c "$LOCK_DRIVER" "$PROJECT_ROOT" "$@" </dev/null 2>/dev/null || true
    fi
    return 0
}

# The lock comparison.
compare_lock() {
    toml_python_run
}

# 'uv lock --check --offline', run once: the pyproject.toml check and the
# lock check both read its exit code and output. In the background, as
# wait_script explains, and without fd 9, so a uv that outlives the build
# cannot keep the lock. Call at top level only.
UV_CHECK_RC=""
UV_CHECK_OUTPUT=""
uv_lock_check() {
    [ -z "$UV_CHECK_RC" ] || return 0
    UV_CHECK_RC=0
    (cd "$PROJECT_ROOT" && unset_uv_overrides && uv lock --check --offline) </dev/null >"$TMPWORK/uv-lock-check" 2>&1 8>&- 9>&- &
    SCRIPT_PID=$!
    wait_script || UV_CHECK_RC=$?
    UV_CHECK_OUTPUT=$(cat "$TMPWORK/uv-lock-check")
    return 0
}

# The settings that change how uv resolves (resolution, prerelease,
# fork-strategy, exclude-newer, exclude-newer-package, no-sources) in the
# configuration files uv 0.11 reads for the tool: the file UV_CONFIG_FILE
# names, alone; else the first uv.toml, or pyproject.toml with a [tool.uv]
# table, in the tool folder or a folder above it, then the user's uv.toml and
# the system's. None with UV_NO_CONFIG. Prints "<scope> <key> in <file>" per
# setting: scope "tool" for the tool folder's own uv.toml or pyproject.toml,
# which ship, "machine" for the others, which the deploy host does not have.
# A file in the tool folder is named relative to it.
uv_resolution_settings() {
    local dir file scope table files=() dirs=() system=""
    env_flag UV_NO_CONFIG && return 0
    if [ -n "${UV_CONFIG_FILE:-}" ]; then
        # uv runs in the tool folder, where a relative path starts.
        case "$UV_CONFIG_FILE" in
            /*) files=("$UV_CONFIG_FILE") ;;
            *) files=("$PROJECT_ROOT/$UV_CONFIG_FILE") ;;
        esac
    else
        dir=$PROJECT_ROOT
        while [ -n "$dir" ]; do
            if [ -f "$dir/uv.toml" ]; then
                files+=("$dir/uv.toml")
                break
            fi
            if [ -f "$dir/pyproject.toml" ] &&
                grep -q '^[[:space:]]*\[\{1,2\}[[:space:]]*tool[[:space:]]*\.[[:space:]]*uv[[:space:]]*[].]' "$dir/pyproject.toml"; then
                files+=("$dir/pyproject.toml")
                break
            fi
            [ "$dir" != / ] || break
            dir=$(dirname -- "$dir")
        done
        files+=("${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml")
        IFS=: read -r -a dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"
        for dir in "${dirs[@]}"; do
            if [ -n "$dir" ] && [ -f "$dir/uv/uv.toml" ]; then
                system="$dir/uv/uv.toml"
                break
            fi
        done
        files+=("${system:-/etc/uv/uv.toml}")
    fi
    for file in "${files[@]}"; do
        { [ -f "$file" ] && [ -r "$file" ]; } || continue
        scope=machine
        if [ -z "${UV_CONFIG_FILE:-}" ] && { [ "$file" = "$PROJECT_ROOT/uv.toml" ] || [ "$file" = "$PROJECT_ROOT/pyproject.toml" ]; }; then
            scope=tool
        fi
        table=""
        case "$file" in */pyproject.toml) table="[tool.uv]" ;; esac
        TABLE="$table" PREFIX="$scope " SUFFIX=" in ${file#"$PROJECT_ROOT"/}" awk '
            BEGIN { table = ENVIRON["TABLE"]; inside = (table == "") }
            { sub(/\r$/, "") }
            /^[[:space:]]*\[/ {
                name = $0
                sub(/[[:space:]]*#.*$/, "", name)
                gsub(/[[:space:]]/, "", name)
                inside = (name == table)
                next
            }
            inside && match($0, /^[[:space:]]*(resolution|prerelease|fork-strategy|exclude-newer|exclude-newer-package|no-sources)[[:space:]]*=/) {
                key = substr($0, RSTART, RLENGTH)
                gsub(/[[:space:]=]/, "", key)
                print ENVIRON["PREFIX"] key ENVIRON["SUFFIX"]
            }' "$file" 2>/dev/null || true
    done
    return 0
}

# Under a failed lock check, on stderr: the settings uv_resolution_settings
# finds. One the deploy host does not have may be why uv.lock looks out of
# date, and 'uv lock' would then lock with it.
uv_settings_hint() {
    local settings list
    settings=$(uv_resolution_settings)
    [ -n "$settings" ] || return 0
    list=$(printf '%s\n' "$settings" | sed 's/^[^ ]* //' | awk '{ printf "%s%s", (NR > 1 ? ", " : ""), $0 }')
    if printf '%s\n' "$settings" | grep -q '^machine '; then
        echo "uv reads settings that change how it resolves: $list. One outside the tool folder applies on this machine only, not on the deploy host: if it is why uv.lock looks out of date, remove it there instead of running 'uv lock'." >&2
    else
        echo "uv reads settings that change how it resolves: $list." >&2
    fi
    return 0
}

# Why uv cannot read the TOML file $1, from uv's output $2: a merge conflict
# marker's line, else uv's cause and its last line, such as "TOML parse error
# at line 4, column 9: key with no value, expected `=`".
uv_parse_reason() {
    local reason last marker
    reason=$(printf '%s\n' "$2" | sed -n 's/^[[:space:]]*Caused by: //p' | sed -n 1p)
    last=$(printf '%s\n' "$2" | sed '/^[[:space:]]*$/d' | tail -n 1)
    case "$last" in
        *"|"*|*'Caused by:'*|*'Failed to parse'*) ;;
        *) reason="${reason:+$reason: }$last" ;;
    esac
    marker=$(grep -n -m 1 -E '^(<<<<<<<|=======|>>>>>>>)( |$)' "$1" | cut -d: -f1) || marker=""
    if [ -n "$marker" ]; then
        reason="a merge conflict marker at line $marker"
    fi
    printf '%s\n' "${reason:-uv cannot read it}"
}

# build/ is the build's output folder, which make build replaces and make
# clean deletes. Anything else named build (a file, a broken link) is the
# tool's own: make clean deleted it, and make build failed at mkdir after
# the previous tarball was gone. Both stop here instead, before anything is
# removed. A link to a folder is a folder here, as before.
check_build_folder() {
    if { [ -e "$PROJECT_ROOT/build" ] || [ -L "$PROJECT_ROOT/build" ]; } && [ ! -d "$PROJECT_ROOT/build" ]; then
        error "build is not a folder: make build writes its output into build/, and make clean deletes build/. Move it away (mv build build.old), then run the command again. Nothing was changed."
        exit 1
    fi
}

# make clean (--clean) removes build/ under the build lock, so it never
# deletes the copy a running build is writing. It needs no git and reads no
# build.conf.
if [ "$CLEAN_MODE" = true ]; then
    check_build_folder
    take_build_lock
    trap release_build_lock EXIT
    stop_left_over_script
    rm_tree "$PROJECT_ROOT/build"
    echo "Build directory cleaned."
    exit 0
fi

# git is a hard dependency: the .distignore exclusion file is evaluated with
# `git check-ignore`, and scripts/get_libs.sh is fingerprinted with `git hash-object`.
if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: 'git' is required to build (distignore matching uses 'git check-ignore'). Install git and retry." >&2
    exit 1
fi
# The caller's environment must not redirect our git calls into another repo.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

# Clean up temp state always, and the build directory when a build that got
# past the preflight fails. A preflight stop removes nothing, so the previous
# build copy and tarball stay.
TMPWORK=""
LIBS_TMP=""
TAR_TMP=""
BUILD_STARTED=false
on_exit() {
    local status=$?
    set +e
    # A script or uv still running (the build got a signal) stops first, so
    # nothing writes into what is removed below.
    stop_script
    stop_stderr_capture
    [ -n "$TMPWORK" ] && rm -rf "$TMPWORK"
    [ -n "$LIBS_TMP" ] && rm_tree "$LIBS_TMP"
    [ -n "$LIBS_TMP" ] && rm_libs_lists
    [ -n "$TAR_TMP" ] && rm -f "$TAR_TMP"
    if [ $status -ne 0 ] && [ "$BUILD_STARTED" = true ]; then
        # A build stopped by a signal printed no error to end the progress bar's
        # line, so it is cleared first, as error() does.
        if [ "$PROGRESS_BAR" = true ]; then
            printf '\r\033[K' >&2
        fi
        echo "Build failed; removing $BUILD_DIR" >&2
        [ -n "$BUILD_DIR" ] && rm_tree "$BUILD_DIR"
        # Remove top-level build dir if empty
        if [ -d "$PROJECT_ROOT/build" ] && [ -z "$(ls -A "$PROJECT_ROOT/build")" ]; then
            rmdir "$PROJECT_ROOT/build"
        fi
    fi
    # Last, so the next build cannot start while this one cleans up.
    release_build_lock
    exit $status
}
# Set before the lock and the preflight. Without these, Ctrl-C, a kill or a
# dropped ssh session (HUP) runs on_exit with the status of the last command
# that finished (usually 0), or not at all, so the half-built copy stays, or
# a uv the preflight runs, still holding the lock, outlives the build and
# stops the next one. The codes are 128 + the signal number, as the shell
# reports them.
trap on_exit EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Removes what a build killed with kill -9 (so on_exit did not run) left
# behind: first the get_libs.sh or hooks.sh it left running
# (stop_left_over_script), then its temp folder (make_tmpwork), a stale lock
# it moved aside (take_over_stale_lock) and the tarball tar was writing,
# build/.<name>.partial. This build holds the lock, so no build of this
# folder uses them. A temp folder in TMPDIR is removed only when it is this
# user's and its owner file names this tool folder. One this user may not
# remove (another user's, in a shared tool folder) is left.
remove_build_leftovers() {
    local path
    stop_left_over_script
    for path in "$STATE_DIR"/build-tmp.* "$STATE_DIR"/build.lock.stale-* "$PROJECT_ROOT"/build/.*.tar.gz.partial; do
        { [ -e "$path" ] || [ -L "$path" ]; } || continue
        rm_tree "$path" 2>/dev/null || true
    done
    for path in "${TMPDIR:-/tmp}"/ngbuild-exclude.*; do
        { [ -d "$path" ] && [ ! -L "$path" ] && [ -O "$path" ]; } || continue
        [ "$(sed -n 1p "$path/owner" 2>/dev/null)" = "$PROJECT_ROOT" ] || continue
        rm_tree "$path" 2>/dev/null || true
    done
    return 0
}

# The build's temp folder, printed: .ngargparser/build-tmp.XXXXXX, so a build
# killed with kill -9 leaves it where the next build finds it. Where
# .ngargparser/ is not writable (another user's, in a shared tool folder),
# ${TMPDIR:-/tmp}/ngbuild-exclude.XXXXXX, with this tool folder's path in its
# owner file.
make_tmpwork() {
    local dir
    if dir=$(mktemp -d "$STATE_DIR/build-tmp.XXXXXX" 2>/dev/null); then
        printf '%s\n' "$dir"
        return 0
    fi
    dir=$(mktemp -d "${TMPDIR:-/tmp}/ngbuild-exclude.XXXXXX") || return 1
    printf '%s\n' "$PROJECT_ROOT" > "$dir/owner" || return 1
    printf '%s\n' "$dir"
}

# Before build.conf is read (its temp file goes in TMPWORK) and the
# preflight, so a second build in this folder stops at once.
take_build_lock
remove_build_leftovers
TMPWORK=$(make_tmpwork)

# The build settings build.conf may set start empty, then build.conf is read
# if present, then defaults apply. This lets a tool's build.conf set them
# without touching build.sh (which is framework-owned and gets overwritten by
# `cli sync`).
TOOL_NAME=""
TARBALL_PREFIX=""
# No longer read. Initialized only so a leftover setting in build.conf can be reported.
EXCLUDE_FROM_BUILD_SYMLINK=""
# TOOL_VERSION from the environment wins over one set in build.conf, so a value
# committed to the tool cannot replace the version CI builds (the git ref).
ENV_TOOL_VERSION="${TOOL_VERSION:-}"
TOOL_VERSION_SOURCE="It comes from the TOOL_VERSION environment variable (default local; a '/' becomes '-')."
# build.conf may only set variables. An exit in it, or a command that fails
# (set -e), would end the build right here with no message, and 'exit 0' with
# a success status and no tarball, so read_build_conf turns that into an error.
build_conf_exited() {
    error "scripts/core/build.conf exited (code $1) while being read: it may only set variables."
    exit 1
}
# A file bash reads that was saved with Windows line endings fails there with
# "$'\r': command not found" or "set: pipefail\r: invalid option name", so it
# stops the build first. $1 is the file, $2 its path in the tool folder, $3
# the .gitattributes pattern that keeps it in LF. BSD sed (macOS) reads \r
# as r, so perl converts it there.
check_line_endings() {
    local cr fix
    cr=$(printf '\r')
    { [ -f "$1" ] && grep -q "$cr\$" "$1"; } || return 0
    if [[ "$(uname)" == "Darwin" ]]; then
        fix="perl -pi -e 's/\\r\$//' $2"
    else
        fix="sed -i \"s/\\r\$//\" $2"
    fi
    error "$2 has Windows line endings (CRLF): convert it with '$fix' (and add '$3 text eol=lf' to .gitattributes)"
    exit 1
}
# Sources build.conf in a subshell, with set -e as the build runs, so it can
# set no variable of the build's own (SRC_DIR, PROJECT_ROOT and the others).
# Writes to the file $1, each NUL-terminated: "end", the values of TOOL_NAME,
# TOOL_VERSION, TARBALL_PREFIX and EXCLUDE_FROM_BUILD_SYMLINK, then the name
# of each other variable build.conf set or changed. Bash's own variables, which
# change by themselves, are left out. Writes nothing when build.conf exits or
# a command in it fails; returns the subshell's exit code. The names of its own
# variables start with __ngb_. Call with set +e: set -e is ignored inside a
# command whose status is tested.
read_build_conf() {
    (
        close_lock_fd
        set -e
        __ngb_out=$1
        __ngb_trace=""
        case $- in *x*) __ngb_trace=yes; set +x ;; esac
        # The value each variable has before, kept under a name of its own.
        for __ngb_name in $(compgen -v); do
            case $__ngb_name in __ngb_*) continue ;; esac
            printf -v "__ngb_was_$__ngb_name" '%s' "${!__ngb_name}"
        done
        if [ -n "$__ngb_trace" ]; then set -x; fi
        # shellcheck source=build.conf
        source "$BUILD_SH_DIR/build.conf"
        { set +x; } 2>/dev/null
        {
            printf '%s\0' end "${TOOL_NAME-}" "${TOOL_VERSION-}" "${TARBALL_PREFIX-}" "${EXCLUDE_FROM_BUILD_SYMLINK-}"
            compgen -v | while IFS= read -r __ngb_name; do
                case $__ngb_name in
                    __ngb_*|TOOL_NAME|TOOL_VERSION|TARBALL_PREFIX|EXCLUDE_FROM_BUILD_SYMLINK) continue ;;
                    BASH*|COLUMNS|COMP_*|DIRSTACK|EPOCHREALTIME|EPOCHSECONDS|FUNCNAME|HISTCMD|LINENO|LINES) continue ;;
                    OLDPWD|OPTARG|OPTIND|PIPESTATUS|PWD|RANDOM|SECONDS|SHELLOPTS|SRANDOM|_) continue ;;
                esac
                __ngb_was=__ngb_was_$__ngb_name
                if [ -z "${!__ngb_was+set}" ] || [ "${!__ngb_was}" != "${!__ngb_name}" ]; then
                    printf '%s\0' "$__ngb_name"
                fi
            done
        } > "$__ngb_out"
    )
}
if [ -f "$BUILD_SH_DIR/build.conf" ]; then
    # Else the read below fails as if build.conf had exited.
    if [ ! -r "$BUILD_SH_DIR/build.conf" ]; then
        error "scripts/core/build.conf cannot be read (Permission denied)."
        exit 1
    fi
    check_line_endings "$BUILD_SH_DIR/build.conf" scripts/core/build.conf build.conf
    BUILD_CONF_READ="$TMPWORK/build-conf"
    # Empty when build.conf exits before the values are written.
    : > "$BUILD_CONF_READ"
    set +e
    read_build_conf "$BUILD_CONF_READ"
    BUILD_CONF_RC=$?
    set -e
    BUILD_CONF_VALUES=()
    while IFS= read -r -d '' conf_value; do
        BUILD_CONF_VALUES+=("$conf_value")
    done < "$BUILD_CONF_READ"
    rm -f "$BUILD_CONF_READ"
    if [ "${BUILD_CONF_VALUES[0]:-}" != end ]; then
        build_conf_exited "$BUILD_CONF_RC"
    fi
    TOOL_NAME=${BUILD_CONF_VALUES[1]}
    TOOL_VERSION=${BUILD_CONF_VALUES[2]}
    TARBALL_PREFIX=${BUILD_CONF_VALUES[3]}
    EXCLUDE_FROM_BUILD_SYMLINK=${BUILD_CONF_VALUES[4]}
    for conf_value in "${BUILD_CONF_VALUES[@]:5}"; do
        notice "Notice: scripts/core/build.conf sets $conf_value, which the build does not read (only TOOL_NAME, TOOL_VERSION and TARBALL_PREFIX)."
    done
fi
if [ -n "$ENV_TOOL_VERSION" ]; then
    if [ "${TOOL_VERSION:-}" != "$ENV_TOOL_VERSION" ]; then
        notice "Warning: scripts/core/build.conf sets TOOL_VERSION=${TOOL_VERSION:-}, which is ignored: the TOOL_VERSION environment variable ($ENV_TOOL_VERSION) wins. Remove TOOL_VERSION from build.conf."
    fi
    TOOL_VERSION="$ENV_TOOL_VERSION"
elif [ -n "${TOOL_VERSION:-}" ]; then
    TOOL_VERSION_SOURCE="It comes from scripts/core/build.conf, which applies only when the TOOL_VERSION environment variable is not set (a '/' becomes '-')."
fi

# Apply defaults when not set by build.conf
[ -z "$TOOL_NAME" ] && TOOL_NAME="ng_${APP_NAME}"
# pull the tool version from the environment, otherwise set it to 'local'
TOOL_VERSION="${TOOL_VERSION:-local}"
# Replace '/' with '-': CI often passes the git ref name as TOOL_VERSION, and a
# branch like 'feature/foo' would otherwise break the version-stamping sed
# (s/TOOL_VERSION/.../: the slash ends the command) and nest TOOL_DIR/BUILD_DIR.
TOOL_VERSION="${TOOL_VERSION//\//-}"

# TOOL_NAME and TOOL_VERSION name the build copy, which a failed build removes,
# so each must be one plain folder name: a '..' or '/' would point that removal
# outside build/, and a leading '-' would read as an option to tar. Checked
# before the build copy is made or anything of an earlier build is removed
# (but what a killed build left). The letters are spelled out because a
# range such as A-Z can match accented letters in some locales.
check_name() {
    local var="$1" value="$2" source="$3"
    if [ -z "$value" ] || [ "$value" = "." ] || [ "$value" = ".." ] || [[ "$value" == -* ]] ||
        [[ ! "$value" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._+-]+$ ]]; then
        error "$var '$value' is not allowed: use only letters, digits, '.', '_', '+' and '-' (not '.' or '..' alone, and not starting with '-'). $source"
        exit 1
    fi
}
check_name TOOL_NAME "$TOOL_NAME" "It comes from scripts/core/build.conf, or is ng_<tool folder name> when build.conf does not set it."
check_name TOOL_VERSION "$TOOL_VERSION" "$TOOL_VERSION_SOURCE"
# TARBALL_PREFIX starts the tarball's file name, so the same letters apply: a
# '/' would make tar fail late, after the previous build copy is gone, a
# leading '-' would read as an option, and a leading '.' would hide the
# tarball (build/*.tar.gz, the CI job's artifact pattern, can miss it). Empty
# means IEDB_.
if [ -n "$TARBALL_PREFIX" ] && { [[ "$TARBALL_PREFIX" == [-.]* ]] ||
    [[ ! "$TARBALL_PREFIX" =~ ^[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._+-]+$ ]]; }; then
    error "TARBALL_PREFIX '$TARBALL_PREFIX' is not allowed: use only letters, digits, '.', '_', '+' and '-' (not starting with '-' or '.'), or leave it empty for IEDB_. It comes from scripts/core/build.conf."
    exit 1
fi

TOOL_DIR=$TOOL_NAME-$TOOL_VERSION
BUILD_DIR=$PROJECT_ROOT/build/$TOOL_DIR

[ -z "$TARBALL_PREFIX" ] && TARBALL_PREFIX="IEDB_"
TAR_NAME="${TARBALL_PREFIX}$(echo "$TOOL_NAME" | tr '[:lower:]' '[:upper:]')-${TOOL_VERSION}.tar.gz"
# A file name may be at most 255 bytes, and tar first writes the tarball as
# .<its name>.partial, 9 more: a longer name (a CI job's long branch name as
# TOOL_VERSION) failed only at the tarball, with tar's "File name too long",
# after the previous build was removed. The names hold ASCII letters only, so
# a character is a byte. The build copy's name is shorter than the tarball's.
TAR_NAME_MAX=246
if [ "${#TAR_NAME}" -gt "$TAR_NAME_MAX" ]; then
    TAR_NAME_ROOM=$((TAR_NAME_MAX - ${#TAR_NAME} + ${#TOOL_VERSION}))
    if [ "$TAR_NAME_ROOM" -ge 1 ]; then
        error "TOOL_VERSION is ${#TOOL_VERSION} characters long, which makes the tarball's name ${#TAR_NAME} characters, and at most $TAR_NAME_MAX fit (a file name may have 255 bytes, and the build first writes .<name>.partial): use a TOOL_VERSION of at most $TAR_NAME_ROOM characters. $TOOL_VERSION_SOURCE"
    else
        error "TOOL_NAME and TARBALL_PREFIX in scripts/core/build.conf make the tarball's name ${#TAR_NAME} characters with TOOL_VERSION, and at most $TAR_NAME_MAX fit (a file name may have 255 bytes, and the build first writes .<name>.partial): shorten them."
    fi
    exit 1
fi

# ---------------------------------------------------------------------------
# Preflight: fail before anything is removed.
# Removed layouts stop the build instead of being ignored, so an old exclusion
# file or hook name never changes what ships without anyone noticing.
# ---------------------------------------------------------------------------
# A cli sync keeps the files it may change, as they were before it, in
# .ngargparser/sync-in-progress/<time>Z-<pid>/, with a marker beside it,
# <that name>.json, until it ends; a sync whose undo failed leaves them in
# .ngargparser/failed-restore-<time>/, and its marker names that folder
# ("files"). A marker whose folder is still there is a sync that did not
# finish (killed, a power cut, a failed undo), as cli sync judges it, and the
# framework files may be half updated. A sync that runs holds the build lock,
# taken above, so none runs here now. A marker without its folder is not
# one: cli sync removes it. Prints the folder, relative to the tool folder.
unfinished_sync() {
    local marker folder
    for marker in "$STATE_DIR"/sync-in-progress/*.json; do
        [ -f "$marker" ] || continue
        folder=$(sed -n 's/.*"files": *"\([^"\\]*\)".*/\1/p' "$marker" 2>/dev/null | sed -n 1p) || folder=""
        [ -n "$folder" ] || folder=${marker%.json}
        case "$folder" in
            /*) ;;
            *) folder="$PROJECT_ROOT/$folder" ;;
        esac
        if [ -d "$folder" ]; then
            printf '%s\n' "${folder#"$PROJECT_ROOT"/}"
            return 0
        fi
    done
    return 0
}
UNFINISHED_SYNC=$(unfinished_sync)
if [ -n "$UNFINISHED_SYNC" ]; then
    error "a cli sync did not finish here (${UNFINISHED_SYNC%/}/): run cli sync to see what to do, then build again."
    exit 1
fi
# scripts/get_libs.sh was called scripts/deps.sh.
check_no_deps_sh() {
    if [ -f "$SRC_DIR/deps.sh" ]; then
        error "scripts/deps.sh is no longer run. Rename it to scripts/get_libs.sh."
        exit 1
    fi
}
# libs/ must be a real folder: the build copies it, and the fetch moves what
# scripts/get_libs.sh fetched into it.
check_libs_folder() {
    if [ -L "$LIBS_DIR" ]; then
        error "libs/ is a symlink (-> $(readlink "$LIBS_DIR")). Replace it with a real directory: the build copies libs/, and fetched dependencies are written into it."
        exit 1
    fi
    if [ -e "$LIBS_DIR" ] && [ ! -d "$LIBS_DIR" ]; then
        error "libs exists but is not a directory."
        exit 1
    fi
}
# make libs (--libs-only) runs only the fetch into libs/, with the stamp and
# the TOOL_VERSION a build uses, so the code scripts/get_libs.sh fetches is
# there for a run in the tool folder before the first make build, and that
# build does not fetch it again.
if [ "$LIBS_ONLY" = true ]; then
    check_no_deps_sh
    check_line_endings "$GET_LIBS_SCRIPT" scripts/get_libs.sh '*.sh'
    check_libs_folder
    fetch_libs
    if [ -f "$GET_LIBS_SCRIPT" ]; then
        echo "libs/ is up to date with scripts/get_libs.sh."
    fi
    exit 0
fi
if [ ! -f "$PROJECT_ROOT/.distignore" ]; then
    for legacy in scripts/do-not-distribute.txt do-not-distribute.txt scripts/.distignore; do
        if [ -f "$PROJECT_ROOT/$legacy" ]; then
            error "$legacy is no longer read. Move it to .distignore at the top of the tool folder: git mv $legacy .distignore. .distignore uses gitignore patterns, so a bare name also matches nested files and folders of that name; write /name to match only the top-level one, as $legacy did."
            exit 1
        fi
    done
    notice "Notice: no .distignore; using the default rules (see cli generate's .distignore)."
fi
# Else git check-ignore would get none of its rules, and the build would stop
# only after the previous build copy is gone.
if [ -f "$PROJECT_ROOT/.distignore" ] && [ ! -r "$PROJECT_ROOT/.distignore" ]; then
    error ".distignore cannot be read (Permission denied): run 'chmod a+r .distignore' (or ask its owner to), then build again."
    exit 1
fi
if [ ! -f "$SRC_DIR/hooks.sh" ]; then
    for legacy in build_hooks.sh dependencies.sh; do
        if [ -f "$SRC_DIR/$legacy" ]; then
            error "scripts/$legacy is no longer run. Rename it to scripts/hooks.sh (cli sync does this for you)."
            exit 1
        fi
    done
fi
if [ -f "$SRC_DIR/build.conf" ]; then
    if [ -f "$BUILD_SH_DIR/build.conf" ]; then
        error "scripts/build.conf is no longer read, and scripts/core/build.conf already exists. Merge scripts/build.conf into scripts/core/build.conf by hand, then delete scripts/build.conf."
    else
        error "scripts/build.conf is no longer read. Move it to scripts/core/build.conf (cli sync does this for you)."
    fi
    exit 1
fi
check_no_deps_sh
check_line_endings "$GET_LIBS_SCRIPT" scripts/get_libs.sh '*.sh'
check_line_endings "$SRC_DIR/hooks.sh" scripts/hooks.sh '*.sh'
# The deploy host runs these two: bash runs deploy/install.sh, and it runs
# configure with sh.
check_line_endings "$PROJECT_ROOT/deploy/install.sh" deploy/install.sh '*.sh'
check_line_endings "$PROJECT_ROOT/configure" configure configure
check_libs_folder
check_build_folder
if [ -n "$EXCLUDE_FROM_BUILD_SYMLINK" ]; then
    notice "Note: EXCLUDE_FROM_BUILD_SYMLINK in scripts/core/build.conf no longer has any effect (the build copies everything); you can delete it."
fi
# A tool folder without what a deploy needs, or whose .distignore excludes it,
# stops here, so the previous build copy and tarball stay.
MISSING=$(missing_required "$PROJECT_ROOT")
if [ -n "$MISSING" ]; then
    error "The tool folder lacks files a deploy needs, so nothing was built:"
    printf '%s\n' "$MISSING" >&2
fi
EXCLUDED_REQUIRED=$(excluded_required)
if [ -n "$EXCLUDED_REQUIRED" ]; then
    error ".distignore excludes files a deploy needs, so nothing was built. Remove the rules that exclude them:"
    printf '%s\n' "$EXCLUDED_REQUIRED" >&2
fi
if [ -n "$MISSING" ] || [ -n "$EXCLUDED_REQUIRED" ]; then
    exit 1
fi
# deploy/install.sh runs ./configure, which needs its exec bit; git records
# the bit, so a fix must be committed too.
if [ ! -x "$PROJECT_ROOT/configure" ]; then
    error "configure is not executable: run 'chmod +x configure' and commit the mode (git update-index --chmod=+x configure)"
    exit 1
fi
# A pyproject.toml that does not parse (a merge conflict left in it, a typo)
# fails 'uv sync' at deploy time. The Python that runs the lock comparison
# reads it first, and a merge conflict marker stops the build at once. For
# any other error uv decides when it is on the PATH, because it reads newer
# TOML than Python's tomllib (such as an inline table over several lines):
# only its "Failed to parse" stops the build. Without such a Python, that is
# the only signal; without uv, Python's verdict stands.
pyproject_unparsable() {
    error "pyproject.toml does not parse ($1): fix it, then build again"
    exit 1
}
PYPROJECT_CHECK=$(toml_python_run pyproject)
PYPROJECT_REASON=""
if [ "$(printf '%s\n' "$PYPROJECT_CHECK" | sed -n 1p)" = unparsable ]; then
    PYPROJECT_REASON=$(printf '%s\n' "$PYPROJECT_CHECK" | sed -n 2p)
fi
case "$PYPROJECT_REASON" in
    "a merge conflict marker at line "*) pyproject_unparsable "$PYPROJECT_REASON" ;;
esac
if [ "$PYPROJECT_CHECK" != ok ] && command -v uv >/dev/null 2>&1; then
    uv_lock_check
    if printf '%s\n' "$UV_CHECK_OUTPUT" | grep -q '^error: Failed to parse:\{0,1\} `[^`]*pyproject\.toml`'; then
        pyproject_unparsable "${PYPROJECT_REASON:-$(uv_parse_reason "$PROJECT_ROOT/pyproject.toml" "$UV_CHECK_OUTPUT")}"
    fi
    PYPROJECT_REASON=""
fi
if [ -n "$PYPROJECT_REASON" ]; then
    pyproject_unparsable "$PYPROJECT_REASON"
fi
check_entry
# A stale uv.lock installs other versions than pyproject.toml asks for. Two
# checks, both offline, so neither waits on the network:
#   1. the lock comparison above, which needs no uv and no package cache;
#   2. 'uv lock --check --offline', which also catches other drift (such as
#      requires-python), but cannot run when the change needs a package that
#      is not in uv's cache, as a dependency added by hand usually does.
# uv exits 1 with "needs to be updated" for a stale lock, and its verdict wins
# when it runs. Any other uv failure (no cached package, no matching Python,
# an older uv) only means its check could not run; the comparison then
# decides. Without uv and without a Python that reads TOML, nothing is
# checked, and a notice says so.
# A uv.lock that does not parse (a merge conflict, a cut-off or empty file)
# stops the build first: uv sync --frozen fails on it at deploy time, and
# 'uv lock' cannot read it either. The Python that runs the comparison says
# so; without one, uv's "Failed to parse" does. A uv.lock that is TOML but
# that uv rejects (a version changed by hand without its hashes, as in a
# merge resolved by hand) stops it too, with uv's lines ($2) below the error.
lock_unparsable() {
    error "uv.lock does not parse ($1), so 'uv lock' cannot read it either: restore it (after a merge: git checkout --theirs uv.lock) or delete it, then run 'uv lock' and commit uv.lock"
    if [ -n "${2:-}" ]; then
        printf '%s\n' "$2" >&2
    fi
    exit 1
}
if [ -f "$PROJECT_ROOT/uv.lock" ]; then
    LOCK_COMPARE=$(compare_lock)
    if [ "$(printf '%s\n' "$LOCK_COMPARE" | sed -n 1p)" = unparsable ]; then
        lock_unparsable "$(printf '%s\n' "$LOCK_COMPARE" | sed -n 2p)"
    fi
    LOCK_DIFF=""
    if [ "$(printf '%s\n' "$LOCK_COMPARE" | sed -n 1p)" = stale ]; then
        LOCK_DIFF=$(printf '%s\n' "$LOCK_COMPARE" | sed 1d)
    fi
    LOCK_RC=""
    LOCK_OUTPUT=""
    if command -v uv >/dev/null 2>&1; then
        uv_lock_check
        LOCK_RC=$UV_CHECK_RC
        LOCK_OUTPUT=$UV_CHECK_OUTPUT
    fi
    if [ "$LOCK_RC" = 2 ] && [[ "$LOCK_OUTPUT" == *'Failed to parse `uv.lock`'* ]]; then
        if [ -z "$LOCK_COMPARE" ]; then
            lock_unparsable "$(uv_parse_reason "$PROJECT_ROOT/uv.lock" "$LOCK_OUTPUT")"
        fi
        # The comparison read it as TOML, so uv rejects what it holds: its
        # first lines of substance, without its progress lines.
        lock_unparsable "uv rejects what it holds: see below" "$(printf '%s\n' "$LOCK_OUTPUT" |
            sed -e '/^[[:space:]]*$/d' -e '/^Using /d' -e '/^Resolved /d' | sed -n '1,8s/^/    /p')"
    fi
    if { [ "$LOCK_RC" = 1 ] && [[ "$LOCK_OUTPUT" == *"needs to be updated"* ]]; } ||
        { [ "$LOCK_RC" != 0 ] && [ -n "$LOCK_DIFF" ]; }; then
        error "uv.lock is out of date with pyproject.toml: run 'uv lock' and commit uv.lock"
        if [ -n "$LOCK_DIFF" ]; then
            printf '%s\n' "$LOCK_DIFF" >&2
        fi
        # uv's own reason, when it gives one, such as "Ignoring existing
        # lockfile due to change in resolution mode", and the settings that
        # can cause it.
        printf '%s\n' "$LOCK_OUTPUT" | sed -n 's/^[[:space:]]*\(.*existing lockfile due to .*\)$/    \1/p' >&2
        uv_settings_hint
        exit 1
    elif [ -z "$LOCK_RC" ] && [ -z "$LOCK_COMPARE" ]; then
        # No uv, and no Python to run the comparison: common on HPC hosts.
        notice "Lock check skipped: uv is not on the PATH, and no Python that reads TOML was found (.venv/bin/python or python3, at 3.11 or later or with tomli), so uv.lock was not checked against pyproject.toml."
    elif [ -n "$LOCK_RC" ] && [ "$LOCK_RC" -ne 0 ]; then
        # uv's first lines of substance, without its progress lines.
        LOCK_REASON=$(printf '%s\n' "$LOCK_OUTPUT" |
            sed -e '/^[[:space:]]*$/d' -e '/^Using /d' -e '/^Resolved /d' | sed -n '1,3s/^/    /p')
        if [ "$LOCK_COMPARE" = ok ]; then
            notice "Lock check partly skipped: the dependencies in pyproject.toml match uv.lock, but 'uv lock --check --offline' could not run (exit $LOCK_RC), so other changes such as requires-python were not checked:${NL}$LOCK_REASON"
        else
            notice "Lock check skipped: 'uv lock --check --offline' could not run (exit $LOCK_RC):${NL}$LOCK_REASON"
        fi
    fi
else
    # deploy/install.sh then runs a plain 'uv sync --no-dev', which resolves
    # anew on the deploy host.
    notice "Notice: the tool has no uv.lock, so the deploy installs the newest versions pyproject.toml allows: run 'uv lock' and commit uv.lock."
fi
# The README and the description cli generate writes hold placeholders, which
# would reach the tool's users. A notice, not a stop: a local build before the
# text is written is fine. README placeholders are the template's lines that
# start with one of its verbs, such as <Describe what my-app does: ...>.
PLACEHOLDER=""
if [ -f "$PROJECT_ROOT/README" ]; then
    PLACEHOLDER=$(awk 'match($0, /<(Describe|Summarize|List|Replace) [abcdefghijklmnopqrstuvwxyz]/) {
        print substr($0, RSTART + 1, RLENGTH - 3); exit }' "$PROJECT_ROOT/README")
fi
if [ -n "$PLACEHOLDER" ]; then
    notice "Notice: README still has template placeholders (<$PLACEHOLDER ...>): fill them in before you deploy."
fi
if [ -f "$PROJECT_ROOT/pyproject.toml" ] && [ -n "$(awk -v sq="'" '
    { sub(/\r$/, "") }
    /^[[:space:]]*\[/ {
        table = $0
        sub(/[[:space:]]*#.*$/, "", table)
        gsub(/[[:space:]]/, "", table)
        in_project = (table == "[project]")
        next
    }
    in_project && /^[[:space:]]*description[[:space:]]*=/ {
        value = $0
        sub(/^[[:space:]]*description[[:space:]]*=[[:space:]]*/, "", value)
        if (value ~ /^"TODO/ || value ~ ("^" sq "TODO")) print "todo"
        exit
    }' "$PROJECT_ROOT/pyproject.toml")" ]; then
    notice "Notice: the description in pyproject.toml still starts with TODO: fill it in before you deploy."
fi

# The seconds each help screen of the startup check may take: 60, or
# NGARGPARSER_STARTUP_CHECK_TIMEOUT, as NGARGPARSER_SYNC_HELP_TIMEOUT sets them
# for cli sync's test drive. A value that is not a number of seconds above 0
# and at most 86400 (a day, as cli sync allows: Python waits at most about 24
# days, and fails on more) stops the build here, before anything is removed.
STARTUP_TIMEOUT=${NGARGPARSER_STARTUP_CHECK_TIMEOUT:-}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT#"${STARTUP_TIMEOUT%%[![:space:]]*}"}
STARTUP_TIMEOUT=${STARTUP_TIMEOUT%"${STARTUP_TIMEOUT##*[![:space:]]}"}
# The whole seconds without leading zeros, compared as text first: a long
# number would overflow the shell's arithmetic.
STARTUP_TIMEOUT_WHOLE=${STARTUP_TIMEOUT%%.*}
STARTUP_TIMEOUT_WHOLE=${STARTUP_TIMEOUT_WHOLE#"${STARTUP_TIMEOUT_WHOLE%%[!0]*}"}
if [ -z "$STARTUP_TIMEOUT" ]; then
    STARTUP_TIMEOUT=60
elif [[ ! "$STARTUP_TIMEOUT" =~ ^([0123456789]+[.]?[0123456789]*|[.][0123456789]+)$ ]] ||
    [[ "$STARTUP_TIMEOUT" != *[123456789]* ]] || [ "${#STARTUP_TIMEOUT_WHOLE}" -gt 5 ] ||
    [ "${STARTUP_TIMEOUT_WHOLE:-0}" -gt 86400 ] ||
    { [ "${STARTUP_TIMEOUT_WHOLE:-0}" -eq 86400 ] && [[ "$STARTUP_TIMEOUT" == *.*[123456789]* ]]; }; then
    error "NGARGPARSER_STARTUP_CHECK_TIMEOUT=$NGARGPARSER_STARTUP_CHECK_TIMEOUT is not a number of seconds above 0 and at most 86400: set it to the seconds each help screen of the startup check may take, such as 120, or unset it for 60."
    exit 1
fi

check_tool_paths

start_stderr_capture

# Clean and recreate build directory. The previous tarball goes too, so a
# failed build never leaves an old one that looks fresh.
step "Setting up build directory"
BUILD_STARTED=true
rm_tree "$BUILD_DIR"
rm -f "$PROJECT_ROOT/build/$TAR_NAME"
mkdir -p "$BUILD_DIR"
write_build_marker

# Create libs directory (this will be a real directory, not a symlink)
mkdir -p "$BUILD_DIR/libs"

# Fetch dependencies into the project's libs/ (skipped when up to date)
fetch_libs

# Evaluate the .distignore exclusion file (exact .gitignore semantics) up front
step "Evaluating distignore rules"
setup_exclusions
report_conda_envs
report_exclusions
report_forced_rules
report_fetched_rules
exclude_cli_leftovers
exclude_nested_git

step "Copying tool files"
copy_plan > "$TMPWORK/copy-plan"
copy_planned
report_untracked
# cp -p keeps read-only directories from dependencies; the build must be able
# to add __init__.py files now and delete the tree on the next build.
find "$BUILD_DIR" -type d -exec chmod u+w {} +
for libentry in "$BUILD_DIR"/libs/*; do
    if [ -d "$libentry" ] && [ ! -L "$libentry" ]; then
        strip_vcs_and_caches "$libentry"
        ensure_init_files "$libentry"
    fi
done
check_required copy
# Before the hooks run, so no hook can write through a link to outside the build.
check_links

step "Updating version info"
# Use sed to replace the string with the environment variable. '&', '\' and
# the '/' delimiter are escaped, so the replacement is taken literally.
VERSION_SED=$(printf '%s' "$TOOL_VERSION" | sed 's|[&/\\]|\\&|g')
if [ -f "$BUILD_DIR/README" ]; then
    if [[ "$(uname)" == "Darwin" ]]; then
        # For MacOS
        sed -i "" "s/TOOL_VERSION/${VERSION_SED}/g" "$BUILD_DIR/README"
    else
        # For Linux
        sed -i "s/TOOL_VERSION/${VERSION_SED}/g" "$BUILD_DIR/README"
    fi
fi

# All dependencies should be in the libs directory
cd "$BUILD_DIR/libs"

show_progress "Running build hooks"
# Execute the project's build hook if present.
HOOK_SCRIPT=""
if [ -f "$SRC_DIR/hooks.sh" ]; then
    HOOK_SCRIPT="$SRC_DIR/hooks.sh"
fi

if [ -n "$HOOK_SCRIPT" ]; then
    log_verbose "Executing build hook: $HOOK_SCRIPT"

    # Set environment variables visible to the hook
    export SRC_DIR PROJECT_ROOT APP_NAME TOOL_NAME TOOL_VERSION TOOL_DIR BUILD_DIR

    # In progress mode the hook's output goes to a log, shown when it fails.
    # In the background, so a signal to the build stops it at once
    # (wait_script), and in a process group of its own (own_group); <&0
    # keeps its stdin, which '&' would replace.
    HOOK_RC=0
    clear_script_group
    if [ "$PROGRESS_MODE" = true ]; then
        mkdir -p "$STATE_DIR"
        own_group bash "$HOOK_SCRIPT" <&0 > "$HOOKS_LOG" 2>&1 8>&- 9>&- &
    else
        own_group bash "$HOOK_SCRIPT" <&0 8>&- 9>&- &
    fi
    SCRIPT_PID=$!
    HOOK_PID=$SCRIPT_PID
    wait_script || HOOK_RC=$?
    stop_left_jobs "$(script_group "$HOOK_PID")" scripts/hooks.sh
    clear_script_group
    if [ "$HOOK_RC" -ne 0 ]; then
        error "scripts/hooks.sh failed (exit $HOOK_RC)."
        if [ "$PROGRESS_MODE" = true ]; then
            echo "--- last lines of .ngargparser/hooks.log ---" >&2
            tail -n 20 "$HOOKS_LOG" >&2
        fi
        exit 1
    fi

    log_verbose "✓ Build hook completed"
else
    log_verbose "No scripts/hooks.sh; nothing to run."
fi

cd "$BUILD_DIR"

# Create version file
if [ -e VERSION ] || [ -L VERSION ]; then
    notice "Notice: the build writes VERSION; the tool's own VERSION file is replaced in the tarball."
fi
echo "${TOOL_VERSION}" > VERSION
date >> VERSION
write_build_marker

# Remove macOS resource fork files
find . -type f -name '._*' -delete

# Ensure all directories in libs/ have __init__.py files
log_verbose "Ensuring __init__.py files exist in all libs directories..."
ensure_init_files "$BUILD_DIR/libs"
log_verbose "✓ All __init__.py files ensured"

step "Checking symlinks"
check_links
check_required hooks

# Before the tarball, so a tool that cannot start never leaves one behind.
step "Checking the tool starts"
check_startup
check_required startup

# Create tarball in build directory. Symlinks are stored as links (no -h), and
# COPYFILE_DISABLE stops macOS tar from adding ._* members.
step "Creating tarball"
cd "$PROJECT_ROOT/build"
TAR_TMP="$PROJECT_ROOT/build/.$TAR_NAME.partial"
COPYFILE_DISABLE=1 tar -czf "$TAR_TMP" "$TOOL_DIR"
mv -f "$TAR_TMP" "$TAR_NAME"
TAR_TMP=""

# The build dir is output: fixes made here would be lost on the next build.
# Read-only files make an editor refuse the save instead. Directories stay
# writable, so `make clean`, the next build, and new files from ./configure work.
step "Making build files read-only"
find "$BUILD_DIR" -type f -exec chmod a-w {} +

if [ "$PROGRESS_MODE" = true ]; then
    flush_stderr
    echo ""
    echo "Build completed: build/$TAR_NAME"
else
    echo "Build completed!"
fi
