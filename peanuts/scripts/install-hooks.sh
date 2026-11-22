#!/bin/sh
# install-hooks.sh : installs this clone's git hooks for the publishing rules (peanuts/scripts/check-publish.sh):
#   commit-msg  refuses a message with an AI trailer (Co-Authored-By / Generated with ... Claude, Codex, Copilot, ...)
#   pre-push    reads this push's ref updates once, replays them to <hook>.local, then runs check-publish.sh on
#               the commits each pushed ref publishes and refuses the push when it fails
# Run once per clone (hooks are not versioned by git). Existing hooks of the same name are kept as <hook>.local and
# still run first; the pre-push one is replayed the captured records, so one that consumes standard input cannot
# starve the gate.
set -eu
top=$(git rev-parse --show-toplevel)
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
for h in commit-msg pre-push; do
    [ -f "$hooks/$h" ] && ! grep -q 'peanuts publishing rules' "$hooks/$h" && mv "$hooks/$h" "$hooks/$h.local"
done
cat > "$hooks/commit-msg" <<'EOF'
#!/bin/sh
# peanuts publishing rules: no AI attribution in commit messages (peanuts/scripts/check-publish.sh)
d=$(dirname "$0"); [ -x "$d/commit-msg.local" ] && { "$d/commit-msg.local" "$@" || exit 1; }
if grep -iqE '^ *(co-authored-by|signed-off-by|generated[- ]with|assisted[- ]by).*(claude|anthropic|codex|openai|chatgpt|gpt-[0-9]|copilot|gemini|cursor|devin|aider|windsurf)|🤖' "$1"; then
    echo "commit-msg: remove the AI trailer from the message (peanuts publishing rules)"; exit 1
fi
EOF
cat > "$hooks/pre-push" <<EOF
#!/bin/sh
# peanuts publishing rules: check-publish.sh on the commits this push publishes (peanuts/scripts/install-hooks.sh)
#
# git gives a pre-push hook one line per ref update on standard input: <local ref> <local sha> <remote ref>
# <remote sha>. That input is read ONCE, into a temporary file, before anything else runs:
#   * an existing pre-push.local hook is replayed the same records from that file, so a hook that consumes all of
#     standard input cannot leave this gate with nothing to check;
#   * this gate's own loop reads the file too, so both hooks see every update, whole.
# Each record is then checked on its own, so the gate never has to assume that HEAD, or a single ref, is what is
# being pushed:
#   * a fast-forward: the remote tip as the baseline, the pushed commit as the target - exactly the added commits;
#   * a new branch or a non-fast-forward push: no remote tip is a usable baseline, so the gate's own baseline (the
#     merge base with upstream) with the pushed commit as the target - the whole branch, not only its newest commit;
#   * a deleted ref (local sha all zeros): nothing is published, so nothing is checked.
# A record that is empty, is not four fields, does not name two commits, or is not terminated by a newline refuses
# the push: an input this hook cannot read whole is never treated as a shorter one, and a final record that `read`
# used to drop is refused rather than skipped.
# Every update is checked even after one fails, so one push reports all of its problems. A push whose records
# cannot be captured, read or replayed is refused: an unknown set of updates is never treated as an empty one.
set -u
d=\$(dirname "\$0")
tmp=\$(mktemp "\${TMPDIR:-/tmp}/peanuts-pre-push.XXXXXX") \\
    || { echo "pre-push: cannot make a temporary file for this push's ref updates: refusing to push"; exit 1; }
trap 'rm -f "\$tmp"' 0 1 2 15
cat > "\$tmp" \\
    || { echo 'pre-push: cannot read the ref updates this push would publish: refusing to push'; exit 1; }
[ -s "\$tmp" ] \\
    || { echo 'pre-push: this push gave no ref updates to check: refusing to push'; exit 1; }
if [ -x "\$d/pre-push.local" ]; then
    "\$d/pre-push.local" "\$@" < "\$tmp" || { echo 'pre-push: pre-push.local refused this push'; exit 1; }
fi
top="$top"
gate="\$top/peanuts/scripts/check-publish.sh"
[ -f "\$gate" ] || { echo "pre-push: \$gate is missing: refusing to push without the publishing gate"; exit 1; }
[ -r "\$tmp" ] || { echo 'pre-push: the ref updates could not be read back: refusing to push'; exit 1; }
exec 3< "\$tmp"   # read them with a descriptor of their own, so nothing else can consume them
zero=0000000000000000000000000000000000000000
status=0
checked=0
while :; do
    # One record. A last record with no newline is not one this hook can read whole: refuse it, never skip it.
    if IFS= read -r record <&3; then
        terminated=1
    else
        [ -n "\$record" ] || break
        terminated=0
    fi
    if [ -z "\$record" ]; then
        echo 'pre-push: a ref update is an empty record: refusing to push'
        status=1
        continue
    fi
    # Four fields, as git documents; anything else is a record this hook cannot vouch for.
    set -- \$record
    if [ \$# -ne 4 ]; then
        echo 'pre-push: a ref update is not four fields: refusing to push'
        status=1
        continue
    fi
    local_sha=\$2
    remote_sha=\$4
    case \$local_sha in
        '' | *[!0-9a-f]*) echo 'pre-push: a ref update does not name a commit: refusing to push'; status=1; continue ;;
    esac
    [ \${#local_sha} -ge 40 ] \\
        || { echo 'pre-push: a ref update does not name a commit: refusing to push'; status=1; continue; }
    case \$remote_sha in
        '' | *[!0-9a-f]*) echo 'pre-push: a ref update does not name a remote commit: refusing to push'; status=1; continue ;;
    esac
    [ \${#remote_sha} -ge 40 ] \\
        || { echo 'pre-push: a ref update does not name a remote commit: refusing to push'; status=1; continue; }
    if [ "\$terminated" = 0 ]; then
        echo 'pre-push: the last ref update is not terminated by a newline: refusing to push'
        status=1
    fi
    [ "\$local_sha" = "\$zero" ] && continue
    checked=1
    if [ "\$remote_sha" != "\$zero" ] && git merge-base --is-ancestor "\$remote_sha" "\$local_sha" 2>/dev/null; then
        sh "\$gate" --since "\$remote_sha" --target "\$local_sha" || status=1
    else
        sh "\$gate" --target "\$local_sha" || status=1
    fi
done
[ "\$checked" = 1 ] || echo "pre-push: this push only deletes refs, so it publishes nothing to check"
exit \$status
EOF
chmod +x "$hooks/commit-msg" "$hooks/pre-push"
echo "installed commit-msg and pre-push in $hooks"
