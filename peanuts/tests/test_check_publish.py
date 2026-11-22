"""check-publish.sh: its base (upstream/android, else upstream/main, else a message, else a ref that does not resolve)
and the Rule 2 exemptions its allowlist grants - one exact fictional fixture line, and nothing else."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, 'peanuts', 'scripts', 'check-publish.sh')
ALLOW = os.path.join(ROOT, 'peanuts', 'scripts', 'publish-allowlist.txt')
GATE = os.path.join('peanuts', 'scripts', 'check-publish.sh')


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


@unittest.skipUnless(shutil.which('git') and shutil.which('sh'), 'needs git and sh')
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
        self.commit('base.txt', 'upstream')
        self.base = self.git('rev-parse', 'HEAD')
        self.commit('mine.txt', 'a contributor')

    def git(self, *a):
        return subprocess.run(['git', *a], cwd=self.repo, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, text):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w', encoding='utf-8') as f:
            f.write(text)
        return full

    def commit(self, name, text):
        self.write(name, text + '\n')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', name)

    def check(self, *args):
        return subprocess.run(['sh', GATE, *args], cwd=self.repo, capture_output=True, text=True)


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

    def test_the_required_fixtures_pass(self):
        self.fixtures()
        r = self.check(self.base)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('clean', r.stdout)

    def test_an_unlisted_private_path_in_a_fixture_file_fails(self):
        self.fixtures()
        path = ENTRIES[0][0]
        # Built by concatenation, so this test file does not itself hold the path it feeds the gate.
        self.add_line(path, "        p = '" + '/home/' + "bob/keys/x'")
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
