"""2FA in the 4b sign-in sheet, end to end on a fake ipatool (no network, no Apple ID).

Макс, путь A (ipatool-auth-pty-pathA.HANDOFF-for-Dima.md): ONE ``ipatool auth login``
process on a terminal, without ``--non-interactive`` and without ``-e``/``-p``; the
Apple ID, the password and the 2FA code are typed at ipatool's own prompts
(``enter Apple ID (email address or phone number): ``, ``enter password: ``,
``enter 2FA code: ``). A stale code → ``apple did not complete verification; try a
fresh 2FA code`` (ErrorCode.TWO_FACTOR_REJECTED, 5005) → auth-code-wrong: the password
form with Ника's text; a new ``auth login`` only on the user's «Войти».

Лена's conditions: a) the code and the password reach no log or file (the terminal
output is scrubbed to the end of the line after the password / code prompts);
b) the Apple ID is not in argv; c) the code is not kept after submit / after ipatool
answers; d) exactly one ``auth login`` per «Войти», zero automatic retries.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake ipatool is a POSIX script")

PASSWORD = "Apple-Pass-31415"
GOOD = "123456"
OLD = "654321"
EMAIL = "person@example.com"


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Max's verbatim lines; the script holds only hashes, so no file on disk has the secrets.
FAKE = """#!{python}
import hashlib, json, os, sys, termios
open({record!r}, "a").write(json.dumps({{"argv": sys.argv, "env": dict(os.environ)}}) + "\\n")
h = lambda s: hashlib.sha256(s.encode()).hexdigest()
args = sys.argv[1:]
if "info" in args:
    print('{{"success":false}}'); sys.exit(1)
if "--non-interactive" in args:
    print("auth code is required", file=sys.stderr); sys.exit(1)
def ask(prompt, masked):
    sys.stdout.write(prompt); sys.stdout.flush()
    old = termios.tcgetattr(0)
    if masked:
        new = termios.tcgetattr(0); new[3] &= ~termios.ECHO
        termios.tcsetattr(0, termios.TCSANOW, new)
    try:
        return sys.stdin.readline().strip()
    finally:
        termios.tcsetattr(0, termios.TCSANOW, old)
        if masked:
            sys.stdout.write("\\n"); sys.stdout.flush()
email = ask("enter Apple ID (email address or phone number): ", False)
password = ask("enter password: ", True)
if h(email) != {email_h!r} or h(password) != {password_h!r}:
    print("failed to login: invalid password", file=sys.stderr); sys.exit(1)
code = ask("enter 2FA code: ", False)
if h(code) == {good_h!r}:
    print('{{"level":"info","success":true}}'); sys.exit(0)
print("apple did not complete verification; try a fresh 2FA code", file=sys.stderr)
sys.exit(1)
"""


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from apprestore_core.command import SECRET_ENV_NAMES
    from apprestore_gui import auth_pty

    record = tmp_path / "spawned.jsonl"
    tool = tmp_path / "ipatool"
    tool.write_text(
        FAKE.format(python=sys.executable, record=str(record), email_h=_h(EMAIL),
                    password_h=_h(PASSWORD), good_h=_h(GOOD)),
        encoding="utf-8",
    )
    tool.chmod(0o755)
    for name in SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(auth_pty, "_which_ipatool", lambda: str(tool))
    monkeypatch.setattr(auth_pty, "_apple_login_host_reachable", lambda env, timeout=8: True)

    def spawned() -> list[dict]:
        if not record.exists():
            return []
        return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]

    return SimpleNamespace(tool=tool, spawned=spawned)


def _logins(rows: list[dict]) -> list[list[str]]:
    return [r["argv"] for r in rows if "login" in r["argv"]]


def _no_secrets(rows: list[dict], *secrets: str) -> None:
    from apprestore_core.command import SECRET_ENV_NAMES

    assert rows
    for row in rows:
        blob = json.dumps([row["argv"], row["env"]])
        for secret in secrets:
            assert secret not in blob
        assert not SECRET_ENV_NAMES & {k.upper() for k in row["env"]}
        if "login" in row["argv"]:
            assert "--non-interactive" not in row["argv"]
            assert "--password" not in row["argv"] and "--auth-code" not in row["argv"]


def _wait(qapp, cond, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def test_login_argv_has_no_email_password_or_non_interactive() -> None:
    from apprestore_gui.auth_pty import login_argv

    assert login_argv("/opt/ipatool") == ["/opt/ipatool", "auth", "login"]


def test_prompts_are_maxs_verbatim() -> None:
    from apprestore_gui import auth_pty

    assert auth_pty.PROMPT_APPLE_ID == "enter Apple ID (email address or phone number): "
    assert auth_pty.PROMPT_PASSWORD == "enter password: "
    assert auth_pty.PROMPT_CODE == "enter 2FA code: "


def test_scrubber_drops_echo_to_end_of_line_across_chunks() -> None:
    from apprestore_gui.auth_pty import PromptScrubber

    sc = PromptScrubber()
    chunks = ["enter pass", "word: ", "Apple-Pa", "ss-31415\n", "enter 2FA co", "de: 12", "3456", "\napple did not",
              " complete verification; try a fresh 2FA code\n"]
    out = "".join(sc.feed(c) for c in chunks)
    assert PASSWORD not in out and GOOD not in out and "123" not in out
    assert out == ("enter password: \nenter 2FA code: \n"
                   "apple did not complete verification; try a fresh 2FA code\n")


@pytest.mark.parametrize(
    "text",
    [
        "apple did not complete verification; try a fresh 2FA code",
        "apple did not complete verification",
        "enter 2FA code: \nError: apple did not complete verification (5005)",
    ],
)
def test_did_not_complete_verification_is_two_factor_rejected(text: str) -> None:
    from apprestore_core.ipatool_api import ErrorCode, classify_error
    from apprestore_gui.auth_pty import WRONG_CODE_TEXT, _explain_login_failure, is_two_factor_rejected

    assert classify_error(text) is ErrorCode.TWO_FACTOR_REJECTED
    assert is_two_factor_rejected(text)
    # auth-code-wrong, not «session expired», not a network problem
    assert _explain_login_failure(1, text, code_sent=True) == WRONG_CODE_TEXT
    assert _explain_login_failure(1, text + "\ni/o timeout", code_sent=False) == WRONG_CODE_TEXT


def test_auth_code_required_is_not_relogin() -> None:
    from apprestore_core.ipatool_api import ErrorCode, classify_error

    assert classify_error("auth code is required") is ErrorCode.AUTH_CODE_REQUIRED


def _wait(qapp, cond, timeout: float = 20.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError("timed out")


def _scan_for_secrets(root: Path, *secrets: str) -> None:
    """Лена a): nothing written anywhere under the test home holds a secret."""

    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            for secret in secrets:
                assert secret.encode() not in data, path


@pytest.mark.parametrize(("code", "ok"), [pytest.param(GOOD, True, id="good"), pytest.param(OLD, False, id="old")])
def test_apple_login_thread_one_process_code_into_same_terminal(qapp, fake, code, ok, tmp_path) -> None:
    from apprestore_gui.auth_pty import WRONG_CODE_TEXT, AppleLogin

    job = AppleLogin(EMAIL, PASSWORD, "", "")
    asked: list[str] = []
    done: list[object] = []
    output: list[str] = []
    job.output.connect(output.append)
    job.need_input.connect(lambda kind: (asked.append(kind), job.submit("code", code)))
    job.done.connect(done.append)
    job.start()
    _wait(qapp, lambda: bool(done))
    job.wait(5000)
    assert asked == ["code"]
    assert done[0].ok is ok
    if not ok:
        assert done[0].message == WRONG_CODE_TEXT
    time.sleep(0.5)  # nothing starts again by itself
    rows = fake.spawned()
    assert len(_logins(rows)) == 1
    _no_secrets(rows, EMAIL, PASSWORD, code)
    transcript = "".join(output)
    assert "enter 2FA code: " in transcript and "enter password: " in transcript
    for secret in (EMAIL, PASSWORD, code):
        assert secret not in transcript
    # c) nothing of the answers stays in the job
    assert job._password == "" and job._code == "" and job._inbox.empty()
    assert code not in repr(vars(job)) and PASSWORD not in repr(vars(job))


def _session(qapp, monkeypatch, tmp_path: Path):
    from apprestore_gui import quick_session as qs

    home = tmp_path / "home"
    home.mkdir()
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    class Service:
        def __init__(self) -> None:
            self.core = SimpleNamespace(tools=SimpleNamespace(_ipatool_env=lambda: {}))

        def keychain_passphrase(self) -> str:
            return ""

        def remember_keychain_passphrase(self, *_a, **_k) -> None:
            pass

        def clear_keychain_passphrase(self) -> None:
            pass

    monkeypatch.setattr(qs, "GuiService", lambda demo_mode=False: Service())
    monkeypatch.setattr(qs, "load_bindings", lambda: {})
    monkeypatch.setattr(qs, "keychain_has_saved_account", lambda: True)  # wrong code → still «out»
    monkeypatch.setattr(qs, "remember_binding", lambda *_a: None)
    s = qs.QuickSession()
    s._refresh_auth = lambda: None  # the follow-up auth-info probe is not under test here
    return s


def test_4b_session_need_code_then_signed_in_one_spawn_one_code(qapp, fake, monkeypatch, tmp_path, caplog, capfd) -> None:
    from apprestore_gui.ui4b.qt_bridge import signin_view

    caplog.set_level(logging.DEBUG)
    s = _session(qapp, monkeypatch, tmp_path)
    s.login(EMAIL, PASSWORD)
    s.login(EMAIL, PASSWORD)  # a double click while running starts nothing more
    _wait(qapp, lambda: s.authPhase == "need_code")
    v = signin_view(open_=True, phase=s.authPhase, status=s._auth_status, email=EMAIL, relogin=False)
    assert v["code"] and v["go"] == "Подтвердить" and not v["busy"]
    assert v["sub"] == "Apple отправила код на ваши устройства: iPhone, iPad или Mac. Введите 6 цифр."
    job = s._auth_job
    sent: list[tuple[str, str]] = []
    real = job.submit
    job.submit = lambda kind, value: (sent.append((kind, "x" * len(value))), real(kind, value))
    s.submitCode("12345")  # short: not sent
    assert sent == [] and s.authPhase == "need_code"
    s.submitCode(GOOD)
    v = signin_view(open_=True, phase=s.authPhase, status=s._auth_status, email=EMAIL, relogin=False)
    assert v["code"] and v["busy"] and v["go"] == "Проверяем…"
    s.submitCode(GOOD)  # Enter again while checking: ignored
    _wait(qapp, lambda: s._auth_job is None)
    assert sent == [("code", "xxxxxx")]
    assert s.signedIn and s.authPhase == "in"
    rows = fake.spawned()
    assert len(_logins(rows)) == 1
    _no_secrets(rows, EMAIL, PASSWORD, GOOD)
    assert GOOD not in repr(vars(s)) and PASSWORD not in repr(vars(s))
    logs = caplog.text + "".join(capfd.readouterr())
    assert GOOD not in logs and PASSWORD not in logs
    _scan_for_secrets(tmp_path, PASSWORD, GOOD)


def test_4b_old_code_back_to_password_form_no_relaunch(qapp, fake, monkeypatch, tmp_path, caplog, capfd) -> None:
    from apprestore_gui.auth_pty import CODE_HINT, WRONG_CODE_TEXT
    from apprestore_gui.ui4b.qt_bridge import signin_view

    caplog.set_level(logging.DEBUG)
    s = _session(qapp, monkeypatch, tmp_path)
    s.login(EMAIL, PASSWORD)
    _wait(qapp, lambda: s.authPhase == "need_code")
    s.submitCode(OLD)
    _wait(qapp, lambda: s._auth_job is None)
    assert not s.signedIn
    assert s.authPhase == "out"  # the password form, not the keychain one, not relogin
    assert not s.sessionRelogin
    view = signin_view(open_=True, phase=s.authPhase, status=s._auth_status, email=EMAIL, relogin=False)
    assert view["error"] == WRONG_CODE_TEXT and not view["code"] and view["go"] == "Войти"
    assert view["hint"] == CODE_HINT
    for _ in range(25):  # 0.5 s: no automatic second «auth login»
        qapp.processEvents()
        time.sleep(0.02)
    assert len(_logins(fake.spawned())) == 1
    assert s._auth_job is None

    # the next attempt is the user's own «Войти»: one more process, a new prompt
    s.login(EMAIL, PASSWORD)
    _wait(qapp, lambda: s.authPhase == "need_code")
    s.submitCode(GOOD)
    _wait(qapp, lambda: s._auth_job is None)
    assert s.signedIn
    rows = fake.spawned()
    assert len(_logins(rows)) == 2
    _no_secrets(rows, EMAIL, PASSWORD, OLD, GOOD)
    logs = caplog.text + "".join(capfd.readouterr())
    for secret in (PASSWORD, OLD, GOOD):
        assert secret not in logs
        assert secret not in repr(vars(s))
    _scan_for_secrets(tmp_path, PASSWORD, OLD, GOOD)


def test_code_texts_are_nikas() -> None:
    from apprestore_gui.auth_pty import CODE_HINT, WRONG_CODE_TEXT
    from apprestore_gui.ui4b.qt_bridge import signin_view

    v = signin_view(open_=True, phase="need_code", status="", email=EMAIL, relogin=False)
    assert CODE_HINT == "Код не пришёл? На iPhone: Настройки → ваше имя → Вход и безопасность → Получить код проверки"
    assert v["hint"] == CODE_HINT and v["codePlaceholder"] == "Код из 6 цифр"
    assert WRONG_CODE_TEXT == ("Код не подошёл или устарел, и Apple завершила вход. "
                               "Введите пароль ещё раз и возьмите самый свежий код.")
    root = Path(__file__).resolve().parents[1] / "apprestore_gui"
    for path in [*(root / "ui4b").rglob("*.py"), *(root / "qml4b").rglob("*.qml"), root / "auth_pty.py"]:
        text = path.read_text(encoding="utf-8")
        assert "Отмена» и войдите ещё раз" not in text, path.name
        assert "Отправить снова" not in text, path.name


def test_no_login_relaunch_paths_in_source() -> None:
    """Only the «Войти» slot starts ``auth login`` with a password; the other AppleLogin
    jobs are keychain unlocks (``auth info``) with an empty email and password."""

    import re

    from apprestore_gui import quick_session

    src = Path(quick_session.__file__).read_text(encoding="utf-8")
    starts = re.findall(r"AppleLogin\((.{0,20})", src)
    assert starts
    with_password = [x for x in starts if not x.startswith('"", ""')]
    assert len(with_password) == 1 and with_password[0].startswith("email, password")
    root = Path(quick_session.__file__).parent
    for path in [root / "auth_pty.py", root / "quick_session.py", *(root / "ui4b").rglob("*.py")]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"login"' in line and "auth" in line:
                assert "--non-interactive" not in line and "--email" not in line, (path.name, line)


def test_sign_in_sheet_code_rules_in_qml() -> None:
    """No auto-submit on the 6th digit; submit only with six digits; the field is
    cleared after submit, on a new prompt and on any exit; locked while busy."""

    from apprestore_gui import quick_session

    qml = (Path(quick_session.__file__).parent / "qml4b" / "components" / "SignInSheet.qml").read_text(encoding="utf-8")
    assert "onTextEdited" not in qml
    assert "onCodePhaseChanged: root.forgetCode()" in qml
    assert "onVisibleChanged: if (!visible) { root.forgetCode()" in qml
    assert "Component.onDestruction: root.forgetCode()" in qml
    assert "onCancel: { root.forgetCode()" in qml
    body = qml[qml.index("function submitCode()"):]
    body = body[: body.index("}\n")]
    assert body.index("root.forgetCode()") < body.index("ui.submitCode(code)")
    assert r"/^\d{6}$/.test(code)" in body
    assert qml.count("readOnly: !!root.s.busy") >= 3
    assert 'Qt.platform.os === "windows"' in qml and "signInCodeField" in qml
