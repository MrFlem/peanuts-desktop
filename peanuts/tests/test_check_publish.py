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
        shutil.copy(ALLOW, os.path.join(self.repo, 'peanuts', 'scripts', 'publish-allowlist.txt'))
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
        """Write each allowlisted file with exactly its allowlisted lines, and commit them."""
        self.assertTrue(ENTRIES, 'the allowlist has no entries')
        for path in sorted({p for p, _ in ENTRIES}):
            self.write(path, ''.join(line + '\n' for p, line in ENTRIES if p == path))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'the redaction fixtures')

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
        self.write('peanuts/scripts/publish-allowlist.txt',
                   path + '\t' + line + '\n' + path + '\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('repeats an earlier entry', r.stdout)
        self.assertNotIn('clean', r.stdout)

    def test_an_entry_with_no_tab_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.write('peanuts/scripts/publish-allowlist.txt', path + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('has no tab', r.stdout)

    def test_an_entry_with_two_tabs_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.write('peanuts/scripts/publish-allowlist.txt', path + '\t' + line + '\tanother\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('more than one tab', r.stdout)

    def test_an_entry_outside_peanuts_tests_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.write('peanuts/scripts/publish-allowlist.txt', 'peanuts/desktop/x.py\t' + line + '\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('not under peanuts/tests/', r.stdout)

    def test_an_entry_with_a_cr_fails(self):
        path, line = ENTRIES[0]
        self.fixtures()
        self.write('peanuts/scripts/publish-allowlist.txt', path + '\t' + line + '\r\n')
        r = self.check(self.base)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn('has a CR', r.stdout)

    def test_an_entry_on_the_last_line_without_a_newline_is_still_read(self):
        # A trailing newline is a file convention, not part of the promise: the last entry must still count.
        path, line = ENTRIES[0]
        self.write(path, line + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'one fixture line')
        self.write('peanuts/scripts/publish-allowlist.txt', path + '\t' + line)
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('clean', r.stdout)

    def test_leading_spaces_of_a_fixture_line_are_kept_byte_for_byte(self):
        # The entry's line has to reach the comparison exactly, leading indentation included.
        path, line = ENTRIES[0]
        self.assertTrue(line.startswith(' '), 'the first fixture line is indented; this test needs one that is')
        self.fixtures()
        self.write('peanuts/scripts/publish-allowlist.txt', path + '\t' + line.lstrip(' ') + '\n')
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
        with open(SCRIPT, encoding='utf-8') as f:
            head = f.read(6000)
        self.assertIn('cannot show that a change holds no secret', head)
        self.assertIn('binary file is skipped', head)


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
        stdin = ''.join('%s %s %s %s\n' % u for u in updates)
        return subprocess.run(['sh', self.hook, 'origin', 'https://example.org/repo.git'],
                              cwd=self.repo, input=stdin, capture_output=True, text=True)

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

    def test_a_deleted_ref_publishes_nothing(self):
        r = self.push(('(delete)', ZERO, 'refs/heads/gone', self.base))
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
