#!/bin/sh
# check-publish.sh [--since <ref>] [--target <ref>] [<since-ref>] : the gate before anything of Peanuts Native is
# pushed to a public or shared remote (GitHub). Exit 0 = clean, 1 = fix first (or the gate could not finish: a
# failed git command, an object it cannot read and a broken allowlist entry all fail it, never pass it).
#
# WHAT IS SCANNED, AND WHY IT IS NOT THE WORKING TREE
#   The commits in <since-ref>..<target-ref>. For each of them, the files that commit changed, read from the object
#   store as they were in that commit. Nothing is read from the working tree, so a local edit that is not committed
#   changes nothing here, and a value that one commit added and a later commit removed is still found: it is in the
#   history being published, which is what a reader of the remote can check out.
#
#   <since-ref> defaults to the merge base with upstream/android (Banana Native's history before it is upstream's,
#   published as the single import commit of the public history, see peanuts/docs/CONTRIBUTING.md "Publishing"). A
#   contributor's clone, whose upstream is Peanuts Native itself (its main branch, no android), takes the merge base
#   with upstream/main: their own commits. <target-ref> defaults to HEAD.
#
# THE PRE-PUSH HOOK, AND THE RANGE IT ASKS FOR
#   peanuts/scripts/install-hooks.sh installs the pre-push hook. That hook does not use HEAD: git gives it one line
#   per ref update (<local ref> <local sha> <remote ref> <remote sha>), and for each ref it is about to publish:
#     * a fast-forward (the remote tip is an ancestor of the pushed commit): --since <remote sha> --target <pushed
#       sha> - exactly the commits the push adds to that branch, and nothing else;
#     * a new branch, or a non-fast-forward push, where no remote tip is a usable baseline: --target <pushed sha>
#       with the default baseline above, so the whole branch is checked, not only its newest commit;
#     * a deleted ref: nothing is published, so nothing is checked.
#   Several refs in one push are all checked; the push is refused if any of them fails. A manual run without
#   --target checks HEAD, which is only right when HEAD is what you are pushing.
#
# RULE 1, NO AI ATTRIBUTION
#   No commit in the range may have an AI author or committer, a Co-Authored-By / Signed-off-by / Assisted-by
#   trailer naming an AI tool or its vendor, or a "Generated with" line (Claude, Anthropic, Codex, OpenAI, ChatGPT,
#   GPT, Copilot, Gemini, Cursor, Devin, ...). The person who submits a change is its author; tools are not
#   contributors.
#
# RULE 2, NO PRIVATE DATA
#   No file the range changes may contain a LAN address, a local home or mount path, an assistant's work folder, an
#   adb-over-network address or a personal e-mail, and no high-confidence credential shape (a private key header,
#   an AWS access key id, a GitHub/Google/Slack token, a Stripe live key). Vendored third-party code
#   (dm/peanuts/third_party) keeps its own copyright lines.
#
#   A test fixture that has to look like private data is exempted one exact line at a time, never a file, a path
#   prefix or a line number, by peanuts/scripts/publish-allowlist.txt (<path><TAB><the line, byte for byte>): a
#   private-looking line that is not listed there fails, and a listed line that is missing from the file at
#   <target-ref>, edited, deleted with its fixture, or repeated (in the file or as a second allowlist entry) fails.
#
#   The report names a file, a line and a reason code, never the value: this output goes to terminals, logs and
#   handoff reports. File paths are repository-relative, so they name nothing outside the repository.
#
# WHAT THIS CANNOT DO
#   A regular-expression scan cannot show that a change holds no secret. It finds the shapes listed in the reason
#   table below, in text; a binary file is skipped (grep -I), and a value in an unusual shape, a split across lines,
#   or an encoded one is not found. A credential that is a plain number, a short word or a hash is not found either:
#   matching those would fail on title IDs, build IDs and versions, which a bug report needs. Review the diff as
#   well; this gate is the last check, not the only one.
set -u
umask 077
LC_ALL=C
export LC_ALL

die() { echo "check-publish: $*"; exit 1; }

cd "$(git rev-parse --show-toplevel)" || die 'not in a git work tree'

tab=$(printf '\t')

# --- the range ----------------------------------------------------------------------------------
since=''
target=''
while [ $# -gt 0 ]; do
	case $1 in
		--since) [ $# -ge 2 ] || die '--since needs a ref'; since=$2; shift 2 ;;
		--target) [ $# -ge 2 ] || die '--target needs a ref'; target=$2; shift 2 ;;
		--) shift; break ;;
		-*) die "unknown option $1 (usage: check-publish.sh [--since <ref>] [--target <ref>] [<since-ref>])" ;;
		*) [ -z "$since" ] && { since=$1; shift; } || die "one baseline, not two ($since and $1)" ;;
	esac
done
[ $# -eq 0 ] || die "unexpected argument: $1"
target=${target:-HEAD}
git rev-parse --verify --quiet "$target^{commit}" >/dev/null \
	|| die "$target is not a commit in this repository; pass the commit whose files are being published"
if [ -z "$since" ]; then
	since=$(git merge-base "$target" upstream/android 2>/dev/null || git merge-base "$target" upstream/main 2>/dev/null)
fi
[ -n "$since" ] || die 'no <since-ref>, and neither upstream/android nor upstream/main to take the merge base from'
# A baseline that does not resolve makes both rules below look at nothing: refuse it, never report clean.
git rev-parse --verify --quiet "$since^{commit}" >/dev/null \
	|| die "$since is not a commit in this repository; pass the baseline the change is based on"
range="$since..$target"
revs=$(git rev-list "$range") \
	|| die "git rev-list $range failed: the gate cannot list the commits that would be published"

fail=0
AI='claude|anthropic|codex|openai|chatgpt|gpt-[0-9]|copilot|gemini|cursor|devin|aider|windsurf|noreply@anthropic'

# --- rule 1: authors, committers, trailers ------------------------------------------------------
# The report names the commit, not the author line: an address in a CI log is the same private data rule 2 is about.
log=$(git log --format='%h %an <%ae> | %cn <%ce>' "$range") \
	|| die "git log $range failed: the gate cannot read the commits that would be published"
bad=$(printf '%s\n' "$log" | grep -iE -- "$AI")
case $? in
	0) echo 'AI author/committer (the person who submits a change is its author and committer; see peanuts/docs/AGENTS.md):'
	   printf '%s\n' "$bad" | cut -d' ' -f1
	   fail=1 ;;
	1) ;;
	*) die 'grep failed on the commit authors: the gate cannot finish' ;;
esac
bad=$(git log --format='@@%h%n%B' "$range" \
	| awk '/^@@/{h=substr($0,3); next} {print h": "$0}' \
	| grep -iE -- "^[0-9a-f]+: *(co-authored-by|signed-off-by|generated[- ]with|assisted[- ]by|with[- ]the[- ]help[- ]of).*($AI)|🤖")
case $? in
	0) echo 'AI trailer in a commit message (remove the trailer; the report names the commit only):'
	   printf '%s\n' "$bad" | cut -d: -f1
	   fail=1 ;;
	1) ;;
	*) die 'grep failed on the commit messages: the gate cannot finish' ;;
esac

# --- rule 2: the reason table -------------------------------------------------------------------
# <code><TAB><extended regular expression>. Detection and the reason in the report come from this one table, so a
# found line always has a code. Keep the shapes high-confidence: a pattern that also matches ordinary code makes
# the gate fail on honest changes, which is how a security check gets switched off.
reasons_table=$(cat <<'REASONS'
lan-ip	192\.168\.[0-9]+\.[0-9]+|(^|[^0-9.])10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+
home-path	/home/[a-z_][a-z0-9_-]*/
macos-home	/Users/[A-Za-z]
windows-home	[Cc]:\\Users\\
mount-path	/mnt/(nvme|ironwolf|data|sd)
assistant-workdir	\.claude/(jobs|projects)|cc-socks
adb-over-network	:5555\b
tailscale-name	\.ts\.net
personal-email	@gmail\.com|@yahoo\.|@outlook\.|@hotmail\.
private-key	-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----
aws-access-key	AKIA[0-9A-Z]{16}
github-token	gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}
google-api-key	AIza[0-9A-Za-z_-]{35}
slack-token	xox[abprs]-[0-9A-Za-z-]{10,}
stripe-live-key	sk_live_[0-9A-Za-z]{16,}
REASONS
) || die 'cannot read the reason table'
priv=$(printf '%s\n' "$reasons_table" | while IFS="$tab" read -r code pattern; do
	[ -n "${code:-}" ] || continue
	printf '%s|' "$pattern"
done) || die 'cannot build the private-data patterns'
PRIV=${priv%|}
[ -n "$PRIV" ] || die 'the private-data patterns are empty: refusing to report a clean tree'

# The codes one line matches, in table order, as "a,b,c". A malformed pattern is the gate's own error, not a pass.
reason_codes() {
	while IFS="$tab" read -r code pattern; do
		[ -n "${code:-}" ] || continue
		printf '%s\n' "$1" | grep -qE -- "$pattern"
		case $? in
			0) printf '%s,' "$code" ;;
			1) ;;
			*) die "the reason pattern for $code is not a valid grep pattern" ;;
		esac
	done <<REASONS
$reasons_table
REASONS
}

# A loopback host's adb port is not a network address. The old gate dropped every line that mentioned 127.0.0.1 or
# 0.0.0.0, which hid a real address beside it on the same line; this removes exactly the host:port tokens instead.
strip_loopback() {
	printf '%s\n' "$1" | sed -E 's#(^|[^0-9A-Za-z.-])(localhost|127\.0\.0\.1|0\.0\.0\.0):5555#\1<loopback>#g'
}

# --- the files each commit changed --------------------------------------------------------------
# Deletions are listed apart: an allowlist entry whose fixture a commit deletes is dead, and must not pass quietly.
changed_files() {   # <commit> <diff-filter> -> paths, one per line
	git diff-tree -r -m --root --no-commit-id --name-only --diff-filter="$2" "$1" -- . \
		':(exclude)dm/peanuts/third_party' \
		':(exclude)peanuts/scripts/check-publish.sh' \
		':(exclude)peanuts/scripts/install-hooks.sh' \
		':(exclude)peanuts/scripts/publish-allowlist.txt' \
		':(exclude)dm/tools/tests/test_log_digest.py'
}

allow=peanuts/scripts/publish-allowlist.txt
tmp="${TMPDIR:-/tmp}/check-publish.$$"
trap 'rm -f "$tmp".*' 0 1 2 15
: > "$tmp.files"; : > "$tmp.gone"; : > "$tmp.hits"; : > "$tmp.seen"; : > "$tmp.ok"; : > "$tmp.entries"; : > "$tmp.stale"

for c in $revs; do
	changed_files "$c" 'ACMRT' > "$tmp.changed" \
		|| die "git diff-tree $c failed: the gate cannot list what that commit changes"
	LC_ALL=C sort -u "$tmp.changed" > "$tmp.changed.u" || die "sort failed on $c's changed files"
	while IFS= read -r f; do
		[ -n "$f" ] || continue
		printf '%s\n' "$f" >> "$tmp.files"
	done < "$tmp.changed.u"
	changed_files "$c" 'D' > "$tmp.deleted" \
		|| die "git diff-tree $c failed: the gate cannot list what that commit deletes"
	LC_ALL=C sort -u "$tmp.deleted" > "$tmp.deleted.u" || die "sort failed on $c's deleted files"
	while IFS= read -r f; do
		[ -n "$f" ] || continue
		printf '%s\n' "$f" >> "$tmp.gone"
	done < "$tmp.deleted.u"
done

# --- the allowlist: byte for byte, one exact line, one occurrence --------------------------------
# Parsed by hand rather than with `read <path><TAB><line>`: IFS whitespace would strip a fixture line's leading or
# trailing spaces, and a final entry without a newline would be lost. A CR, a second tab or a duplicate entry is a
# broken promise and fails the gate.
if [ -f "$allow" ]; then
	n=0
	while IFS= read -r entry || [ -n "$entry" ]; do
		n=$((n + 1))
		case $entry in '' | '#'*) continue ;; esac
		case $entry in
			*"$(printf '\r')"*) echo "$allow: entry $n has a CR: the file must use LF line endings"; fail=1; continue ;;
		esac
		rest=${entry#*"$tab"}
		if [ "$rest" = "$entry" ]; then
			echo "$allow: entry $n has no tab after its path (the form is <path><TAB><the line, byte for byte>)"
			fail=1; continue
		fi
		p=${entry%%"$tab"*}
		line=$rest
		case $line in
			*"$tab"*) echo "$allow: entry $n has more than one tab: a fixture line that contains a tab cannot be listed"
			          fail=1; continue ;;
			'') echo "$allow: entry $n has no line after its tab"; fail=1; continue ;;
		esac
		case $p in
			peanuts/tests/*) ;;
			*) echo "$allow: entry $n lists $p, which is not under peanuts/tests/ (only a required test fixture may be listed)"
			   fail=1; continue ;;
		esac
		if grep -Fxq -- "$entry" "$tmp.entries"; then
			echo "$allow: entry $n repeats an earlier entry for $p: one entry per fixture line"; fail=1; continue
		fi
		printf '%s\n' "$entry" >> "$tmp.entries"
		# Only a file these commits touch needs its entry; an entry for an untouched file is not exercised here.
		if grep -Fxq -- "$p" "$tmp.files" || grep -Fxq -- "$p" "$tmp.gone"; then
			printf '%s\n' "$entry" >> "$tmp.ok"
		fi
	done < "$allow"
fi

# --- the scan: every changed file, at every commit that changed it ------------------------------
for c in $revs; do
	changed_files "$c" 'ACMRT' > "$tmp.changed" \
		|| die "git diff-tree $c failed: the gate cannot list what that commit changes"
	LC_ALL=C sort -u "$tmp.changed" > "$tmp.changed.u" || die "sort failed on $c's changed files"
	while IFS= read -r f; do
		[ -n "$f" ] || continue
		git cat-file blob "$c:$f" > "$tmp.blob" 2>/dev/null \
			|| die "cannot read $f at commit $c: its object is missing, so the gate cannot check it"
		: > "$tmp.seen"
		grep -nIE -- "$PRIV" "$tmp.blob" > "$tmp.grep"
		case $? in
			0 | 1) ;;
			*) die "grep failed on $f at commit $c: the gate cannot finish" ;;
		esac
		while IFS= read -r hit; do
			[ -n "$hit" ] || continue
			lineno=${hit%%:*}
			text=${hit#*:}
			stripped=$(strip_loopback "$text") || die "sed failed on $f at commit $c"
			printf '%s\n' "$stripped" | grep -qE -- "$PRIV"
			case $? in
				0) ;;
				1) continue ;;   # only a loopback host's adb port on this line
				*) die "grep failed on $f at commit $c: the gate cannot finish" ;;
			esac
			codes=$(reason_codes "$stripped"); codes=${codes%,}
			[ -n "$codes" ] || codes=private
			pair=$(printf '%s\t%s' "$f" "$text")
			if grep -Fxq -- "$pair" "$tmp.ok"; then
				# One entry exempts one occurrence: a second identical line in the same version is not covered.
				if grep -Fxq -- "$pair" "$tmp.seen"; then
					printf '%s\t%s\t%s\t%s\n' "$c" "$f" "$lineno" "repeated,$codes" >> "$tmp.hits"
					fail=1
				else
					printf '%s\n' "$pair" >> "$tmp.seen"
				fi
			else
				printf '%s\t%s\t%s\t%s\n' "$c" "$f" "$lineno" "$codes" >> "$tmp.hits"
				fail=1
			fi
		done < "$tmp.grep"
	done < "$tmp.changed.u"
done

# --- what is wrong, by file, line and reason (never the value) ----------------------------------
if [ -s "$tmp.hits" ]; then
	echo "private data in changed files (replace the value with a placeholder such as <work dir>, <device ip> or <your dump folder>; $allow exempts one exact fixture line only):"
	while IFS="$tab" read -r c f lineno codes; do
		printf '%s: %s:%s: %s\n' "$c" "$f" "$lineno" "$codes"
	done < "$tmp.hits"
fi

# Each remaining entry must still describe exactly one line of its file as published at <target-ref>.
if [ -s "$tmp.ok" ]; then
	while IFS= read -r entry; do
		p=${entry%%"$tab"*}
		line=${entry#*"$tab"}
		if ! git cat-file -e "$target:$p" 2>/dev/null; then
			if ! grep -Fxq -- "$p" "$tmp.stale"; then
				echo "stale publish-allowlist entry: $p is deleted by these commits, so its entries are dead: remove them with the fixture"
				printf '%s\n' "$p" >> "$tmp.stale"
			fi
			fail=1; continue
		fi
		git cat-file blob "$target:$p" > "$tmp.blob" 2>/dev/null \
			|| die "cannot read $p at $target: its object is missing, so the gate cannot check its entry"
		count=$(grep -cxF -- "$line" "$tmp.blob")
		case $? in
			0 | 1) ;;
			*) die "grep failed on $p at $target: the gate cannot finish" ;;
		esac
		case $count in
			1) ;;
			0) echo "stale publish-allowlist entry: the listed line is gone from $p or has been edited: update $allow to the line as it now reads"
			   fail=1 ;;
			*) echo "stale publish-allowlist entry: the listed line appears $count times in $p: one entry exempts one line"
			   fail=1 ;;
		esac
	done < "$tmp.ok"
fi

[ $fail = 0 ] && echo "check-publish: clean ($range: no AI attribution, no private data in the files those commits changed)"
exit $fail
