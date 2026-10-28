"""The OpenCode JSON the integrator reads, and the environment the CLI runs in. No network."""
import json
import os
import subprocess
import unittest
from unittest import mock

from seahorse.llm import opencode


class TestEvents(unittest.TestCase):
    def test_text_and_session(self):
        raw = '\n'.join([
            '{"type":"step_start","sessionID":"ses_1","part":{"type":"step-start"}}',
            '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"OK"}}',
        ])
        self.assertEqual(opencode.events_from(raw), ("OK", "ses_1", None))

    def test_error_message(self):
        raw = '{"type":"error","sessionID":"ses_2","error":{"type":"provider.no-route","message":"Model unavailable: xai/not-a-model"}}'
        self.assertEqual(opencode.events_from(raw), ("", "ses_2", "Model unavailable: xai/not-a-model"))


class TestRun(unittest.TestCase):
    def test_no_key_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            answer = opencode.run("hi", "fast", "low", None, False, False, None, 1, 10)
        self.assertTrue(answer.error)
        self.assertIn("XAI_API_KEY", answer.text)

    def test_the_desktop_login_is_not_used(self):
        """A stored login in ~/.local/share/opencode answers when XAI_API_KEY is wrong. Its own data dir stops that."""
        done = subprocess.CompletedProcess([], 0, json.dumps(
            {"type": "text", "sessionID": "ses_1", "part": {"type": "text", "text": "OK"}}), "")
        with mock.patch.dict(os.environ, {"XAI_API_KEY": "sk-test"}), \
                mock.patch.object(opencode, "prepare"), \
                mock.patch.object(opencode, "workdir", return_value="/tmp"), \
                mock.patch.object(opencode.subprocess, "run", return_value=done) as run:
            answer = opencode.run("hi", "fast", "low", None, False, False, None, 1, 10)
        self.assertFalse(answer.error)
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["XDG_DATA_HOME"], str(opencode.data_home()))
        self.assertTrue(opencode.data_home().is_relative_to(opencode.home()))
        self.assertEqual(run.call_args.kwargs["cwd"], "/tmp")
        self.assertEqual(env["PWD"], "/tmp")  # OpenCode takes the project from PWD, not from the process cwd


if __name__ == "__main__":
    unittest.main()
