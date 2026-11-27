"""Exercise the AutoDL launcher without installing software or contacting a provider."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

pytest.importorskip("fcntl")
from commute_digest.delivery_lock import delivery_in_progress


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    source_path = Path(__file__).resolve().parents[1] / "deploy/一键启动.py"
    spec = importlib.util.spec_from_file_location("cloud_launch", source_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.ENV = tmp_path / ".env"
    module.STATE = tmp_path / ".autodl"
    module.STATE.mkdir()
    module.PYTHON = tmp_path / ".venv/bin/python"
    module.PYTHON.parent.mkdir(parents=True)
    module.PYTHON.touch()
    (tmp_path / ".streamlit").mkdir()
    for name in ("uv.lock", "pyproject.toml", ".streamlit/config.toml"):
        (tmp_path / name).touch()
    for name in module.CONFIG_KEYS:
        monkeypatch.delenv(name, raising=False)
    env = {"DEEPSEEK_API_KEY": "fake-deepseek", "PUBLIC_BASE_URL": "https://paper.example",
           "SMTP_HOST": "smtp.example", "SMTP_PORT": "465", "SMTP_USER": "send@example.com",
           "SMTP_PASSWORD": "a test value 'quoted'", "RECIPIENT_EMAIL": "reader@example.com",
           "NGROK_AUTHTOKEN": "fake-token"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return module


def test_configuration_is_noninteractive_reusable_and_preserves_original_files(cloud, monkeypatch):
    env = cloud.configure()
    assert cloud.ENV.stat().st_mode & 0o777 == 0o600
    original = cloud.ENV.read_bytes()
    monkeypatch.delenv("SMTP_PASSWORD")
    assert cloud.configure()["SMTP_PASSWORD"] == env["SMTP_PASSWORD"]
    assert cloud.ENV.read_bytes() == original
    old = b"# existing cloud settings\nSMTP_PORT=465\n\n"
    cloud.ENV.write_bytes(old)
    (cloud.STATE / "ngrok.yml").write_text("version: 3\n")
    monkeypatch.delenv("NGROK_AUTHTOKEN")
    monkeypatch.setenv("SMTP_PASSWORD", "existing")
    assert cloud.configure()["SMTP_PASSWORD"] == "existing"
    assert cloud.ENV.read_bytes() == old
    assert (cloud.STATE / "ngrok.yml").read_text() == "version: 3\n"
    cmd = cloud.commands(env)["ngrok"]
    assert "--traffic-policy-file" not in cmd and "--config" in cmd


def test_first_setup_records_gmail_sender_and_defaults_recipient_to_it(cloud, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "sender@example.org")
    monkeypatch.delenv("RECIPIENT_EMAIL")
    env = cloud.configure()
    assert env["SMTP_FROM"] == env["RECIPIENT_EMAIL"] == "sender@example.org"
    written = cloud.ENV.read_text()
    assert "SMTP_FROM=sender@example.org\n" in written
    assert "RECIPIENT_EMAIL=sender@example.org\n" in written


def test_service_login_prompts_for_authorized_sender_and_keeps_it_on_restart(cloud, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "smtp-service-account")
    monkeypatch.delenv("RECIPIENT_EMAIL")
    with pytest.raises(ValueError, match="SMTP_FROM"):
        cloud.configure()
    assert not cloud.ENV.exists()
    monkeypatch.setattr(cloud.sys.stdin, "isatty", lambda: True)
    questions = []
    replies = iter(("digest@example.org", ""))
    def respond(prompt):
        questions.append(prompt)
        return next(replies)
    monkeypatch.setattr("builtins.input", respond)
    env = cloud.configure()
    assert "发件邮箱" in questions[0] and "[digest@example.org]" in questions[1]
    assert env["SMTP_FROM"] == env["RECIPIENT_EMAIL"] == "digest@example.org"
    original = cloud.ENV.read_bytes()
    assert cloud.configure()["SMTP_USER"] == "smtp-service-account"
    assert cloud.ENV.read_bytes() == original


@pytest.mark.parametrize("key", ["SMTP_FROM", "RECIPIENT_EMAIL"])
def test_reject_invalid_mail_addresses_before_writing_configuration(cloud, monkeypatch, key):
    monkeypatch.setenv(key, "smtp-login")
    with pytest.raises(ValueError, match=key):
        cloud.configure()
    assert not cloud.ENV.exists()


def test_start_is_idempotent_and_repairs_individual_services(cloud, monkeypatch):
    state = {}
    launches, stops, installs = [], [], []
    health = {"local": False, "public": False}
    monkeypatch.setattr(cloud, "sessions", lambda: state.copy())
    monkeypatch.setattr(cloud, "healthy", lambda url: health["local" if url.startswith("http:") else "public"])
    monkeypatch.setattr(cloud, "install_tools", lambda: installs.append(True))
    monkeypatch.setattr(cloud, "executable", lambda name: "/fake/" + name)

    def launch(key, command, env):
        launches.append(key)
        state[key] = ["123." + key]
        if key == "preview":
            health["local"] = True
        if key == "ngrok":
            health["public"] = True
        assert env["COMMUTE_DELIVERY_LOCK"] == str(cloud.STATE / "delivery.lock")

    def stop(active):
        stops.extend(active)
        for key in active:
            del state[key]

    monkeypatch.setattr(cloud, "launch", launch)
    monkeypatch.setattr(cloud, "stop_services", stop)
    cloud.start()
    assert launches == list(cloud.SERVICES) and len(installs) == 1
    launches.clear()
    cloud.start()
    assert launches == [] and stops == [] and len(installs) == 1
    del state["accounts"]
    health["public"] = False
    cloud.start()
    assert stops == ["ngrok"] and launches == ["ngrok", "accounts"]
    stops.clear()
    launches.clear()
    health["local"] = False
    cloud.start()
    assert stops == ["preview"] and launches == ["preview"]
    assert len(installs) == 1


def test_live_mail_lock_blocks_restart_and_never_modifies_the_database(cloud, monkeypatch):
    env = cloud.configure()
    monkeypatch.setenv("COMMUTE_DELIVERY_LOCK", env["COMMUTE_DELIVERY_LOCK"])
    with delivery_in_progress():
        with pytest.raises(ValueError, match="仍在运行"):
            with cloud.drain_workers(env, {"accounts": ["43.commute-digest-accounts"]}):
                pytest.fail("attempted to stop the worker")
    with cloud.drain_workers(env, {"accounts": ["43.commute-digest-accounts"]}):
        pass
    assert not (cloud.ROOT / "data" / "morning.sqlite3").exists()


def test_inactive_historical_ledger_rows_do_not_block_a_legacy_upgrade(cloud, monkeypatch):
    db_path = cloud.ROOT / "real.sqlite3"
    env = {**cloud.configure(), "COMMUTE_DB": str(db_path)}
    monkeypatch.setattr(cloud, "worker_processes", lambda _: None)
    old = "2020-01-01T17:00:00+08:00"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE deliveries(day TEXT, status TEXT)")
        db.execute("CREATE TABLE subscription_deliveries(claimed_at TEXT,status TEXT)")
        db.execute("INSERT INTO deliveries VALUES (?, 'building')", (old,))
        db.execute("INSERT INTO subscription_deliveries VALUES (?, 'sending')", (old,))
    active = {"daily": ["42.commute-digest-daily"], "accounts": ["43.commute-digest-accounts"]}
    with cloud.drain_workers(env, active):
        pass
    recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute("INSERT INTO subscription_deliveries VALUES (?, 'building')", (recent,))
    with pytest.raises(ValueError, match="旧版投递"):
        with cloud.drain_workers(env, active):
            pytest.fail("no live worker should be stopped")
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT * FROM subscription_deliveries ORDER BY claimed_at").fetchall() == [
            (old, "sending"), (recent, "building")]
    # A newly launched worker cannot own the previous process's unfinished claim.
    monkeypatch.setattr(cloud, "worker_processes",
                        lambda _: [([b"python"], [], datetime.now(timezone.utc).timestamp())])
    with cloud.drain_workers(env, active):
        pass


def test_status_checks_local_and_public_routes_separately(cloud, monkeypatch, capsys):
    monkeypatch.setattr(cloud, "sessions", lambda: {"preview": ["43.commute-digest-preview"]})
    monkeypatch.setattr(cloud, "healthy", lambda url: url.startswith("http:"))
    cloud.status()
    output = capsys.readouterr().out
    assert "本机网页　可访问" in output and "公网 HTTPS　无法访问" in output
    assert "账号订阅　已停止" in output


def test_screen_discovery_ignores_stale_and_unrelated_sessions(cloud, monkeypatch):
    listing = """There are screens on:
        142.commute-digest-preview (Detached)
        143.commute-digest-ngrok (Dead ???)
        155.commute-digest-daily (Detached)
        166.other-project-accounts (Detached)
        187.commute-digest-accounts (Attached)
        207.commute-digest-unrelated (Detached)
    """
    response = cloud.subprocess.CompletedProcess(["screen", "-ls"], 0, listing, "")
    monkeypatch.setattr(cloud.subprocess, "run", lambda *args, **kwargs: response)
    assert cloud.sessions() == {"preview": ["142.commute-digest-preview"],
                                "daily": ["155.commute-digest-daily"],
                                "accounts": ["187.commute-digest-accounts"]}


def test_both_mail_entrypoints_hold_the_live_job_lock(cloud, monkeypatch):
    from commute_digest import edition_service, subscription_delivery
    from commute_digest.schedule import BEIJING

    class Verified(Exception):
        pass

    env = cloud.configure()
    monkeypatch.setenv("COMMUTE_DELIVERY_LOCK", env["COMMUTE_DELIVERY_LOCK"])
    def observe(*args):
        with pytest.raises(ValueError, match="仍在运行"):
            with cloud.drain_workers(env, {"daily": ["43.commute-digest-daily"]}):
                pytest.fail("The mailer must hold its lock before claiming a delivery")
        raise Verified

    monkeypatch.setattr(edition_service, "_mail_settings", observe)
    with pytest.raises(Verified):
        edition_service.deliver_slot()
    monkeypatch.setattr(subscription_delivery, "_claim", observe)
    now = datetime.now(BEIJING)
    with pytest.raises(Verified):
        subscription_delivery._deliver(None, now, now)


def test_upgrade_waits_for_current_mail_before_restarting(cloud, monkeypatch):
    from contextlib import contextmanager

    active = {key: ["1."+key] for key in cloud.SERVICES}
    events = []
    def stop(services):
        events.append("stop")
        for key in services:
            del active[key]

    @contextmanager
    def drain(env, workers):
        assert set(workers) == cloud.WORKERS
        if not events:
            events.append("busy")
            raise cloud.DeliveryBusy("仍在投递。")
        events.append("ready")
        yield
    monkeypatch.setattr(cloud.time, "sleep", lambda _: events.append("wait"))
    monkeypatch.setattr(cloud, "drain_workers", drain)
    monkeypatch.setattr(cloud, "sessions", lambda: active.copy())
    monkeypatch.setattr(cloud, "stop_services", stop)
    monkeypatch.setattr(cloud, "install_tools", lambda: events.append("sync"))
    monkeypatch.setattr(cloud, "healthy", lambda url: True)
    monkeypatch.setattr(cloud, "launch", lambda key, command, env: active.update({key: ["2."+key]}))
    cloud.start(force=True)
    assert events[:5] == ["busy", "wait", "ready", "sync", "stop"]


def test_first_boot_with_public_outage_recovers_without_restarting_mail_workers(cloud, monkeypatch):
    available, listening = False, False
    active, launches, stops, installs = {}, [], [], []
    monkeypatch.setattr(cloud.time, "sleep", lambda _: None)
    monkeypatch.setattr(cloud, "sessions", lambda: active.copy())
    monkeypatch.setattr(cloud, "healthy", lambda url: url.startswith("http:") or listening)
    monkeypatch.setattr(cloud, "install_tools", lambda: installs.append(True))
    monkeypatch.setattr(cloud, "executable", lambda name: "/fake/" + name)

    def launch(key, command, env):
        nonlocal listening
        active[key] = ["123." + key]
        launches.append(key)
        if key == "ngrok":
            listening = available

    def stop(services):
        stops.extend(services)
        for key in services:
            del active[key]

    monkeypatch.setattr(cloud, "launch", launch)
    monkeypatch.setattr(cloud, "stop_services", stop)
    with pytest.raises(ValueError, match="尚未全部就绪"):
        cloud.start()
    assert (cloud.STATE / "running-release").read_text() == cloud.signature(cloud.configure())
    assert set(active) == set(cloud.SERVICES)
    assert all(stopped == "ngrok" for stopped in stops)

    available = True
    launches.clear()
    stops.clear()
    cloud.start()
    assert stops == ["ngrok"] and launches == ["ngrok"] and len(installs) == 1
