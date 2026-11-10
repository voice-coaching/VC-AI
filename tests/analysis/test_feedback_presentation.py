"""Cosmetic provider variation must not erase otherwise valid coaching."""
from copy import deepcopy
import unittest

from voice_coach.six_dimension.pipeline import public_feedback


class FeedbackPresentation(unittest.TestCase):
    def render(self, practice):
        rows = [dict(practice=practice)]
        before = deepcopy(rows)
        result = public_feedback(rows)
        self.assertEqual(rows, before)
        return result

    def test_plain_instruction_survives_missing_separator(self):
        text = '마지막 단어까지 같은 목소리로 이어 읽으세요.'
        self.assertEqual(self.render(text), '* ' + text)

    def test_bullet_and_arrow_spacing_only_are_normalized(self):
        expected = '* 문장 전체 → 마지막까지 고르게 읽으세요.'
        for text in ('문장 전체→마지막까지 고르게 읽으세요.',
                     ' * 문장 전체 -> 마지막까지 고르게 읽으세요. ',
                     '• 문장 전체 → 마지막까지 고르게 읽으세요.'):
            with self.subTest(text=text):
                self.assertEqual(self.render(text), expected)

    def test_original_canonical_text_is_unchanged(self):
        text = '문장 전체 → 마지막까지 고르게 읽으세요.'
        self.assertEqual(self.render(text), '* ' + text)

    def test_quoted_script_endings_do_not_become_extra_instructions(self):
        for opening, closing in (('‘', '’'), ('“', '”'), ('"', '"'), ("'", "'"), ('「', '」')):
            text = f'문장 끝 → {opening}끝났습니다{closing} 뒤를 부드럽게 이어 읽으세요.'
            with self.subTest(opening=opening):
                self.assertEqual(self.render(text), '* ' + text)

    def test_quoting_does_not_hide_real_multiple_instructions(self):
        for text in ('문장 → 먼저 읽으세요 ‘끝났습니다’ 뒤에 쉬세요.',
                     '문장 → ‘끝났습니다’ 뒤를 읽으세요 다시 해요',
                     '문장 → ‘끝났습니다’ 뒤를 읽으세요. 다시 해요.',
                     '문장 → ‘끝났습니다 뒤를 읽으세요.'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.render(text)

    def test_relaxed_typography_keeps_content_and_length_guards(self):
        for text in ('', '  ', '* ', '대상 → ', ' → 연습하세요.',
                     '대상 → 하나 → 둘', '문장\n읽으세요.',
                     '문장 → 첫째를 읽으세요. 둘째를 읽으세요.',
                     '문장 → 읽으세요 다시 해요',
                     '초당 6음절로 읽으세요.',
                     '문장 → 25dB로 읽으세요.', '가' * 99):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.render(text)

    def test_total_lines_remain_bounded(self):
        with self.assertRaisesRegex(ValueError, 'FEEDBACK_LINE_LIMIT'):
            public_feedback([dict(practice='고르게 읽으세요.')] * 4)

    def test_new_harnesses_keep_numerical_policy_and_classification(self):
        from voice_coach.backend_analysis.canonical_harness import (
            AIHUB_LIP, AIHUB_LIP_INTERNAL, FEEDBACK_PRESENTATION,
            FEEDBACK_PRESENTATION_INTERNAL, check_schema_registration, check_result_classification)
        for parent, candidate in ((AIHUB_LIP, FEEDBACK_PRESENTATION),
                                  (AIHUB_LIP_INTERNAL, FEEDBACK_PRESENTATION_INTERNAL)):
            self.assertEqual(parent.core_sha256, candidate.core_sha256)
            self.assertEqual(parent.prompt_sha256, candidate.prompt_sha256)
            self.assertEqual(parent.model, candidate.model)
            check_schema_registration(candidate)
            check_result_classification(candidate)
