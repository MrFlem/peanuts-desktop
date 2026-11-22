#!/bin/sh
# install-hooks.sh : installs this clone's git hooks for the publishing rules (peanuts/scripts/check-publish.sh):
#   commit-msg  refuses a message with an AI trailer (Co-Authored-By / Generated with ... Claude, Codex, Copilot, ...)
#   pre-push    runs check-publish.sh on the commits each pushed ref publishes, and refuses the push when it fails
# Run once per clone (hooks are not versioned by git). Existing hooks of the same name are kept as <hook>.local and
# still run first.
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
# <remote sha>. Each one is checked on its own, so the gate never has to assume that HEAD, or a single ref, is what
# is being pushed:
#   * a fast-forward: the remote tip as the baseline, the pushed commit as the target - exactly the added commits;
#   * a new branch or a non-fast-forward push: no remote tip is a usable baseline, so the gate's own baseline (the
#     merge base with upstream) with the pushed commit as the target - the whole branch, not only its newest commit;
#   * a deleted ref (local sha all zeros): nothing is published, so nothing is checked.
# Every update is checked even after one fails, so one push reports all of its problems.
d=\$(dirname "\$0"); [ -x "\$d/pre-push.local" ] && { "\$d/pre-push.local" "\$@" || exit 1; }
top="$top"
gate="\$top/peanuts/scripts/check-publish.sh"
[ -f "\$gate" ] || { echo "pre-push: \$gate is missing: refusing to push without the publishing gate"; exit 1; }
zero=0000000000000000000000000000000000000000
status=0
checked=0
while read -r local_ref local_sha remote_ref remote_sha; do
    [ -n "\${local_sha:-}" ] || continue
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
