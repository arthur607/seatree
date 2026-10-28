"""The OpenCode JSON the integrator reads. No network."""
import unittest

from seahorse.llm.opencode import events_from


class TestEvents(unittest.TestCase):
    def test_text_and_session(self):
        raw = '\n'.join([
            '{"type":"step_start","sessionID":"ses_1","part":{"type":"step-start"}}',
            '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"OK"}}',
        ])
        self.assertEqual(events_from(raw), ("OK", "ses_1", None))

    def test_error_message(self):
        raw = '{"type":"error","sessionID":"ses_2","error":{"type":"provider.no-route","message":"Model unavailable: xai/not-a-model"}}'
        self.assertEqual(events_from(raw), ("", "ses_2", "Model unavailable: xai/not-a-model"))


if __name__ == "__main__":
    unittest.main()
