from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux installer')
MARKER = '# Managed by AppRestore install-linux.sh'

FAKE_PYTHON = '''#!/usr/bin/python3
import os, shutil, subprocess, sys
from pathlib import Path
args = [a for a in sys.argv[1:] if a != '-I']
if args[:2] == ['-m', 'venv']:
    dest = Path(args[2]) / 'bin'
    dest.mkdir(parents=True)
    shutil.copyfile(__file__, dest / 'python')
    (dest / 'python').chmod(0o700)
elif args[:2] == ['-m', 'pip']:
    if os.environ.get('FAIL_PIP') and 'check' in args:
        sys.exit(3)
elif args[:2] == ['-m', 'apprestore_gui'] and '--self-test' in args:
    if os.environ.get('COLLIDE_GUI'):
        (Path(os.environ['HOME'])/'.local/bin/apprestore-gui').write_text('unrelated command')
    print('{"ok": true}')
elif args and args[0] in ('-', '-c'):
    sys.exit(subprocess.call([sys.executable, '-I', *args]))
else:
    print(Path(__file__).resolve())
'''


@pytest.fixture
def install_world(tmp_path: Path):
    if os.geteuid() == 0:
        pytest.skip('installer intentionally refuses root')
    for tool in ('git', 'curl', 'tar', 'flock', 'cc'):
        if shutil.which(tool) is None:
            pytest.skip(f'{tool} is unavailable')
    source = tmp_path/'checkout with spaces'
    source.mkdir()
    original = Path(__file__).resolve().parents[1]/'install-linux.sh'
    shutil.copyfile(original, source/'install-linux.sh')
    for name in ('apprestore_core', 'apprestore_gui', 'requirements/wheels', 'packaging'):
        (source/name).mkdir(parents=True)
    for name in ('pyproject.toml', 'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                 'requirements/runtime.lock', 'requirements/build.lock'):
        (source/name).touch()
    (source/'packaging/bootstrap-ipatool.sh').write_text('''#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/bin"
printf '#!/bin/bash\\necho ipatool-test\\n' > "$root/bin/ipatool"
chmod 700 "$root/bin/ipatool"
printf 'MIT' > "$root/bin/ipatool-LICENSE.txt"
''')
    python = tmp_path/'fake-python'
    python.write_text(FAKE_PYTHON)
    python.chmod(0o700)
    home = tmp_path/'home with spaces'
    home.mkdir()
    tools = tmp_path/'tools'
    tools.mkdir()
    (tools/'usbmuxd').write_text('#!/bin/sh\nexit 0\n')
    (tools/'usbmuxd').chmod(0o700)
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home/'data'),
               XDG_CACHE_HOME=str(home/'cache'), PATH=f'{tools}:/usr/bin:/bin:/usr/sbin:/sbin')
    return source, python, home, env


def run_install(world, *flags: str, **extra: str):
    source, python, _home, env = world
    return subprocess.run(['bash', str(source/'install-linux.sh'), '--python', str(python), *flags],
                          env=dict(env, **extra), capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('gui', [False, True])
def test_install_publishes_private_launchers_and_keeps_runtime_path(install_world, gui):
    _source, _python, home, env = install_world
    result = run_install(install_world, *(['--gui'] if gui else []))
    assert result.returncode == 0, result.stderr
    commands = ['apprestore', 'apprestore-gui'] if gui else ['apprestore']
    for name in commands:
        launcher = home/'.local/bin'/name
        assert launcher.stat().st_mode & 0o777 == 0o700
        run = subprocess.run([str(launcher), '--version'], env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert Path(run.stdout.strip()).is_file()
    assert len(list((home/'data/AppRestore/runtimes').glob('linux.*'))) == 1
    assert (home/'data/AppRestore').stat().st_mode & 0o777 == 0o700
    assert not (home/'.ipatool').exists()


def test_failed_update_keeps_previous_working_command_and_removes_new_runtime(install_world):
    assert run_install(install_world).returncode == 0
    _source, _python, home, env = install_world
    launcher = home/'.local/bin/apprestore'
    before = launcher.read_bytes()
    result = run_install(install_world, FAIL_PIP='1')
    assert result.returncode != 0
    assert launcher.read_bytes() == before
    assert len(list((home/'data/AppRestore/runtimes').glob('linux.*'))) == 1
    assert subprocess.run([str(launcher), '--version'], env=env, capture_output=True).returncode == 0


@pytest.mark.parametrize('symlink', [False, True])
def test_unrelated_command_is_never_overwritten(install_world, symlink):
    _source, _python, home, _env = install_world
    bin_dir = home/'.local/bin'
    bin_dir.mkdir(parents=True)
    unrelated = home/'unrelated'
    unrelated.write_text('leave me alone')
    launcher = bin_dir/'apprestore'
    if symlink:
        launcher.symlink_to(unrelated)
    else:
        launcher.write_text('leave me alone')
    result = run_install(install_world)
    assert result.returncode != 0 and 'Не перезаписываю' in result.stderr
    assert unrelated.read_text() == 'leave me alone'
    assert launcher.is_symlink() if symlink else launcher.read_text() == 'leave me alone'
    assert not list((home/'data/AppRestore/runtimes').glob('linux.*'))


def test_partial_publication_rolls_back_cli_without_removing_foreign_gui(install_world):
    _source, _python, home, _env = install_world
    bin_dir = home/'.local/bin'
    bin_dir.mkdir(parents=True)
    before = '#!/bin/bash\n'+MARKER+'\necho old-runtime\n'
    (bin_dir/'apprestore').write_text(before)
    result = run_install(install_world, '--gui', COLLIDE_GUI='1')
    assert result.returncode != 0
    assert (bin_dir/'apprestore').read_text() == before
    assert (bin_dir/'apprestore-gui').read_text() == 'unrelated command'
    assert not list((home/'data/AppRestore/runtimes').glob('linux.*'))


def test_update_publishes_new_generation_without_moving_old_venv(install_world):
    assert run_install(install_world).returncode == 0
    _source, _python, home, _env = install_world
    previous = (home/'.local/bin/apprestore').read_text()
    assert run_install(install_world).returncode == 0
    assert (home/'.local/bin/apprestore').read_text() != previous
    generations = list((home/'data/AppRestore/runtimes').glob('linux.*'))
    assert len(generations) == 2
    assert all((p/'venv/bin/python').is_file() for p in generations)
