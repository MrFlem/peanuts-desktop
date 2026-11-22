#!/bin/sh
# check-publish.sh [<since-ref>] : the gate before anything of Peanuts Native is pushed to a public or shared remote (GitHub). Exit 0 = clean, 1 = fix first.
#
# Rule 1, no AI attribution: no commit in <since-ref>..HEAD may have an AI author or committer, a Co-Authored-By trailer naming an AI tool or its vendor,
#   or a "Generated with" line (Claude, Anthropic, Codex, OpenAI, ChatGPT, GPT, Copilot, Gemini, Cursor, Devin, ...). The person who submits a change is its
#   author; tools are not contributors.
# Rule 2, no private data: no file Peanuts changed since <since-ref> may contain a LAN address, a local home or mount path, an assistant's work folder,
#   an adb-over-network address or a personal e-mail. Vendored third-party code (dm/peanuts/third_party) keeps its own copyright lines. A test fixture that
#   has to look like private data is exempted one exact line at a time, never a file, a path prefix or a line number, by peanuts/scripts/publish-allowlist.txt
#   (<path><TAB><the line, byte for byte>): a private-looking line that is not listed there fails, and a listed line that is missing, edited or repeated fails.
#
# <since-ref> defaults to the merge base with upstream/android (Banana Native's history before it is upstream's, published as the single import commit of the
# public history, see peanuts/docs/CONTRIBUTING.md "Publishing"). A contributor's clone, whose upstream is Peanuts Native itself (its main branch, no android),
# takes the merge base with upstream/main: their own commits. peanuts/scripts/install-hooks.sh runs this before every push.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
if [ $# -gt 0 ]; then
	since=$1
else
	since=$(git merge-base HEAD upstream/android 2>/dev/null || git merge-base HEAD upstream/main 2>/dev/null)
fi
[ -n "$since" ] || { echo "check-publish: no <since-ref>, and neither upstream/android nor upstream/main to take the merge base from"; exit 1; }
# A baseline that does not resolve makes both rules below look at nothing and pass: refuse it instead of reporting clean.
git rev-parse --verify --quiet "$since^{commit}" >/dev/null || { echo "check-publish: $since is not a commit in this repository; pass the baseline the change is based on"; exit 1; }
fail=0
AI='claude|anthropic|codex|openai|chatgpt|gpt-[0-9]|copilot|gemini|cursor|devin|aider|windsurf|noreply@anthropic'

# Rule 1: authors, committers, trailers
bad=$(git log "$since"..HEAD --format='%h %an <%ae> | %cn <%ce>' | grep -iE "$AI")
[ -n "$bad" ] && { echo "AI author/committer:"; echo "$bad"; fail=1; }
bad=$(git log "$since"..HEAD --format='@@%h%n%B' | awk '/^@@/{h=substr($0,3); next} {print h": "$0}' | grep -iE "^[0-9a-f]+: *(co-authored-by|signed-off-by|generated[- ]with|assisted[- ]by|with[- ]the[- ]help[- ]of).*($AI)|🤖" )
[ -n "$bad" ] && { echo "AI trailer in a commit message:"; echo "$bad"; fail=1; }

# Rule 2: private data in the files Peanuts changed (their current contents)
PRIV='192\.168\.[0-9]+\.[0-9]+|(^|[^0-9.])10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+|/home/[a-z_][a-z0-9_-]*/|/Users/[A-Za-z]|[Cc]:\\Users\\|/mnt/(nvme|ironwolf|data|sd)|\.claude/(jobs|projects)|cc-socks|:5555\b|\.ts\.net|@gmail\.com|@yahoo\.|@outlook\.|@hotmail\.'
tab=$(printf '\t')
allow=peanuts/scripts/publish-allowlist.txt
tmp="${TMPDIR:-/tmp}/check-publish.$$"
trap 'rm -f "$tmp".*' 0 1 2 15
git diff --name-only --diff-filter=d "$since" HEAD -- . ':!dm/peanuts/third_party' ':!peanuts/scripts/check-publish.sh' ':!peanuts/scripts/install-hooks.sh' ':!dm/tools/tests/test_log_digest.py' ':!peanuts/scripts/publish-allowlist.txt' > "$tmp.files"

# Every private-looking line in those files, as <path><TAB><line>.
: > "$tmp.found"
while IFS= read -r f; do
	[ -f "$f" ] || continue
	grep -nIE "$PRIV" "$f" 2>/dev/null | grep -vE '127\.0\.0\.1|0\.0\.0\.0' | while IFS= read -r hit; do
		printf '%s\t%s\n' "$f" "${hit#*:}" >> "$tmp.found"
	done
done < "$tmp.files"

# The approved ones, from the allowlist: its own entries, for files this change touches.
: > "$tmp.ok"
if [ -f "$allow" ]; then
	while IFS="$tab" read -r p line; do
		case $p in '' | '#'*) continue ;; esac
		case $p in
			peanuts/tests/*) ;;
			*) echo "publish-allowlist: $p is not under peanuts/tests/ (only a required test fixture may be listed)"; fail=1; continue ;;
		esac
		[ -n "$line" ] || { echo "publish-allowlist: the entry for $p has no line after its tab"; fail=1; continue; }
		grep -Fxq -- "$p" "$tmp.files" || continue   # its file is not part of this change, so the entry is not needed
		printf '%s\t%s\n' "$p" "$line" >> "$tmp.ok"
	done < "$allow"
fi

# Found minus approved is publication-blocking; approved minus found is an entry that no longer matches its line.
if [ -s "$tmp.found" ] || [ -s "$tmp.ok" ]; then
	LC_ALL=C sort "$tmp.found" > "$tmp.found.s"
	LC_ALL=C sort "$tmp.ok" > "$tmp.ok.s"
	unapproved=$(LC_ALL=C comm -23 "$tmp.found.s" "$tmp.ok.s")
	stale=$(LC_ALL=C comm -13 "$tmp.found.s" "$tmp.ok.s")
	if [ -n "$unapproved" ]; then
		echo "private data in changed files (replace with a placeholder such as <work dir> or <device ip>):"
		printf '%s\n' "$unapproved" | while IFS="$tab" read -r p line; do
			grep -nF -- "$line" "$p" 2>/dev/null | head -3 | sed "s|^|$p:|"
		done | cut -c1-200
		fail=1
	fi
	if [ -n "$stale" ]; then
		echo "stale publish-allowlist entry (the listed line is gone, edited or repeated: update $allow to the line as it now reads):"
		printf '%s\n' "$stale" | cut -c1-200
		fail=1
	fi
fi

[ $fail = 0 ] && echo "check-publish: clean ($since..HEAD: no AI attribution, no private data in changed files)"
exit $fail
