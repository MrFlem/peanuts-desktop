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
#   The inspection is four commands, each checked on its own (git log for the authors, git log for the messages,
#   awk for the trailer lines, grep for the pattern): a failure in any of them fails the gate. It is not a pipeline,
#   because POSIX sh has no pipefail and a pipeline reports only its last command - which used to turn a broken
#   git log or awk into "no trailer found" and a clean verdict.
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
# THE ALLOWLIST IS READ FROM <target-ref>, NEVER FROM THE WORKING TREE
#   The exemptions are the ones published at the target commit, read with git cat-file. An uncommitted edit to the
#   file therefore cannot change the verdict for the same --since/--target pair, in either direction: a local entry
#   cannot exempt a line that was published without it, and a local deletion cannot make a published exemption
#   fail. A tree whose target has no allowlist has no exemptions; an object the gate cannot read fails it.
#
#   The policy for the commits in between, which is what a multi-commit range is:
#     * a new fixture and its entry may be committed in any order, so the entry is taken from the target for the
#       whole range; when it was not yet present at the commit whose version of the file holds the line, the gate
#       prints that ("added later in this range") instead of passing it silently;
#     * a line only exempts the versions whose text is byte-identical to the listed line. A commit that added a real
#       value and a later commit that edited it is still a hit: editing the line breaks the match, so a later
#       exception cannot launder an earlier, different value;
#     * a fixture deleted anywhere makes its entries stale, because each entry must still match exactly one line of,
#       its file at the target; a file deleted with its fixture is reported the same way;
#     * an entry removed by a later commit while its line remains is caught by the scan of that later version: the
#       line is no longer exempt there, so it is reported; a file no commit in this range touches is not read by
#       that scan, so it is re-read at the target when an entry this range ever published is gone from it: a
#       revocation cannot leave the content it used to cover exempt;
#     * the entries a range ever published are gathered from each commit's allowlist one blob at a time, never by
#       appending the blobs to each other: a blob is a whole file, and its last record may have no newline, which
#       concatenation would run into the next blob's first record - a comment at the end of one blob would then
#       swallow the entry that opens the next, and a revoked exemption would never be re-checked;
#     * one entry exempts one occurrence in each version, so a second copy of a listed line fails;
#     * every entry of the target's allowlist has to describe exactly one line of its file at the target, whether
#       or not this range touches that file, so an entry the file no longer keeps is stale rather than dormant.
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
tmp="${TMPDIR:-/tmp}/check-publish.$$"
trap 'rm -f "$tmp".*' 0 1 2 15

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
#
# Each step writes a file and is checked before the next one reads it. A pipeline would report only its last
# command's status, and POSIX sh has no pipefail: a git log that fails part-way, or an awk that does, used to leave
# grep with nothing to find, and "nothing to find" is what a clean range looks like. Every command here fails the
# gate on its own, whatever the other ones did.
AI_TRAILER="^[0-9a-f]+: *(co-authored-by|signed-off-by|generated[- ]with|assisted[- ]by|with[- ]the[- ]help[- ]of).*($AI)|🤖"
git log --format='%h %an <%ae> | %cn <%ce>' "$range" > "$tmp.authors" \
	|| die "git log $range failed: the gate cannot read the commit authors"
bad=$(grep -iE -- "$AI" "$tmp.authors")
case $? in
	0) echo 'AI author/committer (the person who submits a change is its author and committer; see peanuts/docs/AGENTS.md):'
	   printf '%s\n' "$bad" | cut -d' ' -f1
	   fail=1 ;;
	1) ;;
	*) die 'grep failed on the commit authors: the gate cannot finish' ;;
esac
git log --format='@@%h%n%B' "$range" > "$tmp.messages" \
	|| die "git log $range failed: the gate cannot read the commit messages"
awk '/^@@/{h=substr($0,3); next} {print h": "$0}' "$tmp.messages" > "$tmp.trailers" \
	|| die 'awk failed on the commit messages: the gate cannot finish'
bad=$(grep -iE -- "$AI_TRAILER" "$tmp.trailers")
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
: > "$tmp.files"; : > "$tmp.gone"; : > "$tmp.hits"; : > "$tmp.seen"; : > "$tmp.ok"; : > "$tmp.entries"
: > "$tmp.stale"; : > "$tmp.later"; : > "$tmp.allows"; : > "$tmp.everallow"; : > "$tmp.rescanned"

# The exemptions are the ones published at <target-ref>, read from the object store, never the working tree: a
# local edit to the allowlist must not change the verdict for the same --since/--target pair, and an exemption
# must be published to count. A tree without the file has no exemptions; an object the gate cannot read is a
# failure, because "no entries" and "could not look" must never be the same answer.
# blob_at <ref> <path> <output file>: 0 when the file's bytes at <ref> were written to <output file>, 1 when the
# path is not in that tree (there is nothing there, and nothing to check). It never returns otherwise: a git that
# cannot inspect the tree, a path that is not a regular file and an object that cannot be read all fail the gate,
# because "could not look" must not answer like "nothing there".
blob_at() {
	if ! found=$(git ls-tree "$1" -- "$2" 2>&1); then
		die "cannot look up $2 at $1: git could not inspect that tree, so the gate cannot check it"
	fi
	[ -n "$found" ] || return 1
	case $found in
		*" blob "*) ;;
		*) die "$2 at $1 is not a regular file: the gate cannot read it" ;;
	esac
	git cat-file blob "$1:$2" > "$3" 2>/dev/null \
		|| die "cannot read $2 at $1: its object is missing, so the gate cannot check it"
}

allowlist_at() {   # <ref> <output file>: the allowlist as published at that commit
	# The output goes to the file named here, never through the function's stdout: a die() inside a redirected
	# function would put its own message in that file and leave the terminal with nothing.
	if blob_at "$1" "$allow" "$2"; then
		return 0
	fi
	: > "$2"   # no allowlist in that commit: no exemptions
}
# allowlist_records <allowlist file> <output file>: the records of one blob, one per line, whatever its last byte is.
# A blob is a whole file, and its final record may have no newline; appending blobs to each other would run that
# record into the next blob's first one, so each is read on its own here and written out terminated. The records
# themselves are not normalized - an entry has to reach its comparison byte for byte - and blank lines are dropped.
allowlist_records() {
	[ -s "$1" ] || return 0
	while IFS= read -r ar_record || [ -n "$ar_record" ]; do
		[ -n "$ar_record" ] || continue
		printf '%s\n' "$ar_record" >> "$2" || die "cannot record the allowlist entries of $1"
	done < "$1"
}
allowlist_at "$target" "$tmp.allow" || exit 1
allowlist_at "$since" "$tmp.allows" || exit 1
allowlist_records "$tmp.allows" "$tmp.everallow"

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
if [ -s "$tmp.allow" ]; then
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
		# Every entry is checked against its file at <target-ref>, whether or not this range touches that file:
		# an entry the file no longer keeps is stale, and leaving it dormant is how a revoked one stayed silent.
		printf '%s\n' "$entry" >> "$tmp.ok"
	done < "$tmp.allow"
fi

# --- the scan: every changed file, at every commit that changed it ------------------------------
# scan_file <commit> <path> <blob file> <allowlist file> <record-later 0|1>: one version of one file, checked
# against that commit's exemptions. Every private-looking line the exemptions do not cover is a hit, and one entry
# exempts one occurrence, so a second identical line in the same version is a hit as well. The report names the
# commit, the file, the line and the reasons, never the value it found.
scan_file() {
	sf_c=$1
	sf_f=$2
	sf_blob=$3
	sf_allowc=$4
	sf_later=$5
	: > "$tmp.seen"
	grep -nIE -- "$PRIV" "$sf_blob" > "$tmp.grep"
	case $? in
		0 | 1) ;;
		*) die "grep failed on $sf_f at commit $sf_c: the gate cannot finish" ;;
	esac
	while IFS= read -r hit; do
		[ -n "$hit" ] || continue
		lineno=${hit%%:*}
		text=${hit#*:}
		stripped=$(strip_loopback "$text") || die "sed failed on $sf_f at commit $sf_c"
		printf '%s\n' "$stripped" | grep -qE -- "$PRIV"
		case $? in
			0) ;;
			1) continue ;;   # only a loopback host's adb port on this line
			*) die "grep failed on $sf_f at commit $sf_c: the gate cannot finish" ;;
		esac
		codes=$(reason_codes "$stripped"); codes=${codes%,}
		[ -n "$codes" ] || codes=private
		pair=$(printf '%s\t%s' "$sf_f" "$text")
		if grep -Fxq -- "$pair" "$tmp.ok"; then
			# One entry exempts one occurrence: a second identical line in the same version is not covered.
			if grep -Fxq -- "$pair" "$tmp.seen"; then
				printf '%s\t%s\t%s\t%s\n' "$sf_c" "$sf_f" "$lineno" "repeated,$codes" >> "$tmp.hits"
				fail=1
			else
				printf '%s\n' "$pair" >> "$tmp.seen"
				# The entry is published at <target-ref>; was it already there at this commit? If not, it was added later
				# in the range. That still covers this exact line, but it is reported rather than silent: an entry that
				# only appears after the line it exempts is a promise made afterwards.
				if [ "$sf_later" = 1 ] && ! grep -Fxq -- "$pair" "$sf_allowc"; then
					printf '%s\t%s\t%s\n' "$sf_c" "$sf_f" "$lineno" >> "$tmp.later"
				fi
			fi
		else
			printf '%s\t%s\t%s\t%s\n' "$sf_c" "$sf_f" "$lineno" "$codes" >> "$tmp.hits"
			fail=1
		fi
	done < "$tmp.grep"
}

for c in $revs; do
	changed_files "$c" 'ACMRT' > "$tmp.changed" \
		|| die "git diff-tree $c failed: the gate cannot list what that commit changes"
	allowlist_at "$c" "$tmp.allowc" || exit 1
	allowlist_records "$tmp.allowc" "$tmp.everallow"
	LC_ALL=C sort -u "$tmp.changed" > "$tmp.changed.u" || die "sort failed on $c's changed files"
	while IFS= read -r f; do
		[ -n "$f" ] || continue
		git cat-file blob "$c:$f" > "$tmp.blob" 2>/dev/null \
			|| die "cannot read $f at commit $c: its object is missing, so the gate cannot check it"
		scan_file "$c" "$f" "$tmp.blob" "$tmp.allowc" 1
	done < "$tmp.changed.u"
done

# --- an exemption this range revokes re-evaluates the content it used to cover -------------------
# The loop above reads only the files these commits change. A fixture committed before the range is not read again,
# so a private-looking line in it is covered by whatever entry the target publishes - and an entry this range
# removes would otherwise leave that line published without an exemption at all, which no commit here would show.
# Every entry this range ever published (at the baseline and at each commit in it) that the target no longer has is
# therefore checked against the content as published at the target, with the target's own exemptions: the content
# it used to cover is re-evaluated, while retained content whose entry is still there stays exempt.
if [ -s "$tmp.everallow" ]; then
	LC_ALL=C sort -u "$tmp.everallow" > "$tmp.everallow.u" \
		|| die 'sort failed on the allowlist entries of this range'
	: > "$tmp.rescanned"
	while IFS= read -r entry; do
		case $entry in '' | '#'*) continue ;; esac
		grep -Fxq -- "$entry" "$tmp.allow" && continue   # still exempt at the target
		p=${entry%%"$tab"*}
		[ "$p" = "$entry" ] && continue                  # not an entry; the target's own parse reports a broken one
		grep -Fxq -- "$p" "$tmp.files" && continue        # changed in the range: already read at the target
		grep -Fxq -- "$p" "$tmp.gone" && continue
		grep -Fxq -- "$p" "$tmp.rescanned" && continue
		printf '%s\n' "$p" >> "$tmp.rescanned"
		blob_at "$target" "$p" "$tmp.blob" || continue    # not published at the target: nothing retained
		scan_file "$target" "$p" "$tmp.blob" "$tmp.allow" 0
	done < "$tmp.everallow.u"
fi

# --- what is wrong, by file, line and reason (never the value) ----------------------------------
if [ -s "$tmp.hits" ]; then
	echo "private data in changed files (replace the value with a placeholder such as <work dir>, <device ip> or <your dump folder>; $allow exempts one exact fixture line only):"
	while IFS="$tab" read -r c f lineno codes; do
		printf '%s: %s:%s: %s\n' "$c" "$f" "$lineno" "$codes"
	done < "$tmp.hits"
fi

if [ -s "$tmp.later" ]; then
	echo "exempted by an allowlist entry that is not in the same commit (it was added later in this range; the entry still has to name this exact line at $target):"
	while IFS="$tab" read -r c f lineno; do
		printf '  %s: %s:%s\n' "$c" "$f" "$lineno"
	done < "$tmp.later"
fi

# Each remaining entry must still describe exactly one line of its file as published at <target-ref>.
if [ -s "$tmp.ok" ]; then
	while IFS= read -r entry; do
		p=${entry%%"$tab"*}
		line=${entry#*"$tab"}
		if ! blob_at "$target" "$p" "$tmp.blob"; then
			if ! grep -Fxq -- "$p" "$tmp.stale"; then
				echo "stale publish-allowlist entry: $p is not in the target commit, so its entries are dead: remove them with the fixture"
				printf '%s\n' "$p" >> "$tmp.stale"
			fi
			fail=1; continue
		fi
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
