"""Static test-runner proposals. Never import project modules or execute a command."""
import ast
import configparser
import json
import os
from pathlib import Path
import re
import shlex
try:
    import tomllib
except ImportError:
    tomllib = None


def js_test_file(node):
    return node['language'] in ('javascript', 'typescript', 'tsx') and bool(
        re.search(r'\.(test|spec)\.[cm]?[jt]sx?$|(^|/)__tests__/', node['file']))


def python_test_case(node):
    return (node['language'] == 'python' and node['kind'] in ('function', 'method') and
            node['name'].split('.')[-1].startswith('test_') and
            bool(re.search(r'(^|/)test_[^/]+\.py$|_test\.py$', node['file'])))


def proposal(argv=None, reason=None, runner=None, scope=None, evidence=()):
    return dict(command=argv, cwd='.', command_reason=reason, command_runner=runner,
                command_scope=scope, command_evidence=list(evidence))


class Commands:
    """Cache only within one plan; configuration changes are read again on the next request."""
    def __init__(self, project, runner_root=None):
        self.project = project
        # Where commands run and local tools live: the reviewed checkout, also when the analysed tree is a
        # temporary snapshot of tracked files (review --staged), which has no .venv/ or vendor/.
        self.runner_root = Path(runner_root or project.root).resolve()
        self.cache = {}
        self.trees = {}

    def read(self, path):
        if path not in self.cache:
            local = self.project.cg.local_path(self.project.root, path)
            if not local.exists():
                self.cache[path] = None
            else:
                if local.stat().st_size > 1000000:
                    raise ValueError('Runner configuration exceeds the read limit.')
                if path in self.project.files:
                    self.project._read(path)  # keep source freshness checks
                self.cache[path] = local.read_text(encoding='utf-8')
        return self.cache[path]

    def tree(self, path):
        if path not in self.trees:
            self.trees[path] = ast.parse(self.read(path))
        return self.trees[path]

    def pytest_evidence(self, path):
        evidence = [name for name in ('pytest.ini', '.pytest.ini') if self.read(name) is not None]
        for parent in (Path('.'), *reversed(Path(path).parent.parents), Path(path).parent):
            name = (parent / 'conftest.py').as_posix()
            if self.read(name) is not None and name not in evidence:
                evidence.append(name)
        for name in ('setup.cfg', 'tox.ini'):
            text = self.read(name)
            if text is None:
                continue
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(text)
            if config.has_section('tool:pytest') or (name == 'tox.ini' and config.has_section('pytest')):
                evidence.append(name)
            elif name == 'setup.cfg' and config.has_section('pytest'):
                raise ValueError('Legacy setup.cfg [pytest] is not a supported pytest configuration.')
        text = self.read('pyproject.toml')
        if text is not None:
            if tomllib is None:
                raise ValueError('TOML runner detection needs Python 3.11 or newer.')
            tool = tomllib.loads(text).get('tool', {})
            if isinstance(tool, dict) and any(key.startswith('pytest') for key in tool):
                evidence.append('pyproject.toml')
        return evidence

    def unittest_class(self, node):
        """Only top-level classes with an explicit local inheritance chain to unittest.TestCase."""
        tree = self.tree(node['file'])
        modules, bases = set(), set()
        for entry in tree.body:
            if isinstance(entry, ast.Import):
                modules.update(alias.asname or alias.name for alias in entry.names if alias.name == 'unittest')
            elif isinstance(entry, ast.ImportFrom) and entry.module == 'unittest' and not entry.level:
                bases.update(alias.asname or alias.name for alias in entry.names if alias.name == 'TestCase')
        rebound = {entry.id for entry in ast.walk(tree) if isinstance(entry, ast.Name) and isinstance(entry.ctx, ast.Store)}
        rebound.update(entry.name for entry in tree.body if isinstance(entry, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        modules -= rebound
        bases -= rebound
        classes = {entry.name: entry for entry in tree.body if isinstance(entry, ast.ClassDef)}
        known = set()
        for _ in classes:
            for name, cls in classes.items():
                if any((isinstance(base, ast.Name) and base.id in bases | known) or
                       (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and
                        base.value.id in modules and base.attr == 'TestCase') for base in cls.bases):
                    known.add(name)
        parts = node['name'].split('.')
        return len(parts) == 2 and parts[0] in known and any(
            isinstance(entry, (ast.FunctionDef, ast.AsyncFunctionDef)) and entry.name == parts[1]
            for entry in classes[parts[0]].body)

    def python(self, node):
        path, name = node['file'], node['name']
        interpreter = 'python3'
        interpreter_evidence = []
        # Virtualenv executables are normally symlinks: only inspect their metadata, never run them.
        for candidate in ('.venv/bin/python', 'venv/bin/python'):
            binary = self.runner_root / candidate
            if binary.is_file() and os.access(binary, os.X_OK):
                interpreter = candidate
                interpreter_evidence = [candidate]
                break
        evidence = self.pytest_evidence(path)
        if evidence:
            parts = name.split('.')
            if not all(part.isidentifier() for part in parts) or len(parts) > 2:
                return proposal(reason='The pytest node cannot be selected statically.')
            if len(parts) == 2 and not parts[0].startswith('Test') and not self.unittest_class(node):
                return proposal(reason='The class is not a recognized pytest or unittest test class.')
            return proposal([interpreter, '-m', 'pytest', './' + path + '::' + '::'.join(parts)],
                            runner='pytest', scope='test', evidence=evidence + interpreter_evidence)
        if not self.unittest_class(node):
            return proposal(reason='No pytest configuration or explicit unittest.TestCase class was found.')
        module = Path(path).with_suffix('')
        if not all(part.isidentifier() for part in module.parts):
            return proposal(reason='The unittest module path is not importable from the project root.')
        for parent in Path(path).parents:
            if parent != Path('.') and self.read((parent / '__init__.py').as_posix()) is None:
                return proposal(reason='The unittest package has no __init__.py; root imports are uncertain.')
        return proposal([interpreter, '-m', 'unittest', '.'.join(module.parts) + '.' + name],
                        runner='unittest', scope='test', evidence=[path] + interpreter_evidence)

    def javascript(self, node):
        for parent in Path(node['file']).parents:
            if parent != Path('.') and self.read((parent / 'package.json').as_posix()) is not None:
                return proposal(reason='The test belongs to a nested package; a root command is uncertain.')
        text = self.read('package.json')
        if text is None:
            return proposal(reason='No package.json declares Jest or Vitest.')
        package = json.loads(text)
        runners = set()
        custom_script = False
        for key in ('dependencies', 'devDependencies'):
            runners.update(name for name in ('jest', 'vitest') if name in package.get(key, {}))
        for script in package.get('scripts', {}).values():
            if not isinstance(script, str):
                continue
            tokens = shlex.split(script)
            if tokens and tokens[0] in ('jest', 'vitest', './node_modules/.bin/jest', './node_modules/.bin/vitest'):
                runner = Path(tokens[0]).name
                runners.add(runner)
                custom_script |= tokens[1:] not in ([], ['run']) if runner == 'vitest' else bool(tokens[1:])
        if len(runners) != 1:
            return proposal(reason='Jest and Vitest are ambiguous.' if runners else 'No package.json declaration identifies Jest or Vitest.')
        if custom_script:
            return proposal(reason='Runner scripts require project-specific options; confirm the project command.', evidence=['package.json'])
        runner = next(iter(runners))
        if not js_test_file(node) or (runner == 'vitest' and not re.search(r'\.(test|spec)\.[cm]?[jt]sx?$', node['file'])):
            return proposal(reason='The file is not a recognized test file for the declared runner.')
        argv = ['./node_modules/.bin/' + runner, '--runTestsByPath'] if runner == 'jest' else ['./node_modules/.bin/vitest', 'run']
        return proposal(argv + ['./' + node['file']], runner=runner, scope='file', evidence=['package.json'])

    def go(self, node):
        module = self.read('go.mod')
        if module is None or not re.search(r'(?m)^\s*module\s+(?:"[^"\n]+"|[^\s]+)\s*(?://[^\n]*)?$', module):
            return proposal(reason='No module declaration in root go.mod identifies a runnable Go module.')
        for parent in Path(node['file']).parents:
            if parent != Path('.') and self.read((parent / 'go.mod').as_posix()) is not None:
                return proposal(reason='The Go test is in a nested module; a root command is uncertain.')
        name = node['name']
        if not node['file'].endswith('_test.go') or not name.isidentifier() or not name.startswith(('Test', 'Fuzz', 'Example')) or name == 'TestMain':
            return proposal(reason='No supported Go test selector was identified.')
        parent = Path(node['file']).parent.as_posix()
        package = '.' if parent == '.' else './' + parent
        return proposal(['go', 'test', package, '-run', '^' + re.escape(name) + '$'],
                        runner='go', scope='test', evidence=['go.mod', node['file']])

    def php(self, node):
        """PHPUnit only when the checkout declares it: a root phpunit.xml(.dist) and vendor/bin/phpunit inside it."""
        root = self.runner_root
        configs = [name for name in ('phpunit.xml', 'phpunit.xml.dist') if (root / name).is_file() and not (root / name).is_symlink()]
        try:
            binary = (root / 'vendor' / 'bin' / 'phpunit').resolve(strict=True)
        except (OSError, RuntimeError):
            binary = None
        if not configs or binary is None or not binary.is_file() or not binary.is_relative_to(root):
            return proposal(reason='No root phpunit.xml(.dist) with vendor/bin/phpunit inside the project.')
        parts = node['name'].split('.')
        if node['kind'] != 'method' or len(parts) != 2 or not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', part) for part in parts):
            return proposal(reason='No PHPUnit test method was identified.')
        return proposal(['php', 'vendor/bin/phpunit', '--filter', phpunit_filter([parts[0] + '::' + parts[1]]), node['file']],
                        runner='phpunit', scope='test', evidence=configs + ['vendor/bin/phpunit'])

    def test(self, node):
        try:
            if node['language'] == 'python':
                return self.python(node)
            if node['language'] == 'php':
                return self.php(node)
            if node['language'] == 'go':
                return self.go(node)
            if node['language'] in ('javascript', 'typescript', 'tsx'):
                return self.javascript(node)
            return proposal(reason='No supported runner was established for this language.')
        except (OSError, UnicodeError, ValueError, SyntaxError, configparser.Error, TypeError, AttributeError):
            return proposal(reason='Runner evidence is unreadable, invalid or ambiguous.')


def phpunit_filter(selectors):
    """A delimited regex on PHPUnit's full test name: any namespace segments, Class::method, any data set.

    Namespace segments exclude only the separator, ':' and spaces, so non-ASCII names match; /s lets a
    data set name span lines and \\z ends the name exactly."""
    names = '|'.join(selectors)
    return '/^(?:[^\\\\:\\s]+\\\\)*(?:' + names + ')(?: with data set .*)?\\z/s'


def aggregate(tests):
    """One argv only when every selected test has a command for the same runner."""
    if not tests:
        return proposal(reason='No tests were selected.')
    if any(not item.get('command') for item in tests):
        return proposal(reason='Some selected tests have no certain runner command.')
    runners = {item['command_runner'] for item in tests}
    if len(runners) != 1:
        return proposal(reason='Selected tests need different runners; no single argv is proposed.')
    runner = next(iter(runners))
    commands = [item['command'] for item in tests]
    unique = lambda values: list(dict.fromkeys(values))
    if runner in ('pytest', 'unittest'):
        argv = commands[0][:3] + unique(command[3] for command in commands)
    elif runner in ('jest', 'vitest'):
        argv = commands[0][:2] + unique(command[2] for command in commands)
    elif runner == 'phpunit':
        files = unique(command[4] for command in commands)
        if len(files) != 1:
            # Without a path PHPUnit runs only what phpunit.xml includes, so a selected file outside its suites would be skipped.
            return proposal(reason='PHPUnit takes one test path per run; the selected tests span several files, so run each proposed command.')
        selectors = unique(name for command in commands for name in re.search(r'\(\?:([^()]*)\)\(\?: with', command[3]).group(1).split('|'))
        argv = ['php', 'vendor/bin/phpunit', '--filter', phpunit_filter(selectors), files[0]]
    elif runner == 'go':
        packages = unique(command[2] for command in commands)
        names = unique(command[4][1:-1] for command in commands)
        argv = ['go', 'test', *packages, '-run', '^(' + '|'.join(names) + ')$']
    else:
        return proposal(reason='No single command is available for the selected runner.')
    return proposal(argv, runner=runner, scope='selected_tests',
                    evidence=unique(path for item in tests for path in item['command_evidence']))
