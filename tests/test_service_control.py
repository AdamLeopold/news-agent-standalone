from unittest.mock import patch
import pytest

from ttf_news import cli


def test_start_does_not_accept_untracked_port_listener(tmp_path):
    with patch.object(cli,'RUNTIME',tmp_path), patch.object(cli,'health_url',return_value={'server_pid':1}), patch.object(cli.subprocess,'Popen') as spawn:
        with pytest.raises(RuntimeError,match='without a matching managed PID'):
            cli.start()
        spawn.assert_not_called()


def test_start_does_not_confuse_old_listener_with_managed_pid(tmp_path):
    (tmp_path/'app.pid').write_text('123')
    with patch.object(cli,'RUNTIME',tmp_path), patch.object(cli,'alive',return_value=True), patch.object(cli,'health_url',return_value={'server_pid':456}), patch.object(cli.subprocess,'Popen') as spawn:
        with pytest.raises(RuntimeError,match='expected health endpoint'):
            cli.start()
        spawn.assert_not_called()


def test_shutdown_preserves_pid_when_identity_is_wrong(tmp_path):
    from types import SimpleNamespace
    (tmp_path/'app.pid').write_text('123')
    with patch.object(cli,'RUNTIME',tmp_path), patch.object(cli,'alive',return_value=True), patch.object(cli.subprocess,'run',return_value=SimpleNamespace(stdout='unrelated service')), patch.object(cli.os,'kill') as kill:
        with pytest.raises(RuntimeError,match='not the managed'):
            cli.stop()
        kill.assert_not_called()
        assert (tmp_path/'app.pid').exists()
