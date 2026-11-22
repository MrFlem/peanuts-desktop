"""check-publish.sh: its base (upstream/android, else upstream/main, else a message, else a ref that does not
resolve), the committed blobs it reads rather than the working tree, its loopback and allowlist rules, the reasons it
reports without the value it found, and the pre-push hook that hands it the refs actually being pushed."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, 'peanuts', 'scripts', 'check-publish.sh')
ALLOW = os.path.join(ROOT, 'peanuts', 'scripts', 'publish-allowlist.txt')
HOOKS = os.path.join(ROOT, 'peanuts', 'scripts', 'install-hooks.sh')
GATE = os.path.join('peanuts', 'scripts', 'check-publish.sh')
ZERO = '0' * 40
REAL_GIT = shutil.which('git')
# Built here rather than written out: an adb port on a line is exactly what the gate flags in the files it reads,
# and this test file is one of those files.
ADB_PORT = ':' + '555' + '5'


def allowlist():
    """(path, line) for each entry of the repository's own allowlist, in file order."""
    entries = []
    with open(ALLOW, encoding='utf-8') as f:
        for raw in f:
            raw = raw.rstrip('\n')
            if not raw or raw.startswith('#'):
                continue
            path, line = raw.split('\t', 1)
            entries.append((path, line))
    return entries


ENTRIES = allowlist()


def homed(name):
    """A home-folder path, built here so this test file does not itself hold one the gate would flag."""
    return '/home/' + name


@unittest.skipUnless(REAL_GIT and shutil.which('sh'), 'needs git and sh')
class Base(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Tester')
        self.git('config', 'user.email', 'tester@example.org')
        os.makedirs(os.path.join(self.repo, 'peanuts', 'scripts'))
        shutil.copy(SCRIPT, os.path.join(self.repo, GATE))
        # A committed allowlist with no entries: the tests that need one write and commit it themselves (the gate
        # reads the allowlist from the object store at <target-ref>, so an uncommitted copy would give it nothing).
        self.write('peanuts/scripts/publish-allowlist.txt', '# no exemptions in this fixture repository\n')
        # The gate is committed with the base commit below, like the allowlist.
        self.branch = self.git('symbolic-ref', '--short', 'HEAD')
        self.commit('base.txt', 'upstream')
        self.base = self.git('rev-parse', 'HEAD')
        self.commit('mine.txt', 'a contributor')

    def git(self, *a):
        return subprocess.run(['git', *a], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, text):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full) or self.repo, exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(text)
        return full

    def commit(self, name, text):
        self.write(name, text + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', name)

    def commit_lines(self, path, lines, message=None):
        self.write(path, ''.join(line + '\n' for line in lines))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message or path)

    def check(self, *args, env=None):
        return subprocess.run(['sh', GATE, *args], cwd=self.repo, capture_output=True, text=True, env=env)

    def allow_path(self):
        return os.path.join(self.repo, 'peanuts', 'scripts', 'publish-allowlist.txt')

    def set_allowlist(self, text, message='the allowlist'):
        """Write the allowlist and commit it: the gate reads the published one, never the working tree."""
        self.write('peanuts/scripts/publish-allowlist.txt', text)
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)

    def stub_command(self, name, body):
        """A PATH whose `name` is a sh script instead of the real program: `body` is its text."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
        return dict(os.environ, PATH=d + os.pathsep + os.environ.get('PATH', ''))

    def stub_git_log_for(self, marker):
        """A git whose `log` fails only for the call whose format starts with `marker`."""
        body = ('seen=0\n'
                'for a in "$@"; do\n'
                '  [ "$a" = log ] && seen=1\n'
                '  case $a in\n'
                '    ' + marker + '*) [ "$seen" = 1 ] && { echo "injected failure" >&2; exit 128; } ;;\n'
                '  esac\n'
                'done\n'
                'exec ' + REAL_GIT + ' "$@"\n')
        return self.stub_command('git', body)

    def stub_git(self, subcommand):
        """A PATH whose git fails, and only for `subcommand`: the gate must not read that as a clean tree."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        path = os.path.join(d, 'git')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!/bin/sh\n'
                    'for a in "$@"; do [ "$a" = %s ] && { echo "injected failure" >&2; exit 128; }; done\n'
                    'exec %s "$@"\n' % (subcommand, REAL_GIT))
        os.chmod(path, 0o755)
        return dict(os.environ, PATH=d + os.pathsep + os.environ.get('PATH', ''))


class TheBase(Base):
    """The baseline argument: where it comes from, and what an unusable one does."""

    def test_upstream_main(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.base)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.base, r.stdout)

    def test_upstream_android_first(self):
        self.git('update-ref', 'refs/remotes/upstream/main', 'HEAD')
        self.git('update-ref', 'refs/remotes/upstream/android', self.base)
        r = self.check()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(self.base, r.stdout)

    def test_neither(self):
        r = self.check()
        self.assertEqual(r.returncode, 1)
        self.assertIn('neither upstream/android nor upstream/main', r.stdout)

    def test_a_baseline_that_does_not_resolve(self):
        # An unusable ref used to leave both rules looking at nothing and report clean.
        r = self.check('0' * 40)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('is not a commit', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_the_target_must_resolve_too(self):
        r = self.check('--target', '0' * 40)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('is not a commit', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_target_other_than_head_is_what_is_scanned(self):
        # The pre-push hook passes the pushed commit, which is not always HEAD.
        clean = self.git('rev-parse', 'HEAD')
        self.commit('later.txt', 'a later commit')
        r = self.check('--since', self.base, '--target', clean)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(clean, r.stdout)


class CommittedObjects(Base):
    """Rule 2 reads the commits being published, not the files on disk."""

    def test_a_secret_added_and_removed_later_is_still_found(self):
        # The value is in a commit the push publishes: a reader of the remote can check it out.
        self.commit_lines('net.py', ["HOST = '" + '192.168.' + "1.77'"])
        self.git('rm', '-q', 'net.py')
        self.git('commit', '-q', '-m', 'removes it again')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('net.py', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_uncommitted_edit_does_not_hide_a_committed_value(self):
        self.commit_lines('net.py', ["HOST = '" + '192.168.' + "1.78'"])
        self.write('net.py', 'HOST = <device ip>\n')   # the working tree is clean, the commit is not
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('net.py', r.stdout)

    def test_a_value_only_in_the_working_tree_is_not_published_and_not_flagged(self):
        self.write('net.py', "HOST = '" + '192.168.' + "1.79'\n")   # never staged, never committed
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_a_file_deleted_with_its_value_is_still_found(self):
        self.commit_lines('notes.txt', ['x', "LAN = '" + '10.' + "4.5.6'"])
        self.git('rm', '-q', 'notes.txt')
        self.git('commit', '-q', '-m', 'gone')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('notes.txt:2:', r.stdout)


class FailClosed(Base):
    """A git command that fails is the gate failing, never a clean tree."""

    def test_a_git_that_cannot_inspect_the_target_fails(self):
        # A2: "could not look" is a failure, never "nothing there" - the allowlist and every file are read with
        # git ls-tree first, so a failing inspection cannot pass for a tree without them.
        r = self.check(self.base, env=self.stub_git('ls-tree'))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('cannot look up', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_failing_git_log_fails(self):
        r = self.check(self.base, env=self.stub_git('log'))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('git log', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_failing_git_diff_tree_fails(self):
        r = self.check(self.base, env=self.stub_git('diff-tree'))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('git diff-tree', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_missing_object_fails(self):
        self.commit_lines('net.py', ["HOST = '" + '192.168.' + "1.90'"])
        oid = self.git('rev-parse', 'HEAD:net.py')
        os.remove(os.path.join(self.repo, '.git', 'objects', oid[:2], oid[2:]))
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('object is missing', r.stdout)
        self.assertNotIn('clean', r.stdout)


class CommitMessagePipeline(Base):
    """A1: every command in the trailer inspection fails the gate on its own, never a clean tree.

    The inspection used to be one pipeline (git log | awk | grep), and POSIX sh has no pipefail: only grep's status
    was read. A failing git log or awk left grep with nothing to find, which is what a clean range looks like.
    """

    AUTHOR_FORMAT = '--format=%h'
    MESSAGE_FORMAT = '--format=@@'

    def ai_trailer_commit(self):
        self.write('peanuts/desktop/x.py', 'x = 1\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'a change\n\nCo-Authored-By: Claude <noreply@anthropic.com>')

    def test_the_author_log_failing_is_not_a_pass(self):
        r = self.check(self.base, env=self.stub_git_log_for(self.AUTHOR_FORMAT))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('commit authors', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_the_message_log_failing_is_not_a_pass_although_the_author_log_succeeded(self):
        r = self.check(self.base, env=self.stub_git_log_for(self.MESSAGE_FORMAT))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('commit messages', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_trailer_is_never_a_pass_over_a_failing_message_log(self):
        # The trailer is there: a pipeline would have found it. The point is that the failing command fails the
        # gate as well, and that the result never reads as clean.
        self.ai_trailer_commit()
        r = self.check(self.base, env=self.stub_git_log_for(self.MESSAGE_FORMAT))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('commit messages', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_awk_failing_is_not_a_pass(self):
        env = self.stub_command('awk', 'echo "injected failure" >&2\nexit 2\n')
        r = self.check(self.base, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('awk failed', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_trailer_is_never_a_pass_over_a_failing_awk(self):
        self.ai_trailer_commit()
        env = self.stub_command('awk', 'echo "injected failure" >&2\nexit 2\n')
        r = self.check(self.base, env=env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn('awk failed', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_the_trailer_is_found_when_every_command_succeeds(self):
        self.ai_trailer_commit()
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('AI trailer in a commit message', r.stdout)


class AllowlistFromTheTarget(Base):
    """A2: the exemptions are the ones published at <target-ref>, and the range's history is accounted for."""

    def fixture_path(self):
        return 'peanuts/tests/test_fixture_lines.py'

    def fixture(self, name='alice'):
        """A line shaped like the private data rule 2 stops, built by concatenation like the others here."""
        return "        KEYS = '" + homed(name) + "/keys/prod.keys'"

    def test_an_uncommitted_allowlist_entry_cannot_exempt_a_line(self):
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.write('peanuts/scripts/publish-allowlist.txt', self.fixture_path() + '\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_uncommitted_allowlist_deletion_cannot_drop_a_published_exemption(self):
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        self.write('peanuts/scripts/publish-allowlist.txt', '# the checkouts exemptions, edited and not committed\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_the_allowlist_comes_from_the_target_not_from_the_checkout(self):
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        head = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', self.base)   # this tree has neither the fixture nor its entry
        r = self.check('--since', self.base, '--target', head)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_an_entry_added_later_in_the_range_is_reported(self):
        # The fixture and its entry may be committed in either order, so the entry covers the whole range - but
        # the commit that predates it is named, so the exemption is never silent.
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        first = self.git('rev-parse', 'HEAD')
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('added later in this range', r.stdout)
        self.assertIn(first, r.stdout)

    def test_a_later_entry_cannot_cover_an_earlier_different_value(self):
        # The line was a real value and a later commit edits it into the listed one: only the version whose text
        # matches the entry is exempt, so the earlier commit is still a hit.
        real = self.fixture('alice')
        self.commit_lines(self.fixture_path(), [real])
        self.write(self.fixture_path(), self.fixture('bob') + '\n')
        self.set_allowlist(self.fixture_path() + '\t' + self.fixture('bob') + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertIn('home-path', r.stdout)
        self.assertNotIn(homed('alice'), r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_target_without_an_allowlist_has_no_exemptions(self):
        self.commit_lines(self.fixture_path(), [self.fixture()])
        self.git('rm', '-q', 'peanuts/scripts/publish-allowlist.txt')
        self.git('commit', '-q', '-m', 'drops the allowlist')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_locally_deleted_allowlist_does_not_change_the_verdict(self):
        # A2: the committed allowlist decides. Removing the checkout's copy cannot revoke a published exemption,
        # and nothing in the working tree is consulted to reach that verdict.
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        os.unlink(self.allow_path())
        self.assertFalse(os.path.exists(self.allow_path()))
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_a_local_replacement_of_the_allowlist_does_not_change_the_verdict_either(self):
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        # A different entry in the checkout, for a line that was never published.
        self.write('peanuts/scripts/publish-allowlist.txt', self.fixture_path() + '\t' + self.fixture('bob') + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_a_locally_deleted_allowlist_cannot_hide_a_published_hit(self):
        self.commit_lines(self.fixture_path(), [self.fixture()])
        os.unlink(self.allow_path())
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_revoked_entry_rechecks_the_retained_fixture(self):
        # A2.3: the fixture and its entry are committed before the range; a later commit only removes the entry.
        # Nothing in the range touches the fixture file, so no scan of it happens - and its line is published with
        # no exemption at all, which the gate has to report.
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        retained = self.git('rev-parse', 'HEAD')
        self.set_allowlist('# the exemption is revoked; the fixture stays\n', message='revokes the exemption')
        r = self.check('--since', retained)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertIn('home-path', r.stdout)
        self.assertNotIn('clean', r.stdout)
        self.assertNotIn(homed('alice'), r.stdout)

    def test_a_retained_fixture_whose_entry_stays_is_clean(self):
        # The other side of the same rule: re-evaluating retained content must not turn a kept promise into a hit.
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        retained = self.git('rev-parse', 'HEAD')
        self.commit('unrelated.txt', 'a change that does not touch the fixture')
        r = self.check('--since', retained)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_an_entry_for_retained_content_is_validated_outside_the_range_too(self):
        # A2.3: an entry whose line the committed file no longer holds is stale even when no commit in this range
        # touches that file. It used to be skipped as "not exercised here", so a broken promise stayed silent.
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        self.write(self.fixture_path(), self.fixture('bob') + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'the fixture line is edited, the entry is not')
        stale_from = self.git('rev-parse', 'HEAD')
        self.commit('unrelated.txt', 'a change that does not touch the fixture')
        r = self.check('--since', stale_from)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('stale publish-allowlist entry', r.stdout)
        self.assertIn(self.fixture_path(), r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_allowlist_object_the_gate_cannot_read_fails(self):
        line = self.fixture()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n')
        oid = self.git('rev-parse', 'HEAD:peanuts/scripts/publish-allowlist.txt')
        os.remove(os.path.join(self.repo, '.git', 'objects', oid[:2], oid[2:]))
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('cannot read', r.stdout)
        self.assertNotIn('clean', r.stdout)


class HistoricalAllowlistBoundaries(Base):
    """A2: a range's allowlists are read blob by blob, never by concatenating their bytes.

    A blob is a whole file, and its last record may have no newline. Appending the blobs to each other runs that
    record into the next blob's first one, and when the first is a comment the entry that follows it disappears
    from the range's history: a revoked exemption is then never re-checked, and the gate reports a clean
    publication for a fixture that is still published with no exemption at all (the false negative Codex found).
    """

    def fixture_path(self):
        return 'peanuts/tests/test_hist_fixture.py'

    def line(self, name='alice'):
        return "        KEYS = '" + homed(name) + "/keys/prod.keys'"

    def revoked_history(self, unterminated):
        """The fixture and its entry are committed inside the range; the target revokes the entry.

        `unterminated` says which blob ends with a record that has no newline, and so which blob's first record is
        at risk: 'newest' is the target's own comment (the entry opens the blob of the commit just below it),
        'intermediate' is a comment in a commit between the entry's and the target's.
        """
        line = self.line()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist('# no exemptions yet\n', message='the baseline allowlist')
        since = self.git('rev-parse', 'HEAD')
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n', message='the fixture and its entry')
        if unterminated == 'intermediate':
            self.set_allowlist('# the list continues below, and this line has no newline',
                               message='an intermediate comment with no trailing newline')
        self.set_allowlist('# the exemption is revoked here' + ('' if unterminated == 'newest' else '\n'),
                           message='revokes the exemption')
        return since

    def test_a_revocation_is_found_when_the_target_blob_has_no_trailing_newline(self):
        since = self.revoked_history('newest')
        r = self.check('--since', since)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertIn('home-path', r.stdout)
        self.assertNotIn('clean', r.stdout)
        self.assertNotIn(homed('alice'), r.stdout)

    def test_a_revocation_is_found_when_an_intermediate_blob_has_no_trailing_newline(self):
        since = self.revoked_history('intermediate')
        r = self.check('--since', since)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertIn('home-path', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_the_same_revocation_is_found_when_every_blob_ends_with_a_newline(self):
        # The control: the revocation is the only thing that changed here, so the two tests above fail for the
        # missing newline and not for the fixtures or for the revocation itself.
        line = self.line()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist('# no exemptions yet\n', message='the baseline allowlist')
        since = self.git('rev-parse', 'HEAD')
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n', message='the fixture and its entry')
        self.set_allowlist('# the exemption is revoked here\n', message='revokes the exemption')
        r = self.check('--since', since)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(self.fixture_path() + ':1:', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_baseline_blob_without_a_trailing_newline_stays_clean_when_its_entry_is_kept(self):
        # The harmless side of the same shape: the target keeps the entry, so the entry the history holds still
        # names an exemption the target publishes. Nothing should be reported for a shorter file ending.
        line = self.line()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n# the list ends here', message='the baseline')
        since = self.git('rev-parse', 'HEAD')
        self.commit('unrelated.txt', 'a change that does not touch the fixture')
        r = self.check('--since', since)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_an_entry_repeated_across_two_blobs_is_not_a_duplicate_error(self):
        # The duplicate rule is one entry per fixture line in the target's allowlist; the same entry committed
        # twice in the range's history is ordinary history and collapses to one.
        line = self.line()
        self.commit_lines(self.fixture_path(), [line])
        self.set_allowlist('# no exemptions yet\n', message='the baseline allowlist')
        since = self.git('rev-parse', 'HEAD')
        self.set_allowlist(self.fixture_path() + '\t' + line + '\n', message='the fixture and its entry')
        # The same allowlist published again: a second commit whose blob repeats the entry.
        self.git('commit', '-q', '--allow-empty', '-m', 'the same entry, committed again')
        r = self.check('--since', since)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)
        self.assertNotIn('repeats an earlier entry', r.stdout)


class Loopback(Base):
    """A loopback host is not a network address, and must not hide one beside it on the same line."""

    def test_a_loopback_address_alone_passes(self):
        self.commit_lines('local.py', ["BIND = '127." + "0.0.1'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_a_loopback_adb_port_alone_passes(self):
        self.commit_lines('local.py', ['adb connect 127.' + '0.0.1' + ADB_PORT])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_a_lan_address_beside_a_loopback_address_fails(self):
        # The old gate dropped the whole line because it mentioned 127.0.0.1, hiding the address beside it.
        self.commit_lines('local.py', ["HOSTS = ['127." + "0.0.1', '192.168." + "1.55']"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('local.py:1: lan-ip', r.stdout)

    def test_a_lan_adb_address_beside_a_loopback_one_fails(self):
        self.commit_lines('local.py', ['adb connect 127.' + '0.0.1' + ADB_PORT,
                                       'adb connect 10.' + '9.8.7' + ADB_PORT])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('local.py:2: lan-ip', r.stdout)
        self.assertIn('adb-over-network', r.stdout)

    def test_a_port_with_no_host_fails(self):
        self.commit_lines('local.py', ['PORT = ' + ADB_PORT])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('adb-over-network', r.stdout)


class Rule2Exemptions(Base):
    """The allowlist accepts the required fictional fixtures, and only those exact lines."""

    def fixtures(self):
        """Write each allowlisted file with exactly its allowlisted lines, and commit them with their entries."""
        self.assertTrue(ENTRIES, 'the allowlist has no entries')
        for path in sorted({p for p, _ in ENTRIES}):
            self.write(path, ''.join(line + '\n' for p, line in ENTRIES if p == path))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'the redaction fixtures')
        self.set_allowlist(''.join(p + '\t' + line + '\n' for p, line in ENTRIES), 'the fixtures and their entries')

    def add_line(self, path, line):
        with open(os.path.join(self.repo, path), 'a', encoding='utf-8') as f:
            f.write(line + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'one more line')

    def test_the_eight_required_fixtures_are_still_listed(self):
        # This WO must not change the exemptions it was asked to preserve.
        self.assertEqual(len(ENTRIES), 8)
        self.assertEqual({p for p, _ in ENTRIES},
                         {'peanuts/tests/test_desktop_engine.py', 'peanuts/tests/test_desktop_model.py'})

    def test_the_required_fixtures_pass(self):
        self.fixtures()
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_an_unlisted_private_path_in_a_fixture_file_fails(self):
        self.fixtures()
        path = ENTRIES[0][0]
        # Built by concatenation, so this test file does not itself hold the path it feeds the gate.
        self.add_line(path, "        p = '" + homed('bob') + "/keys/x'")
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn(path + ':', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_edited_fixture_fails(self):
        # A real name in place of the fictional one: the listed text no longer matches, so the entry is stale.
        self.fixtures()
        path, line = next(e for e in ENTRIES if 'alice' in e[1])
        edited = line.replace('alice', 'eve')
        self.assertNotEqual(edited, line)
        self.write(path, ''.join((edited if l == line else l) + '\n' for p, l in ENTRIES if p == path))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'renamed')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('stale publish-allowlist entry', r.stdout)

    def test_a_second_copy_of_a_listed_line_fails(self):
        self.fixtures()
        path, line = ENTRIES[0]
        self.add_line(path, line)
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('private data in changed files', r.stdout)
        self.assertIn('repeated', r.stdout)

    def test_a_deleted_fixture_fails(self):
        self.fixtures()
        path = ENTRIES[0][0]
        self.git('rm', '-q', path)
        self.git('commit', '-q', '-m', 'drops the fixture')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('stale publish-allowlist entry', r.stdout)
        self.assertIn(path, r.stdout)

    def test_a_duplicate_entry_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.set_allowlist(path + '\t' + line + '\n' + path + '\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('repeats an earlier entry', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_entry_with_no_tab_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.set_allowlist(path + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('has no tab', r.stdout)

    def test_an_entry_with_two_tabs_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.set_allowlist(path + '\t' + line + '\tanother\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('more than one tab', r.stdout)

    def test_an_entry_outside_peanuts_tests_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.set_allowlist('peanuts/desktop/x.py\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('not under peanuts/tests/', r.stdout)

    def test_an_entry_with_a_cr_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.set_allowlist(path + '\t' + line + '\r\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('has a CR', r.stdout)

    def test_an_entry_on_the_last_line_without_a_newline_is_still_read(self):
        # A trailing newline is a file convention, not part of the promise: the last entry must still count.
        path, line = ENTRIES[0]
        self.write(path, line + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'one fixture line')
        self.set_allowlist(path + '\t' + line)   # no trailing newline, and the last entry must still count
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_leading_spaces_of_a_fixture_line_are_kept_byte_for_byte(self):
        # The entry's line has to reach the comparison exactly, leading indentation included.
        path, line = ENTRIES[0]
        self.assertTrue(line.startswith(' '), 'the first fixture line is indented; this test needs one that is')
        self.fixtures()
        self.set_allowlist(path + '\t' + line.lstrip(' ') + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('stale publish-allowlist entry', r.stdout)


class Diagnostics(Base):
    """A report names the file, the line and the reason - never the value that was found."""

    def test_the_value_is_not_printed(self):
        self.commit_lines('net.py', ["HOSTS = ['" + '192.168.' + "3.44', '" + homed('mallory') + "/games']"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('net.py:1:', r.stdout)
        self.assertIn('lan-ip', r.stdout)
        self.assertIn('home-path', r.stdout)
        self.assertNotIn('192.168.' + '3.44', r.stdout)
        self.assertNotIn(homed('mallory'), r.stdout)

    def test_an_ai_author_is_named_by_commit_not_by_address(self):
        self.write('peanuts/desktop/x.py', 'x = 1\n')
        self.git('add', '-A')
        self.git('-c', 'user.name=Claude', '-c', 'user.email=builder@example.org',
                 'commit', '-q', '-m', 'a change')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('AI author/committer', r.stdout)
        self.assertNotIn('builder@example.org', r.stdout)
        self.assertNotIn('Claude', r.stdout)
        self.assertIn(self.git('rev-parse', '--short', 'HEAD')[:7], r.stdout)


class DetectionScope(Base):
    """The high-confidence credential shapes A7 adds, and the honest limits (a document, not a guarantee)."""

    def test_a_private_key_header_fails(self):
        self.commit_lines('k.py', ["KEY = '''-----BEGIN " + "OPENSSH PRIVATE KEY-----'''"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('private-key', r.stdout)
        self.assertNotIn('BEGIN', r.stdout.split('private-key')[0])

    def test_an_aws_access_key_id_fails(self):
        self.commit_lines('aws.py', ["ID = '" + 'AKIA' + "IOSFODNN7EXAMPLE'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('aws-access-key', r.stdout)

    def test_a_github_token_fails(self):
        self.commit_lines('gh.py', ["TOKEN = '" + 'ghp_' + 'a' * 36 + "'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('github-token', r.stdout)

    def test_a_google_api_key_fails(self):
        self.commit_lines('g.py', ["K = '" + 'AIza' + 'b' * 35 + "'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('google-api-key', r.stdout)

    def test_a_slack_token_fails(self):
        self.commit_lines('s.py', ["T = '" + 'xoxb-' + '1234567890' + "-abcdefghij'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('slack-token', r.stdout)

    def test_a_stripe_live_key_fails(self):
        self.commit_lines('st.py', ["K = '" + 'sk_live_' + 'c' * 24 + "'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('stripe-live-key', r.stdout)

    def test_an_ordinary_hash_and_title_id_are_not_flagged(self):
        # Matching these would fail honest changes, which is how a gate gets switched off.
        self.commit_lines('ids.py', ["TITLE = '01006BB00C6F0000'", "SHA = 'deadbeefcafe0123456789abcdef0123456789ab'"])
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_the_limits_are_documented(self):
        # The whole header: it grows as the policy does, and the limits are what must not be lost from it.
        with open(SCRIPT, encoding='utf-8') as f:
            header = f.read().split('set -u', 1)[0]
        self.assertIn('cannot show that a change holds no secret', header)
        self.assertIn('binary file is skipped', header)


class PrePushHook(Base):
    """A3: the hook checks the refs git hands it, not HEAD and not one assumed ref."""

    def setUp(self):
        super().setUp()
        shutil.copy(HOOKS, os.path.join(self.repo, 'peanuts', 'scripts', 'install-hooks.sh'))
        subprocess.run(['sh', 'peanuts/scripts/install-hooks.sh'], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        self.hook = os.path.join(self.repo, '.git', 'hooks', 'pre-push')
        self.git('update-ref', 'refs/remotes/upstream/main', self.base)

    def push(self, *updates):
        return self.push_raw(''.join('%s %s %s %s\n' % u for u in updates))

    def push_raw(self, stdin):
        return subprocess.run(['sh', self.hook, 'origin', 'https://example.org/repo.git'],
                              cwd=self.repo, input=stdin, capture_output=True, text=True)

    def local_hook(self, body):
        """An existing hook of the same name, which install-hooks.sh chains and must not let eat stdin."""
        path = os.path.join(self.repo, '.git', 'hooks', 'pre-push.local')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('#!/bin/sh\n' + body)
        os.chmod(path, 0o755)
        return path

    def secret_commit(self, name='net.py', ip='5'):
        self.commit_lines(name, ["HOST = '" + '192.168.' + ip + ".9'"])
        return self.git('rev-parse', 'HEAD')

    def test_a_new_branch_with_a_secret_is_refused(self):
        sha = self.secret_commit()
        r = self.push(('refs/heads/topic', sha, 'refs/heads/topic', ZERO))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('private data in changed files', r.stdout)

    def test_a_fast_forward_checks_only_the_commits_it_adds(self):
        on_remote = self.secret_commit()          # already published: not this push's business
        self.commit('clean.txt', 'a clean change')
        head = self.git('rev-parse', 'HEAD')
        r = self.push(('refs/heads/main', head, 'refs/heads/main', on_remote))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(on_remote + '..' + head, r.stdout)

    def test_a_fast_forward_that_carries_the_secret_is_refused(self):
        self.secret_commit()
        head = self.git('rev-parse', 'HEAD')
        r = self.push(('refs/heads/main', head, 'refs/heads/main', self.base))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('private data in changed files', r.stdout)

    def test_a_non_fast_forward_push_is_checked(self):
        # The remote tip is not an ancestor of what is pushed, so no remote tip is a usable baseline: the branch is
        # checked against the gate's own baseline, which is where the secret is.
        self.git('checkout', '-q', '-b', 'other', self.base)
        self.commit('other.txt', 'another line of work')
        remote = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', self.branch)
        sha = self.secret_commit()
        r = self.push(('refs/heads/main', sha, 'refs/heads/main', remote))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('private data in changed files', r.stdout)

    def test_head_elsewhere_does_not_decide_what_is_checked(self):
        # HEAD is left on a branch that holds the secret; the pushed ref is clean, so the push is allowed.
        base = self.base
        self.git('checkout', '-q', '-b', 'dirty', base)
        self.secret_commit()
        self.git('checkout', '-q', '-b', 'clean', base)
        self.commit('clean.txt', 'a clean change')
        clean = self.git('rev-parse', 'HEAD')
        self.git('checkout', '-q', 'dirty')   # HEAD now has the secret in its history
        r = self.push(('refs/heads/clean', clean, 'refs/heads/clean', base))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_every_update_is_checked_and_one_bad_one_refuses_the_push(self):
        good = self.git('rev-parse', 'HEAD')
        bad = self.secret_commit()
        r = self.push(('refs/heads/one', good, 'refs/heads/one', self.base),
                      ('refs/heads/two', bad, 'refs/heads/two', ZERO))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        # The clean update was checked and passed; the other one failed; the push is refused for both together.
        self.assertIn(self.base + '..' + good, r.stdout)
        self.assertIn('private data in changed files', r.stdout)

    def test_a_local_hook_that_consumes_stdin_cannot_hide_a_secret(self):
        # Codex's scenario: the previous hook reads every record and exits 0, so a hook that then read stdin itself
        # saw none, checked nothing and allowed the push. The records are captured first, then replayed.
        self.local_hook('cat > /dev/null\nexit 0\n')
        sha = self.secret_commit()
        r = self.push(('refs/heads/topic', sha, 'refs/heads/topic', ZERO))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('private data in changed files', r.stdout)

    def test_a_local_hook_that_consumes_stdin_does_not_stop_a_clean_push(self):
        self.local_hook('cat > /dev/null\nexit 0\n')
        self.commit('clean.txt', 'a clean change')
        sha = self.git('rev-parse', 'HEAD')
        r = self.push(('refs/heads/main', sha, 'refs/heads/main', self.base))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_both_hooks_are_given_every_record(self):
        seen = os.path.join(self.repo, 'the-local-hook-saw.txt')
        self.local_hook('cat > ' + seen + '\nexit 0\n')
        sha = self.git('rev-parse', 'HEAD')
        updates = (('refs/heads/one', sha, 'refs/heads/one', self.base),
                   ('refs/heads/two', sha, 'refs/heads/two', ZERO))
        r = self.push(*updates)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(seen, encoding='utf-8') as f:
            self.assertEqual(f.read(), ''.join('%s %s %s %s\n' % u for u in updates))

    def test_a_greedy_local_hook_does_not_stop_the_other_refs_from_being_checked(self):
        self.local_hook('cat > /dev/null\nexit 0\n')
        good = self.git('rev-parse', 'HEAD')
        bad = self.secret_commit()
        r = self.push(('refs/heads/one', good, 'refs/heads/one', self.base),
                      ('refs/heads/two', bad, 'refs/heads/two', ZERO))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(self.base + '..' + good, r.stdout)
        self.assertIn('private data in changed files', r.stdout)

    def test_a_local_hook_that_refuses_stops_the_push(self):
        self.local_hook('exit 1\n')
        sha = self.git('rev-parse', 'HEAD')
        r = self.push(('refs/heads/main', sha, 'refs/heads/main', self.base))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('pre-push.local refused this push', r.stdout)

    def test_a_push_with_no_records_is_refused(self):
        # Records that could not be captured look exactly like this: never an empty set that passes.
        r = self.push()
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('no ref updates', r.stdout)

    def test_a_record_this_hook_cannot_read_is_refused(self):
        r = self.push_raw('refs/heads/main abc\n')
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('not four fields', r.stdout)

    def test_a_record_without_a_commit_id_is_refused(self):
        r = self.push_raw('refs/heads/main nothex refs/heads/main ' + ZERO + '\n')
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('does not name a commit', r.stdout)

    def test_a_record_with_a_short_commit_id_is_refused(self):
        r = self.push_raw('refs/heads/main abcdef0 refs/heads/main ' + ZERO + '\n')
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('does not name a commit', r.stdout)

    def test_a_record_with_a_bad_remote_commit_is_refused(self):
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s refs/heads/main nothex\n' % sha)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('does not name a remote commit', r.stdout)

    def test_a_last_record_without_a_newline_is_refused(self):
        # A3: `read` returns false for a final record with no newline, and the loop used to drop it - a record the
        # hook could not read whole looked like no record at all. Even a valid one is refused now.
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s refs/heads/main %s' % (sha, self.base))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('not terminated by a newline', r.stdout)

    def test_a_malformed_last_record_without_a_newline_is_refused(self):
        r = self.push_raw('refs/heads/main abc refs/heads/main')   # three fields, and no newline either
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('not four fields', r.stdout)
        self.assertIn('refusing to push', r.stdout)

    def test_an_empty_record_is_refused(self):
        # Empty input is refused before the loop (it looks like records that could not be captured); a blank
        # record among records is malformed, and is refused rather than skipped.
        r = self.push_raw('\n')
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('empty record', r.stdout)

    def test_a_valid_last_record_still_passes_with_its_newline(self):
        self.commit('clean.txt', 'a clean change')
        sha = self.git('rev-parse', 'HEAD')
        r = self.push(('refs/heads/main', sha, 'refs/heads/main', self.base))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_a_record_with_an_overlong_remote_commit_is_refused(self):
        # Codex's case: 41 hex digits cleared the old "at least 40" test, failed the ancestry test, and were read
        # as "no usable remote tip" - the gate then checked the branch and the push was allowed.
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s refs/heads/main %s\n' % (sha, ZERO + '0'))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("does not name a remote commit of this repository's length (40 hex digits)", r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_a_record_with_an_overlong_commit_id_is_refused(self):
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s0 refs/heads/main %s\n' % (sha, ZERO))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("does not name a commit of this repository's length (40 hex digits)", r.stdout)

    def test_a_record_with_a_sha256_length_id_is_refused_in_a_sha1_repository(self):
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s refs/heads/main %s\n' % (sha, '0' * 64))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("does not name a remote commit of this repository's length (40 hex digits)", r.stdout)

    def test_a_deleted_ref_publishes_nothing(self):
        r = self.push(('(delete)', ZERO, 'refs/heads/gone', self.base))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('only deletes refs', r.stdout)


class Sha256Repository(Base):
    """A3: the id length is the repository's own object format, not "at least 40"."""

    def push(self, *updates):
        return self.push_raw(''.join('%s %s %s %s\n' % u for u in updates))

    def push_raw(self, stdin):
        return subprocess.run(['sh', self.hook, 'origin', 'https://example.org/repo.git'],
                              cwd=self.repo, input=stdin, capture_output=True, text=True)

    def setUp(self):
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        made = subprocess.run(['git', 'init', '-q', '--object-format=sha256'], cwd=self.repo,
                              capture_output=True, text=True)
        if made.returncode != 0:
            self.skipTest('this git cannot make a sha256 repository')
        self.git('config', 'user.name', 'Tester')
        self.git('config', 'user.email', 'tester@example.org')
        os.makedirs(os.path.join(self.repo, 'peanuts', 'scripts'))
        shutil.copy(SCRIPT, os.path.join(self.repo, GATE))
        self.write('peanuts/scripts/publish-allowlist.txt', '# no exemptions in this fixture repository\n')
        self.branch = self.git('symbolic-ref', '--short', 'HEAD')
        self.commit('base.txt', 'upstream')
        self.base = self.git('rev-parse', 'HEAD')
        self.commit('mine.txt', 'a contributor')
        shutil.copy(HOOKS, os.path.join(self.repo, 'peanuts', 'scripts', 'install-hooks.sh'))
        subprocess.run(['sh', 'peanuts/scripts/install-hooks.sh'], cwd=self.repo, check=True,
                       capture_output=True, text=True)
        self.hook = os.path.join(self.repo, '.git', 'hooks', 'pre-push')
        self.git('update-ref', 'refs/remotes/upstream/main', self.base)

    def test_a_record_with_sha256_ids_is_accepted(self):
        sha = self.git('rev-parse', 'HEAD')
        self.assertEqual(len(sha), 64)
        r = self.push(('refs/heads/topic', sha, 'refs/heads/topic', '0' * 64))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_a_sha1_length_id_is_refused_in_a_sha256_repository(self):
        sha = self.git('rev-parse', 'HEAD')
        r = self.push_raw('refs/heads/main %s refs/heads/main %s\n' % (sha, '0' * 40))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("does not name a remote commit of this repository's length (64 hex digits)", r.stdout)

    def test_a_deleted_ref_uses_the_sha256_zero_id(self):
        r = self.push(('(delete)', '0' * 64, 'refs/heads/gone', self.base))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('only deletes refs', r.stdout)


class TheOtherRules(Base):
    """Everything the gate checked before it checked its fixtures still fails it."""

    def test_private_data_in_an_ordinary_file_fails(self):
        self.write('peanuts/desktop/engine/net.py', "HOST = '" + '192.168.' + "1.30'\n")
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'a host')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('private data in changed files', r.stdout)
        self.assertIn('peanuts/desktop/engine/net.py', r.stdout)

    def test_an_ai_trailer_still_fails(self):
        self.write('peanuts/desktop/x.py', 'x = 1\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'a change\n\nCo-Authored-By: Claude <noreply@anthropic.com>')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('AI trailer in a commit message', r.stdout)

    def test_an_ai_author_still_fails(self):
        self.write('peanuts/desktop/x.py', 'x = 1\n')
        self.git('add', '-A')
        self.git('-c', 'user.name=Claude', 'commit', '-q', '-m', 'a change')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('AI author/committer', r.stdout)


if __name__ == '__main__':
    unittest.main()
