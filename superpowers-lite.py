#!/usr/bin/env python3
"""Superpowers Lite: manual workflow entry, with authorized skill chaining.

Python 3.11+, standard library. See superpowers_lite_README.md.
No bootstrap plugin, global prompt injection, or global skill-discovery toggle.
"""
from __future__ import annotations
import argparse
import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11 or newer is required for validated TOML configuration.')
import tomllib

VERSION = '2.0.0'
SCHEMA = 2
REPO = 'obra/superpowers'
DEFAULT_REF = '5bf4e78011075bcfc0dc295f0724994cd123ee71'
DEFAULT_SKILLS = ['brainstorming', 'writing-plans', 'subagent-driven-development']
NEVER_INSTALL = {'using-superpowers', 'diagnosing-superpowers'}
MARKER = '.superpowers-lite.json'
ENV_VAR = 'OPENCODE_DISABLE_CLAUDE_CODE_SKILLS'
HARNESSES = ('codex', 'opencode', 'claude')
NAME_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
OLD_BLOCK_RE = re.compile(r'\n?<!-- superpowers-lite:\w+:begin -->.*?<!-- superpowers-lite:\w+:end -->\n?', re.S)
UNSPECIFIED = object()


class SetupError(Exception):
    pass


def warn(message):
    print('NOTICE: ' + message)


def read(path):
    return Path(path).read_text(encoding='utf-8-sig')


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def linked(path):
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)()) or (
        os.name == 'nt' and path.exists()
        and os.path.normcase(os.path.realpath(path)) != os.path.normcase(str(path.parent.resolve() / path.name)))


def exists(path):
    return os.path.lexists(path)


def valid_name(name):
    return 1 <= len(name) <= 64 and bool(NAME_RE.fullmatch(name))


def fingerprint(path):
    """Content/type hash, including POSIX executability; never traverse links."""
    if not exists(path):
        return None
    digest = hashlib.sha256()

    def add(p, relative):
        digest.update(relative.encode('utf-8') + b'\0')
        if linked(p):
            digest.update(b'L' + os.readlink(p).encode('utf-8'))
        elif p.is_dir():
            digest.update(b'D')
            for child in sorted(p.iterdir(), key=lambda x: x.name):
                add(child, relative + '/' + child.name)
        elif p.is_file():
            digest.update(b'F' + p.read_bytes())
            if os.name != 'nt':
                digest.update(str(p.stat().st_mode & 0o111).encode())
        else:
            raise SetupError(f'Unsupported filesystem entry: {p}')
    add(path, '')
    return digest.hexdigest()


def remove(path):
    """Remove an explicitly planned path without following its link target."""
    if not exists(path):
        return
    if linked(path):
        try:
            path.unlink()
        except OSError:
            if os.name != 'nt':
                raise
            path.rmdir()
    elif path.is_dir():
        if path.resolve().parent != path.parent.resolve():
            raise SetupError(f'Refusing recursive removal outside parent: {path}')
        shutil.rmtree(path)
    else:
        path.unlink()


def strip_jsonc(text):
    """Mask comments/trailing commas without touching quoted string contents."""
    out = list(text)
    strings = set()
    i = 0
    while i < len(text):
        if text[i] == '"':
            start = i
            i += 1
            while i < len(text):
                if text[i] == '\\':
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            strings.update(range(start, i))
        elif text.startswith('//', i):
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            out[i:end] = ' ' * (end - i)
            i = end
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            if end < 0:
                raise SetupError('Unterminated JSONC comment')
            end += 2
            for j in range(i, end):
                if out[j] not in '\r\n':
                    out[j] = ' '
            i = end
        else:
            i += 1
    for i, char in enumerate(out):
        if char == ',' and i not in strings:
            j = i + 1
            while j < len(out) and out[j].isspace():
                j += 1
            if j < len(out) and out[j] in '}]':
                out[i] = ' '
    return ''.join(out)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SetupError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def load_json(text):
    try:
        return json.loads(strip_jsonc(text), object_pairs_hook=unique_object)
    except ValueError as exc:
        raise SetupError(f'Invalid JSON/JSONC: {exc}') from exc


def toml_value(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return 'nan' if math.isnan(value) else 'inf' if value == math.inf else '-inf' if value == -math.inf else repr(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, list):
        return '[' + ', '.join(toml_value(x) for x in value) + ']'
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + toml_value(v) for k, v in value.items()) + '}'
    raise SetupError(f'Cannot serialize TOML value: {type(value).__name__}')


def dump_toml(data):
    def serialize(value):
        return ''.join(json.dumps(k) + ' = ' + toml_value(v) + '\n' for k, v in value.items())
    text = serialize(data)
    if serialize(tomllib.loads(text)) != text:
        raise SetupError('TOML round-trip validation failed')
    return text.encode('utf-8')


class Layout:
    def __init__(self, home=None, opencode_config=None):
        self.home = Path(home).expanduser().resolve() if home else Path.home().resolve()
        def env(var, default):
            return Path(os.environ.get(var, str(default))).expanduser().absolute() if home is None else default
        self.codex = env('CODEX_HOME', self.home / '.codex')
        self.claude = env('CLAUDE_CONFIG_DIR', self.home / '.claude')
        self.opencode = env('OPENCODE_CONFIG_DIR', env('XDG_CONFIG_HOME', self.home / '.config') / 'opencode')
        self.store = env('XDG_DATA_HOME', self.home / '.local' / 'share') / 'superpowers-lite'
        self.manifest = self.store / 'manifest.json'
        self.config_override = opencode_config or (os.environ.get('OPENCODE_CONFIG') if home is None else None)
        self.isolated = home is not None

    def entry(self, harness, name):
        if harness == 'claude':
            return self.claude / 'commands' / (name + '.md')
        return (self.codex if harness == 'codex' else self.opencode) / 'skills' / name

    def config(self, harness):
        if harness == 'codex':
            return self.codex / 'config.toml'
        if self.config_override:
            return Path(self.config_override).expanduser().absolute()
        paths = [self.opencode / n for n in ('opencode.json', 'opencode.jsonc') if (self.opencode / n).exists()]
        if len(paths) > 1:
            raise SetupError('Both OpenCode config files exist; select the effective file with --opencode-config.')
        return paths[0] if paths else self.opencode / 'opencode.json'


def detected(layout):
    return {h for h in HARNESSES if shutil.which(h) or getattr(layout, h).exists()}


def opencode_version(override):
    if override:
        return int(override)
    exe = shutil.which('opencode')
    if exe:
        try:
            result = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=15)
            match = re.search(r'\b([12])\.(\d+)\.(\d+)\b', result.stdout)
            if match and result.returncode == 0:
                version = tuple(map(int, match.groups()))
                if version < ((1, 18, 30) if version[0] == 1 else (2, 0, 4)):
                    raise SetupError('OpenCode needs >=1.18.30 or >=2.0.4 for the tested manual-entry contract.')
                return version[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise SetupError('Cannot detect OpenCode version. Specify --opencode-version 1 or 2.')


def extract_archive(data, destination):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            parts = PurePosixPath(info.filename)
            if parts.is_absolute() or '..' in parts.parts or '\\' in info.filename or ':' in info.filename:
                raise SetupError(f'Unsafe archive entry: {info.filename}')
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SetupError(f'Archive symlinks unsupported: {info.filename}')
            path = destination.joinpath(*parts.parts)
            if info.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(info))
                if os.name != 'nt':
                    path.chmod(0o755 if mode & 0o111 else 0o644)
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise SetupError('Unexpected archive layout')
    return roots[0]


def fetch_source(ref, local, temporary):
    if local:
        root = Path(local).expanduser().resolve()
        commit = None
        if (root / '.git').exists():
            result = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
            if result.returncode == 0:
                commit = result.stdout.strip()
        provenance = {'kind': 'local', 'path': str(root), 'git_commit': commit}
    else:
        commit = ref
        if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
            url = f'https://api.github.com/repos/{REPO}/commits/{urllib.parse.quote(ref, safe="")}'
            request = urllib.request.Request(url, headers={'User-Agent': 'superpowers-lite/' + VERSION})
            with urllib.request.urlopen(request, timeout=45) as response:
                commit = json.load(response)['sha']
        if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
            raise SetupError('Source ref did not resolve to a commit SHA')
        print(f'Downloading {REPO}@{commit}')
        with urllib.request.urlopen(f'https://codeload.github.com/{REPO}/zip/{commit}', timeout=60) as response:
            data = response.read()
        root = extract_archive(data, temporary / 'archive')
        provenance = {'kind': 'github', 'ref': ref, 'git_commit': commit, 'archive_sha256': hashlib.sha256(data).hexdigest()}
    if not (root / 'skills' / 'brainstorming' / 'SKILL.md').is_file():
        raise SetupError(f'Not a Superpowers source tree: {root}')
    return root, provenance


def frontmatter_body(text, where):
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        raise SetupError(f'{where}: missing YAML frontmatter')
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
    if end is None:
        raise SetupError(f'{where}: unterminated YAML frontmatter')
    # Headers in the private tree are descriptive data, not harness config.
    # Refuse behavioral extensions instead of silently losing hooks/context.
    for line in lines[1:end]:
        if line.strip() and not line.startswith((' ', '\t', '#')):
            match = re.match(r'([a-zA-Z][\w/-]*):', line)
            if not match or match[1] not in {'name', 'description', 'metadata', 'license', 'compatibility'}:
                raise SetupError(f'{where}: unsupported upstream frontmatter; review before upgrading: {line.strip()}')
    body = ''.join(lines[end + 1:]).strip()
    if not body:
        raise SetupError(f'{where}: empty skill body')
    return body


def copy_library(source, destination):
    if linked(source):
        raise SetupError(f'Source symlink/junction is unsupported: {source}')
    for p in sorted(source.rglob('*')):
        if linked(p):
            raise SetupError(f'Source symlink/junction is unsupported: {p}')
        out = destination / p.relative_to(source)
        if p.is_dir():
            out.mkdir(parents=True, exist_ok=True)
        elif p.is_file():
            data = p.read_bytes()
            executable = data.startswith(b'#!') or bool(p.stat().st_mode & 0o111)
            if data.startswith(b'#!'):
                data = data.replace(b'\r\n', b'\n')
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            if os.name != 'nt':
                out.chmod(0o755 if executable else 0o644)


TOOL_NOTES = {
    'codex': 'Use the actual Codex tools exposed in this session. Spawn isolated children (fork_turns: "none") when supported. Model/effort overrides must follow the current spawn schema and allowlist. Use followup_task or the available resume/send tool for fix rounds. Use the native plan tool when available.',
    'opencode1': 'Use task with subagent_type "general" for subagents; todowrite for todos; read for files; bash for shell commands. Follow the actual exposed tool schemas.',
    'opencode2': 'Use subagent with agent "general" for subagents, read for files, and shell for shell commands. Keep a Markdown checklist when no todo tool is exposed. Follow the actual exposed tool schemas.',
    'claude': 'Use Agent (or Task on older versions) with general-purpose for subagents and the available native task/todo tools. Respect the actual tool schemas and nested-agent limits of this version.',
}


def build_bundle(root, temp):
    bundle = temp / 'bundle'
    bundle.mkdir()
    available = []
    for skill in sorted((root / 'skills').iterdir()):
        if skill.name in NEVER_INSTALL or not (skill / 'SKILL.md').is_file():
            continue
        if not valid_name(skill.name):
            raise SetupError(f'Invalid upstream skill directory: {skill.name}')
        frontmatter_body(read(skill / 'SKILL.md'), skill / 'SKILL.md')
        copy_library(skill, bundle / 'skills' / skill.name)
        available.append(skill.name)
    refs = root / 'skills' / 'using-superpowers' / 'references'
    if refs.is_dir():
        copy_library(refs, bundle / 'skills' / 'using-superpowers' / 'references')
    for name, text in TOOL_NOTES.items():
        p = bundle / 'tools' / (name + '.md')
        p.parent.mkdir(exist_ok=True)
        p.write_text(text + '\n', encoding='utf-8')
    if (root / 'LICENSE').is_file():
        shutil.copyfile(root / 'LICENSE', bundle / 'LICENSE')
    return bundle, fingerprint(bundle), available


def entry_text(harness, name, skill, bundle, major):
    header = ['---']
    if harness != 'claude':
        header.append('name: ' + json.dumps(name))
    header += ['description: ' + json.dumps(f'Manually start the Superpowers {skill} workflow, including required skill chaining.'), 'disable-model-invocation: true']
    if harness == 'opencode':
        header += ['metadata:', '  opencode/autoinvoke: ' + ('"false"' if major == 1 else 'false')]
    header += ['---']
    tool = 'opencode' + str(major) if harness == 'opencode' else harness
    base = bundle / 'skills'
    paragraphs = [
        '\n'.join(header), f'<!-- superpowers-lite:{VERSION}:{harness}:{name} -->',
        f'The user explicitly started `{skill}`. Read `{(base / skill / "SKILL.md").as_posix()}` completely and follow its body.',
        f'Before dispatching subagents or tracking tasks, read `{(bundle / "tools" / (tool + ".md")).as_posix()}`. Actual tool schemas take precedence over examples in upstream references.',
        f'This invocation authorizes chaining into other Superpowers skills required by this workflow. For `superpowers:<skill>` or an upstream skill name, read `{base.as_posix()}/<skill>/SKILL.md` directly and follow its body. Do not invoke another skill tool or ask the user to invoke each dependency.',
        'Treat upstream frontmatter as descriptive metadata, not authorization to auto-start workflows. Do not load using-superpowers or diagnosing-superpowers.',
        f'Resolve supporting files and scripts relative to the upstream skill directory being read: start with `{(base / skill).as_posix()}/`. These are the canonical paths, regardless of the entry-point directory reported by the harness. Preserve the user\'s project working directory when running scripts; use absolute script paths.',
        'On Windows run shell helpers with Git Bash or WSL; on Linux use Bash. When using WSL, translate Windows paths with wslpath and use the Linux project path. Git workflows need Git and standard shell utilities. The optional brainstorming visual server needs Node. Check prerequisites before using the relevant feature.',
        'Keep this authorization scoped to the requested workflow. Do not start unrelated Superpowers workflows on later requests without another explicit invocation.',
    ]
    return ('\n\n'.join(paragraphs) + '\n').encode('utf-8')


def get_at(data, path):
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def set_at(data, path, present, value):
    current = data
    for key in path[:-1]:
        current = current[key]
    if present:
        current[path[-1]] = copy.deepcopy(value)
    else:
        current.pop(path[-1], None)


class ConfigEdit:
    def __init__(self, path):
        self.format = 'toml' if path.suffix == '.toml' else 'json'
        self.path = path.resolve() if linked(path) else path.absolute()
        self.before = fingerprint(self.path)
        self.original = self.path.read_bytes() if self.path.exists() else None
        if fingerprint(self.path) != self.before:
            raise SetupError(f'Configuration changed while reading: {self.path}')
        self.created = self.original is None
        text = self.original.decode('utf-8-sig') if self.original else ''
        try:
            self.data = tomllib.loads(text) if self.format == 'toml' else load_json(text or '{}')
        except (ValueError, UnicodeError) as exc:
            raise SetupError(f'{path}: {exc}') from exc
        if not isinstance(self.data, dict):
            raise SetupError(f'{path}: configuration must be an object/table')
        self.initial = copy.deepcopy(self.data)
        self.ops = []
        self.conflicts = set()

    def put(self, path, value):
        present, old = get_at(self.data, path)
        if present and old == value:
            return
        if tuple(path) in self.conflicts:
            raise SetupError(f'User-modified owned setting: {self.path}: {".".join(path)}. Reconcile it before updating; uninstall preserves the user value.')
        self.ops.append({'path': path, 'before_exists': present, 'before': copy.deepcopy(old), 'after': copy.deepcopy(value)})
        set_at(self.data, path, True, value)

    def table(self, path, shorthand=False):
        present, value = get_at(self.data, path)
        if not present:
            self.put(path, {})
        elif isinstance(value, str) and shorthand and value in ('allow', 'ask', 'deny'):
            self.put(path, {'*': value})
        elif not isinstance(value, dict):
            raise SetupError(f'{self.path}: {".".join(path)} must be a table/object or permission shorthand')

    def order_last(self, path, names):
        present, table = get_at(self.data, path)
        if not present or not isinstance(table, dict):
            raise SetupError(f'Cannot order non-table setting: {path}')
        before = list(table)
        after = [key for key in before if key not in names] + [key for key in names if key in table]
        if before == after:
            return
        if tuple(path) in self.conflicts:
            raise SetupError(f'User-modified owned setting order: {self.path}: {".".join(path)}')
        self.ops.append({'kind': 'order', 'path': path, 'before': before, 'after': after})
        set_at(self.data, path, True, {key: table[key] for key in after})

    def release(self, record):
        self.created = record.get('created', False)
        for op in reversed(record['ops']):
            present, current = get_at(self.data, op['path'])
            if op.get('kind') == 'order':
                known = set(op['after'])
                if present and isinstance(current, dict) and [k for k in current if k in known] == [k for k in op['after'] if k in current]:
                    restored = iter(k for k in op['before'] if k in current)
                    keys = [next(restored) if k in known else k for k in current]
                    set_at(self.data, op['path'], True, {k: current[k] for k in keys})
                else:
                    warn(f'Preserving user-modified setting order {self.path}: {".".join(op["path"])}')
                    self.conflicts.add(tuple(op['path']))
                continue
            if present and current == op['after']:
                set_at(self.data, op['path'], op['before_exists'], op['before'])
            else:
                warn(f'Preserving user-modified setting {self.path}: {".".join(op["path"])}')
                self.conflicts.add(tuple(op['path']))

    def result(self):
        if self.created and not self.data:
            return None
        if self.data == self.initial and (self.format == 'toml' or json_bytes(self.data) == json_bytes(self.initial)):
            return self.original
        return dump_toml(self.data) if self.format == 'toml' else json_bytes(self.data)


class InstallLock:
    """Serialize planning and application, including reads of ownership state."""
    def __init__(self, store):
        self.store = store
        self.path = store / 'install.lock'
        self.pending = False
        self.active = False
        self.created_dirs = []

    def __enter__(self):
        parent = self.store
        while not parent.exists():
            self.created_dirs.append(parent)
            parent = parent.parent
        self.store.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise SetupError(f'Install lock exists: {self.path}. Ensure no installer is running before --recover.') from exc
        self.active = True
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(json_bytes({'pid': os.getpid(), 'transaction': None}))
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise
        return self

    def __exit__(self, *exc):
        if self.active and not self.pending:
            self.path.unlink(missing_ok=True)
            self.active = False
            for directory in self.created_dirs:
                with contextlib.suppress(OSError):
                    directory.rmdir()


class Plan:
    def __init__(self, store):
        self.store = store
        self.changes = {}

    def add(self, path, content, label='', expected=UNSPECIFIED):
        path = path.absolute()
        if path in self.changes:
            raise SetupError(f'Overlapping planned writes: {path}')
        before = fingerprint(path)
        if expected is not UNSPECIFIED and before != expected:
            raise SetupError(f'Changed since reading configuration: {path}')
        if isinstance(content, bytes) and path.is_file() and not linked(path) and path.read_bytes() == content:
            return
        if content is None and before is None:
            return
        if isinstance(content, Path) and before == fingerprint(content):
            return
        self.changes[path] = {'content': content, 'before': before, 'label': label}

    def display(self):
        for path, change in self.changes.items():
            print(f'  {"remove" if change["content"] is None else "write"} {path} ({change["label"]})')

    def apply(self, fail_after=None, lock=None):
        if lock is None:
            with InstallLock(self.store) as acquired:
                return self.apply(fail_after=fail_after, lock=acquired)
        if not lock.active or lock.store != self.store:
            raise SetupError('Plan requires the active lock for its installation store.')
        if not self.changes:
            print('Already up to date.')
            return None
        ident = uuid.uuid4().hex
        tx = self.store / 'transactions' / ident
        tx.mkdir(parents=True, mode=0o700)
        lock.path.write_bytes(json_bytes({'pid': os.getpid(), 'transaction': ident}))
        lock.pending = True
        journal = {'schema': SCHEMA, 'status': 'preparing', 'items': [], 'created_dirs': []}
        def save():
            tmp = tx / 'journal.tmp'
            tmp.write_bytes(json_bytes(journal))
            os.replace(tmp, tx / 'journal.json')
        try:
            for path, change in self.changes.items():
                if fingerprint(path) != change['before']:
                    raise SetupError(f'Changed since planning: {path}')
                missing, parent = [], path.parent
                while not parent.exists():
                    missing.append(str(parent))
                    parent = parent.parent
                journal['created_dirs'].extend(reversed(missing))
                index = len(journal['items'])
                item = {'path': str(path), 'backup': str(path.parent / f'.superpowers-lite-old-{ident}-{index}'),
                        'stage': str(path.parent / f'.superpowers-lite-new-{ident}-{index}'),
                        'before': change['before'], 'after': None, 'new': change['content'] is not None, 'started': False}
                journal['items'].append(item)
                save()
                path.parent.mkdir(parents=True, exist_ok=True)
                stage, content = Path(item['stage']), change['content']
                if isinstance(content, Path):
                    shutil.copytree(content, stage)
                elif isinstance(content, bytes):
                    stage.write_bytes(content)
                    stage.chmod(stat.S_IMODE(path.stat().st_mode) if path.is_file() else 0o600)
                item['after'] = fingerprint(stage)
            journal['status'] = 'applying'
            save()
            for count, item in enumerate(journal['items'], 1):
                path = Path(item['path'])
                if fingerprint(path) != item['before']:
                    raise SetupError(f'Changed during staging: {path}')
                item['started'] = True
                save()
                if exists(path):
                    os.replace(path, item['backup'])
                if item['new']:
                    os.replace(item['stage'], path)
                if fail_after is not None and count == fail_after:
                    raise OSError('Injected transaction failure')
            journal['status'] = 'committed'
            save()
        except BaseException:
            rollback(journal)
            journal['status'] = 'rolled-back'
            save()
            raise
        finally:
            if journal['status'] == 'rolled-back':
                lock.pending = False
        archive_backups(tx, journal)
        lock.pending = False
        print(f'Committed. Backups/journal: {tx}')
        return tx


def rollback(journal):
    for item in reversed(journal['items']):
        path, backup, stage = (Path(item[k]) for k in ('path', 'backup', 'stage'))
        if exists(backup):
            if exists(path) and fingerprint(path) != item['after']:
                raise SetupError(f'Concurrent edit blocks rollback: {path}; recover from {backup}')
            remove(path)
            os.replace(backup, path)
        elif item['before'] is None and item['started'] and exists(path):
            if fingerprint(path) != item['after']:
                raise SetupError(f'Concurrent edit blocks rollback: {path}')
            remove(path)
        remove(stage)
    for directory in reversed(journal.get('created_dirs', [])):
        with contextlib.suppress(OSError):
            Path(directory).rmdir()


def archive_backups(tx, journal):
    for i, item in enumerate(journal['items']):
        backup = Path(item['backup'])
        if exists(backup):
            dest = tx / 'backups' / str(i)
            dest.parent.mkdir(exist_ok=True)
            if linked(backup):
                dest.with_suffix('.link.json').write_bytes(json_bytes({'target': os.readlink(backup), 'path': item['path']}))
                remove(backup)
            else:
                shutil.move(str(backup), dest)


def recover(store):
    lock = store / 'install.lock'
    if not lock.exists():
        raise SetupError('No interrupted transaction lock exists.')
    info = load_json(read(lock))
    ident = info.get('transaction', '')
    if not re.fullmatch('[a-f0-9]{32}', ident):
        raise SetupError('Invalid transaction lock')
    tx = store / 'transactions' / ident
    journal = load_json(read(tx / 'journal.json'))
    if journal.get('schema') != SCHEMA:
        raise SetupError('Unsupported recovery journal')
    for i, item in enumerate(journal['items']):
        path = Path(item['path'])
        for key, prefix in [('backup', 'old'), ('stage', 'new')]:
            if Path(item[key]) != path.parent / f'.superpowers-lite-{prefix}-{ident}-{i}' or not path.is_absolute():
                raise SetupError('Invalid recovery journal path')
    if journal['status'] == 'committed':
        archive_backups(tx, journal)
    elif journal['status'] != 'rolled-back':
        rollback(journal)
        journal['status'] = 'rolled-back'
        (tx / 'journal.json').write_bytes(json_bytes(journal))
    lock.unlink()
    print(f'Recovered transaction {ident}.')


def load_manifest(layout):
    if not layout.manifest.exists():
        return {'schema': SCHEMA, 'installer': VERSION, 'source': REPO, 'harnesses': {}}
    state = load_json(read(layout.manifest))
    if not isinstance(state, dict) or state.get('schema') != SCHEMA or state.get('source') != REPO or not isinstance(state.get('harnesses'), dict):
        raise SetupError('Unrecognized ownership manifest; refusing automatic replacement.')
    for harness, record in state['harnesses'].items():
        if (harness not in HARNESSES or not isinstance(record, dict) or not isinstance(record.get('entries'), dict)
                or not isinstance(record.get('bundle'), str) or not re.fullmatch('[a-f0-9]{64}', record['bundle'])
                or not isinstance(record.get('configs'), dict) or not isinstance(record.get('tuning'), dict)):
            raise SetupError('Invalid harness ownership record')
        for name, entry in record['entries'].items():
            if (not isinstance(entry, dict) or not isinstance(entry.get('path'), str)
                    or not isinstance(entry.get('digest'), str) or not re.fullmatch('[a-f0-9]{64}', entry['digest'])):
                raise SetupError('Invalid entry ownership record')
            if not valid_name(name) or Path(entry['path']) != layout.entry(harness, name).absolute():
                raise SetupError('Installation paths changed; uninstall using the original home/config paths first.')
        for path, config in record['configs'].items():
            if not Path(path).is_absolute() or not isinstance(config, dict) or not isinstance(config.get('ops'), list):
                raise SetupError('Invalid configuration ownership record')
            for op in config['ops']:
                if isinstance(op, dict) and op.get('kind') == 'order':
                    if (not isinstance(op.get('path'), list) or not op['path']
                            or not all(isinstance(k, str) for k in op['path'])
                            or any(not isinstance(op.get(k), list) or not all(isinstance(v, str) for v in op[k]) for k in ('before', 'after'))
                            or len(set(op['before'])) != len(op['before'])
                            or len(set(op['after'])) != len(op['after'])
                            or set(op['before']) != set(op['after'])):
                        raise SetupError('Invalid configuration ordering operation')
                    continue
                if (not isinstance(op, dict) or not isinstance(op.get('path'), list) or not op['path']
                        or not all(isinstance(k, str) for k in op['path'])
                        or not isinstance(op.get('before_exists'), bool) or 'before' not in op or 'after' not in op):
                    raise SetupError('Invalid configuration ownership operation')
    return state


def owned_marker(path):
    if not (path / MARKER).is_file():
        return False
    try:
        data = load_json(read(path / MARKER))
    except (SetupError, UnicodeError):
        return False
    return (isinstance(data, dict) and data.get('source') == REPO and 'installed' in data
            and ('upstream_name' not in data or valid_name(data['upstream_name'])))


def legacy_entries(layout):
    result = []
    root = layout.home / '.agents' / 'skills'
    if root.is_dir():
        result += [p for p in root.iterdir() if not linked(p) and owned_marker(p)]
    claude = layout.claude / 'skills'
    if claude.is_dir():
        for p in claude.iterdir():
            if linked(p) and any(p.resolve() == target.resolve() for target in result):
                result.append(p)
            elif not linked(p) and owned_marker(p):
                result.append(p)
    tools = layout.home / '.agents' / 'superpowers-tools'
    if owned_marker(tools):
        result.append(tools)
    return result


def plugin_conflicts(layout, harnesses):
    issues = []
    projects = [] if layout.isolated else [Path.cwd(), *Path.cwd().parents]
    configs = []
    if 'opencode' in harnesses:
        dirs = [layout.opencode] + [p / '.opencode' for p in projects]
        if not layout.isolated:
            dirs.append(Path(os.environ.get('XDG_CONFIG_HOME', str(layout.home / '.config'))) / 'opencode')
        for directory in dict.fromkeys(dirs):
            for sub in ('plugins', 'plugin'):
                if (directory / sub).is_dir():
                    issues.extend(str(p) for p in (directory / sub).iterdir() if 'superpowers' in p.name.lower())
            configs.extend(directory / n for n in ('opencode.json', 'opencode.jsonc'))
        configs += [p / n for p in projects for n in ('opencode.json', 'opencode.jsonc')]
        if layout.config_override:
            configs.append(Path(layout.config_override))
        if not layout.isolated and 'superpowers' in os.environ.get('OPENCODE_CONFIG_CONTENT', '').lower():
            issues.append('OPENCODE_CONFIG_CONTENT references superpowers')
    if 'claude' in harnesses:
        configs += [d / n for d in [layout.claude] + [p / '.claude' for p in projects]
                    for n in ('settings.json', 'settings.local.json')]
    for path in dict.fromkeys(configs):
        if not path.is_file():
            continue
        data = load_json(read(path))
        if not isinstance(data, dict):
            raise SetupError(f'Invalid configuration object: {path}')
        for key in ('plugin', 'plugins'):
            if 'superpowers' in json.dumps(data.get(key, [])).lower():
                issues.append(f'{path}: {key}')
        if any('superpowers' in k.lower() and v for k, v in data.get('enabledPlugins', {}).items()):
            issues.append(f'{path}: enabledPlugins')
        if 'superpowers' in json.dumps(data.get('hooks', {})).lower():
            issues.append(f'{path}: hooks')
    if 'codex' in harnesses:
        for path in [layout.codex / 'config.toml'] + [p / '.codex' / 'config.toml' for p in projects]:
            if path.is_file():
                data = tomllib.loads(read(path))
                for name, value in data.get('plugins', {}).items():
                    if 'superpowers' in name.lower() and value is not False and (not isinstance(value, dict) or value.get('enabled', True)):
                        issues.append(f'{path}: plugin {name}')
                if 'superpowers' in json.dumps(data.get('hooks', {}), default=str).lower():
                    issues.append(f'{path}: hooks')
        path = layout.codex / 'hooks.json'
        if path.is_file() and 'superpowers' in read(path).lower():
            issues.append(str(path))
    for path in (layout.home / '.agents' / 'skills' / 'superpowers', layout.opencode / 'skills' / 'superpowers'):
        if exists(path) and not owned_marker(path):
            issues.append(f'{path}: full library remains discoverable')
    instructions = []
    if 'codex' in harnesses:
        instructions += [layout.codex / n for n in ('AGENTS.md', 'AGENTS.override.md')]
    if 'claude' in harnesses:
        instructions += [layout.claude / 'CLAUDE.md']
    if 'opencode' in harnesses:
        instructions += [layout.opencode / 'AGENTS.md']
    instructions += [p / n for p in projects for n in ('AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md', '.claude/CLAUDE.md')]
    for path in dict.fromkeys(instructions):
        if path.is_file():
            text = OLD_BLOCK_RE.sub('', read(path))
            if re.search(r'using-superpowers|superpowers-codex\s+bootstrap|<superpowers', text, re.I):
                issues.append(f'{path}: unmarked Superpowers bootstrap instructions')
    return sorted(set(issues))


def cleanup_old_instructions(layout, harnesses, plan):
    paths = []
    if 'codex' in harnesses:
        paths += [layout.codex / 'AGENTS.md', layout.codex / 'AGENTS.override.md']
    if 'opencode' in harnesses:
        paths += [layout.opencode / 'AGENTS.md']
    if 'claude' in harnesses:
        paths += [layout.claude / 'CLAUDE.md']
    for path in paths:
        if path.is_file():
            old = read(path)
            new = OLD_BLOCK_RE.sub('\n', old)
            if new != old:
                plan.add(path.resolve(), new.encode('utf-8'), 'remove legacy marked block')
    if 'opencode' in harnesses:
        pattern = re.compile(r'\# superpowers-lite:env:begin.*?\# superpowers-lite:env:end\r?\n?', re.S)
        for name in ('.profile', '.bashrc', '.zshenv', '.zshrc'):
            path = layout.home / name
            if path.is_file():
                old = read(path)
                new = pattern.sub('', old)
                if new != old:
                    plan.add(path.resolve(), new.encode('utf-8'), 'remove owned environment block')
        fish = layout.home / '.config' / 'fish' / 'conf.d' / 'superpowers-lite.fish'
        if fish.is_file() and read(fish).strip() == f'set -gx {ENV_VAR} 1':
            plan.add(fish, None, 'remove owned environment file')
        if os.environ.get(ENV_VAR):
            warn(f'{ENV_VAR} is set in this process. This installer no longer sets it. Restart after removing unmarked launcher settings; old Windows setx values have no ownership record and are not silently erased.')


def check_collision(layout, harness, name, target, removed):
    roots = [layout.home / '.agents' / 'skills', layout.claude / 'skills', layout.opencode / 'skills']
    if not layout.isolated:
        for p in [Path.cwd(), *Path.cwd().parents]:
            roots += [p / '.agents' / 'skills', p / '.claude' / 'skills', p / '.opencode' / 'skills']
    for root in dict.fromkeys(roots):
        path = root / name
        if path.absolute() == target.absolute() or path in removed:
            continue
        # OpenCode's own entry does not shadow a Claude command. Claude does
        # not discover OpenCode skills, but an actual Claude skill would shadow it.
        relevant = harness == 'opencode' or (harness == 'claude' and root == layout.claude / 'skills') or (harness == 'codex' and root == layout.home / '.agents' / 'skills')
        if exists(path) and relevant:
            raise SetupError(f'Conflicting discoverable skill: {path}. Remove/migrate it; no global discovery-disable workaround is used.')


def prepare(args, layout, temp):
    old = load_manifest(layout)
    state = copy.deepcopy(old)
    selected = set(args.only.split(',')) if args.only else (set(old['harnesses']) if args.uninstall else detected(layout))
    if not selected or not selected <= set(HARNESSES):
        raise SetupError('Select installed harnesses or explicitly pass --only codex,opencode,claude.')
    if args.uninstall:
        selected &= set(old['harnesses'])
        if not selected:
            raise SetupError('No selected harness has an owned installation.')
    conflicts = plugin_conflicts(layout, selected)
    if conflicts and not args.uninstall:
        raise SetupError('Existing full bootstrap/plugin detected. Disable it in its harness first:\n  ' + '\n  '.join(conflicts))
    plan = Plan(layout.store)
    legacy = legacy_entries(layout)
    if legacy:
        if not args.migrate_legacy or args.uninstall:
            raise SetupError('Legacy shared install found. Use --migrate-legacy with every affected harness.')
        affected = set(HARNESSES) if layout.isolated else detected(layout) | ({'claude'} if any(p.parent == layout.claude / 'skills' for p in legacy) else set())
        if not affected <= selected:
            raise SetupError('Legacy migration must include: ' + ','.join(sorted(affected)))
        for path in legacy:
            plan.add(path, None, 'migrate verified legacy entry')
    if not args.uninstall:
        wanted = [s.strip() for s in args.skills.split(',') if s.strip()]
        if not wanted:
            raise SetupError('--skills cannot be empty; use --uninstall explicitly.')
        root, provenance = fetch_source(args.ref, args.source, temp)
        bundle, digest, available = build_bundle(root, temp)
        if any(s not in available or not valid_name(args.name_prefix + s) for s in wanted):
            raise SetupError('Unknown skill or invalid final name (1–64 lowercase kebab-case characters). Available: ' + ', '.join(available))
        wanted = list(dict.fromkeys(wanted))
        destination = layout.store / 'bundles' / digest
        if destination.exists() and fingerprint(destination) != digest:
            raise SetupError(f'Private library was modified: {destination}')
        plan.add(destination, bundle, 'private upstream library, original paths preserved')
        major = opencode_version(args.opencode_version) if 'opencode' in selected else None
    edits = {}
    def config(path):
        key = str(path.absolute())
        if key not in edits:
            edits[key] = ConfigEdit(path)
        return edits[key]
    for harness in sorted(selected):
        prior = old['harnesses'].get(harness, {})
        for path, record in prior.get('configs', {}).items():
            config(Path(path)).release(record)
        next_names = [] if args.uninstall else [args.name_prefix + s for s in wanted]
        for name, record in prior.get('entries', {}).items():
            path = Path(record['path'])
            if fingerprint(path) not in (None, record['digest']):
                raise SetupError(f'User-modified owned entry: {path}; preserve edits before replacement/uninstall.')
            if name not in next_names:
                plan.add(path, None, f'remove obsolete {harness} entry')
        if args.uninstall:
            state['harnesses'].pop(harness, None)
            continue
        entries = {}
        for skill, name in zip(wanted, next_names):
            path = layout.entry(harness, name)
            if exists(path) and name not in prior.get('entries', {}):
                raise SetupError(f'Refusing to overwrite unowned entry: {path}')
            check_collision(layout, harness, name, path, legacy)
            content = entry_text(harness, name, skill, destination, major)
            if harness == 'claude':
                stage = temp / ('claude-' + name + '.md')
                stage.write_bytes(content)
                stage.chmod(0o600)
                plan.add(path, content, 'manual command-format skill, no duplicate discovery root')
            else:
                stage = temp / harness / name
                stage.mkdir(parents=True)
                (stage / 'SKILL.md').write_bytes(content)
                (stage / MARKER).write_bytes(json_bytes({'source': REPO, 'schema': SCHEMA, 'installer': VERSION, 'harness': harness, 'name': name}))
                if harness == 'codex':
                    (stage / 'agents').mkdir()
                    (stage / 'agents' / 'openai.yaml').write_text('policy:\n  allow_implicit_invocation: false\n', encoding='utf-8')
                plan.add(path, stage, 'manual skill entry')
            entries[name] = {'path': str(path.absolute()), 'digest': fingerprint(stage), 'upstream_name': skill}
        tuning = copy.deepcopy(prior.get('tuning', {}))
        if harness == 'opencode':
            edit = config(layout.config(harness))
            if args.migrate_legacy:
                names = {p.name for p in legacy if (p / 'SKILL.md').is_file()}
                present, rules = get_at(edit.data, ['permission', 'skill'])
                if present and isinstance(rules, dict):
                    for name in names:
                        if rules.get(name) == 'ask':
                            rules.pop(name)
            if major == 1:
                edit.table(['permission'], shorthand=True)
                edit.table(['permission', 'skill'], shorthand=True)
                for name in next_names:
                    edit.put(['permission', 'skill', name], 'deny')
                edit.order_last(['permission', 'skill'], next_names)
                edit.order_last(['permission'], ['skill'])
            elif 'permission' in edit.data:
                warn(f'{edit.path} still contains unrelated V1 permission settings; V2 uses permissions.')
        if harness == 'codex':
            edit = config(layout.config(harness))
            if args.enable_codex_multi_agent:
                tuning['enable_multi_agent'] = True
            if args.codex_subagent_model:
                tuning['model'] = args.codex_subagent_model
            if args.codex_subagent_effort:
                tuning['effort'] = args.codex_subagent_effort
            if tuning.get('enable_multi_agent'):
                edit.table(['features'])
                edit.put(['features', 'multi_agent'], True)
                edit.table(['agents'])
                edit.put(['agents', 'enabled'], True)
            for flag, key in [('model', 'default_subagent_model'), ('effort', 'default_subagent_reasoning_effort')]:
                if flag in tuning:
                    edit.table(['agents'])
                    edit.put(['agents', key], tuning[flag])
            if any(get_at(edit.data, p) == (True, False) for p in (['features', 'multi_agent'], ['agents', 'enabled'])):
                warn('Codex multi-agent is disabled; use --enable-codex-multi-agent to change that preference.')
        state['harnesses'][harness] = {'entries': entries, 'bundle': digest, 'provenance': provenance, 'tuning': tuning,
                                      'opencode_major': major if harness == 'opencode' else None, 'configs': {}}
        for key, edit in edits.items():
            if edit.ops:
                state['harnesses'][harness]['configs'][key] = {'ops': copy.deepcopy(edit.ops), 'created': edit.created}
                edit.ops.clear()
    cleanup_old_instructions(layout, selected, plan)
    for edit in edits.values():
        result = edit.result()
        if result != edit.original:
            warn(f'Reformatting {edit.path}; original comments remain in the transaction backup. Values are parsed and validated.')
            plan.add(edit.path, result, 'validated config and reversible owned settings', expected=edit.before)
    state['installer'] = VERSION
    plan.add(layout.manifest, json_bytes(state), 'per-harness ownership and exact source provenance')
    if args.prune:
        used = {r['bundle'] for r in state['harnesses'].values()}
        bundles = layout.store / 'bundles'
        if bundles.is_dir():
            for p in bundles.iterdir():
                if re.fullmatch('[a-f0-9]{64}', p.name) and p.name not in used:
                    if linked(p) or fingerprint(p) != p.name:
                        raise SetupError(f'Refusing to prune modified/unowned bundle: {p}')
                    plan.add(p, None, 'prune unreferenced private library')
    return plan, state, selected


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', help='local upstream checkout; exact content fingerprint recorded')
    ap.add_argument('--ref', default=DEFAULT_REF, help='commit/tag/branch; default is an audited pinned SHA')
    ap.add_argument('--skills', default=','.join(DEFAULT_SKILLS), help='public entry skills; dependency library remains private')
    ap.add_argument('--name-prefix', default='superpowers-')
    ap.add_argument('--only', help='codex,opencode,claude; explicit selection also works before a CLI exists')
    ap.add_argument('--home', help='isolated install home; ignores harness path environment overrides')
    ap.add_argument('--opencode-config', help='explicit effective OpenCode config file')
    ap.add_argument('--opencode-version', choices=['1', '2'], help='override detection; supports v1>=1.18.30 or v2>=2.0.4')
    ap.add_argument('--no-opencode-v1', dest='opencode_version', action='store_const', const='2', help='legacy alias for --opencode-version 2')
    ap.add_argument('--enable-codex-multi-agent', action='store_true', help='explicitly enable global Codex multi-agent settings')
    ap.add_argument('--codex-subagent-model', help='explicit global model default, restored on uninstall')
    ap.add_argument('--codex-subagent-effort', choices=['minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'], help='explicit global effort, works without model option')
    ap.add_argument('--migrate-legacy', action='store_true', help='migrate verified old shared layout for all affected harnesses')
    ap.add_argument('--uninstall', action='store_true', help='remove selected owned entries and restore owned settings')
    ap.add_argument('--prune', action='store_true', help='also remove unused private libraries; close old sessions first')
    ap.add_argument('--recover', action='store_true', help='recover interrupted transaction; ensure no installer is running')
    ap.add_argument('--audit', action='store_true', help='report known full-bootstrap conflicts without installing')
    ap.add_argument('--dry-run', action='store_true', help='build and validate the entire plan without target writes')
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    if args.only:
        names = [x.strip() for x in args.only.split(',')]
        if not names or any(x not in HARNESSES for x in names):
            raise SetupError('--only accepts codex,opencode,claude; empty/unknown entries are errors.')
        args.only = ','.join(dict.fromkeys(names))
    layout = Layout(args.home, args.opencode_config)
    if args.recover:
        if args.dry_run:
            raise SetupError('--recover cannot be combined with --dry-run')
        recover(layout.store)
        return 0
    if args.audit:
        conflicts = plugin_conflicts(layout, set(args.only.split(',')) if args.only else set(HARNESSES))
        for item in conflicts:
            print('CONFLICT: ' + item)
        print('No known bootstrap entry points found.' if not conflicts else 'Disable full installs before installing Lite.')
        return int(bool(conflicts))
    with InstallLock(layout.store) as lock, tempfile.TemporaryDirectory(prefix='superpowers-lite-') as temporary:
        plan, state, selected = prepare(args, layout, Path(temporary))
        print('Validated plan:')
        plan.display()
        if args.dry_run:
            print('Dry run: no installation targets changed.')
        else:
            plan.apply(lock=lock)
        if not args.uninstall:
            for harness in sorted(selected):
                prefix = '$' if harness == 'codex' else '/'
                print(harness + ': ' + '  '.join(prefix + n for n in state['harnesses'][harness]['entries']))
        print('Restart affected harness sessions. No bootstrap hooks or global prompt instructions were installed.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (SetupError, OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)
